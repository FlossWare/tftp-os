"""Tests for tftpos.iso_detect -- auto-detection of OS from ISO metadata."""

from __future__ import annotations

import struct
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tftpos.iso_detect import (
    _detect_debian_dists,
    _detect_debian_netinst,
    _detect_diskdefines,
    _detect_discinfo,
    _detect_freebsd,
    _detect_openbsd,
    _detect_treeinfo,
    _detect_volume_label,
    _parse_diskname,
    _parse_release_string,
    _parse_volume_id,
    detect_iso,
)
from tftpos.mnemonics import DistroAlias


# =====================================================================
# Helper: build a minimal ISO9660 image with a volume label
# =====================================================================

def _make_iso_bytes(volume_id: str) -> bytes:
    """Build the first 34816 bytes of an ISO with the given volume ID.

    The Primary Volume Descriptor (PVD) starts at byte 32768 (sector 16).
    """
    # 16 sectors of zeros (system area + padding)
    data = bytearray(32768)

    # PVD: type=1, id='CD001', version=1
    pvd = bytearray(2048)
    pvd[0] = 0x01
    pvd[1:6] = b"CD001"
    pvd[6] = 0x01

    # Volume identifier at bytes 40..72  (32 bytes, space-padded)
    vid = volume_id.encode("ascii")[:32].ljust(32)
    pvd[40:72] = vid

    data.extend(pvd)
    return bytes(data)


# =====================================================================
# _detect_treeinfo
# =====================================================================

class TestDetectTreeinfo:

    def test_fedora_treeinfo(self, tmp_path):
        """Detects Fedora from a .treeinfo file."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = Fedora
            version = 42
            arch = x86_64
            name = Fedora 42
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "fedora"
        assert result.version == "42"

    def test_rhel_treeinfo(self, tmp_path):
        """Detects RHEL from a .treeinfo file."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = Red Hat Enterprise Linux
            version = 9.3
            arch = x86_64
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "rhel"
        assert result.version == "9.3"

    def test_rocky_treeinfo(self, tmp_path):
        """Detects Rocky Linux from a .treeinfo file."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = Rocky Linux
            version = 9.3
            arch = x86_64
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "rocky"
        assert result.version == "9.3"

    def test_alma_treeinfo(self, tmp_path):
        """Detects AlmaLinux from a .treeinfo file."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = AlmaLinux
            version = 9.3
            arch = x86_64
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "alma"
        assert result.version == "9.3"

    def test_centos_stream_treeinfo(self, tmp_path):
        """Detects CentOS Stream from a .treeinfo file."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = CentOS Stream
            version = 9
            arch = x86_64
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "centos"
        assert result.version == "9"

    def test_treeinfo_without_dot(self, tmp_path):
        """Detects from 'treeinfo' (no leading dot)."""
        ti = tmp_path / "treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = Fedora
            version = 40
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.version == "40"

    def test_treeinfo_family_from_name(self, tmp_path):
        """Falls back to extracting family from [general] name."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            name = Fedora 41
            version = 41
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.version == "41"

    def test_treeinfo_unknown_family(self, tmp_path):
        """Unknown family names are used verbatim."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = MagicLinux
            version = 7
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is not None
        assert result.os_family == "magiclinux"
        assert result.vendor == "magiclinux"
        assert result.version == "7"

    def test_treeinfo_no_general_section(self, tmp_path):
        """Returns None when [general] section is missing."""
        ti = tmp_path / ".treeinfo"
        ti.write_text("[other]\nkey = value\n")
        result = _detect_treeinfo(tmp_path)

        assert result is None

    def test_treeinfo_missing_version(self, tmp_path):
        """Returns None when version is absent."""
        ti = tmp_path / ".treeinfo"
        ti.write_text(textwrap.dedent("""\
            [general]
            family = Fedora
        """))
        result = _detect_treeinfo(tmp_path)

        assert result is None

    def test_treeinfo_missing_file(self, tmp_path):
        """Returns None when neither .treeinfo nor treeinfo exists."""
        result = _detect_treeinfo(tmp_path)

        assert result is None

    def test_treeinfo_corrupt_file(self, tmp_path):
        """Returns None for a file that cannot be parsed as INI."""
        ti = tmp_path / ".treeinfo"
        ti.write_bytes(b"\xff\xfe" + b"\x00" * 100)
        result = _detect_treeinfo(tmp_path)

        assert result is None


# =====================================================================
# _detect_discinfo
# =====================================================================

class TestDetectDiscinfo:

    def test_rhel_discinfo(self, tmp_path):
        """Detects RHEL from .discinfo."""
        di = tmp_path / ".discinfo"
        di.write_text("1234567890.123456\n"
                      "Red Hat Enterprise Linux 8.5\n"
                      "x86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "rhel"
        assert result.version == "8.5"

    def test_centos_discinfo(self, tmp_path):
        """Detects CentOS from .discinfo."""
        di = tmp_path / ".discinfo"
        di.write_text("1234567890\nCentOS Linux 7.9\nx86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "centos"
        assert result.version == "7.9"

    def test_centos_stream_discinfo(self, tmp_path):
        """Detects CentOS Stream from .discinfo."""
        di = tmp_path / ".discinfo"
        di.write_text("1234567890\nCentOS Stream 9\nx86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "centos"
        assert result.version == "9"

    def test_rocky_discinfo(self, tmp_path):
        """Detects Rocky Linux from .discinfo."""
        di = tmp_path / ".discinfo"
        di.write_text("9876543210\nRocky Linux 9.3\nx86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "rocky"
        assert result.version == "9.3"

    def test_alma_discinfo(self, tmp_path):
        """Detects AlmaLinux from .discinfo."""
        di = tmp_path / ".discinfo"
        di.write_text("1111111111\nAlmaLinux 9.3\nx86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "alma"
        assert result.version == "9.3"

    def test_fedora_discinfo(self, tmp_path):
        """Detects Fedora from .discinfo."""
        di = tmp_path / ".discinfo"
        di.write_text("1111111111\nFedora 40\nx86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "fedora"
        assert result.version == "40"

    def test_discinfo_too_few_lines(self, tmp_path):
        """Returns None when .discinfo has fewer than 2 lines."""
        di = tmp_path / ".discinfo"
        di.write_text("1234567890\n")
        result = _detect_discinfo(tmp_path)

        assert result is None

    def test_discinfo_empty_release(self, tmp_path):
        """Returns None when the release line is blank."""
        di = tmp_path / ".discinfo"
        di.write_text("1234567890\n\nx86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is None

    def test_discinfo_unknown_distro(self, tmp_path):
        """Returns None for an unrecognised release string."""
        di = tmp_path / ".discinfo"
        di.write_text("1234567890\nMysteryOS 5.0\nx86_64\n")
        result = _detect_discinfo(tmp_path)

        assert result is None

    def test_discinfo_missing_file(self, tmp_path):
        """Returns None when .discinfo does not exist."""
        result = _detect_discinfo(tmp_path)

        assert result is None


# =====================================================================
# _parse_release_string
# =====================================================================

class TestParseReleaseString:

    @pytest.mark.parametrize(
        "release,os_family,vendor,version",
        [
            ("Red Hat Enterprise Linux 9.3", "fedora", "rhel", "9.3"),
            ("red hat enterprise linux 8", "fedora", "rhel", "8"),
            ("CentOS Stream 9", "fedora", "centos", "9"),
            ("CentOS Linux 7.9", "fedora", "centos", "7.9"),
            ("Rocky Linux 9.3", "fedora", "rocky", "9.3"),
            ("AlmaLinux 9.3", "fedora", "alma", "9.3"),
            ("Fedora 42", "fedora", "fedora", "42"),
        ],
    )
    def test_known_patterns(self, release, os_family, vendor, version):
        result = _parse_release_string(release)

        assert result is not None
        assert result.os_family == os_family
        assert result.vendor == vendor
        assert result.version == version

    def test_unknown_string(self):
        assert _parse_release_string("ArchLinux 2024") is None

    def test_empty_string(self):
        assert _parse_release_string("") is None


# =====================================================================
# _detect_diskdefines  (Ubuntu)
# =====================================================================

class TestDetectDiskdefines:

    def test_ubuntu_full(self, tmp_path):
        """Detects Ubuntu from DISTRIB_ID + DISTRIB_RELEASE."""
        dd = tmp_path / "README.diskdefines"
        dd.write_text(textwrap.dedent("""\
            #define DISKNAME  Ubuntu 24.04 LTS "Noble Numbat" - Release amd64
            #define TYPE  binary
            #define ARCH  amd64
            #define DISTRIB_ID  Ubuntu
            #define DISTRIB_RELEASE  24.04
        """))
        result = _detect_diskdefines(tmp_path)

        assert result is not None
        assert result.os_family == "ubuntu"
        assert result.vendor == "ubuntu"
        assert result.version == "24.04"

    def test_ubuntu_diskname_fallback(self, tmp_path):
        """Falls back to DISKNAME when DISTRIB_ID is absent."""
        dd = tmp_path / "README.diskdefines"
        dd.write_text(textwrap.dedent("""\
            #define DISKNAME  Ubuntu 22.04.3 LTS "Jammy Jellyfish"
            #define TYPE  binary
            #define ARCH  amd64
        """))
        result = _detect_diskdefines(tmp_path)

        assert result is not None
        assert result.os_family == "ubuntu"
        assert result.vendor == "ubuntu"
        assert result.version == "22.04.3"

    def test_non_ubuntu_distrib_id(self, tmp_path):
        """Uses DISTRIB_ID as-is for non-Ubuntu distributions."""
        dd = tmp_path / "README.diskdefines"
        dd.write_text(textwrap.dedent("""\
            #define DISTRIB_ID  Mint
            #define DISTRIB_RELEASE  21.3
        """))
        result = _detect_diskdefines(tmp_path)

        assert result is not None
        assert result.os_family == "mint"
        assert result.vendor == "mint"
        assert result.version == "21.3"

    def test_missing_file(self, tmp_path):
        """Returns None when README.diskdefines does not exist."""
        result = _detect_diskdefines(tmp_path)

        assert result is None

    def test_empty_file(self, tmp_path):
        """Returns None for an empty README.diskdefines."""
        dd = tmp_path / "README.diskdefines"
        dd.write_text("")
        result = _detect_diskdefines(tmp_path)

        assert result is None

    def test_diskname_non_standard(self, tmp_path):
        """Returns None when DISKNAME has no parseable version."""
        dd = tmp_path / "README.diskdefines"
        dd.write_text("#define DISKNAME  Some_random_string\n")
        result = _detect_diskdefines(tmp_path)

        assert result is None


# =====================================================================
# _parse_diskname
# =====================================================================

class TestParseDiskname:

    def test_ubuntu_diskname(self):
        result = _parse_diskname(
            'Ubuntu 24.04 LTS "Noble Numbat" - Release amd64'
        )
        assert result is not None
        assert result.os_family == "ubuntu"
        assert result.version == "24.04"

    def test_non_matching_string(self):
        assert _parse_diskname("no version here") is None

    def test_generic_distro(self):
        result = _parse_diskname("Knoppix 9.1")
        assert result is not None
        assert result.os_family == "knoppix"
        assert result.version == "9.1"


# =====================================================================
# _detect_debian_dists
# =====================================================================

class TestDetectDebianDists:

    def test_debian_bookworm(self, tmp_path):
        """Detects Debian Bookworm from dists/stable/Release."""
        stable = tmp_path / "dists" / "stable"
        stable.mkdir(parents=True)
        (stable / "Release").write_text(textwrap.dedent("""\
            Origin: Debian
            Label: Debian
            Suite: stable
            Codename: bookworm
            Version: 12.5
        """))
        result = _detect_debian_dists(tmp_path)

        assert result is not None
        assert result.os_family == "debian"
        assert result.vendor == "debian"
        assert result.version == "12.5"

    def test_debian_uses_codename_when_no_version(self, tmp_path):
        """Falls back to Codename when Version is absent."""
        testing = tmp_path / "dists" / "testing"
        testing.mkdir(parents=True)
        (testing / "Release").write_text(textwrap.dedent("""\
            Origin: Debian
            Label: Debian
            Suite: testing
            Codename: trixie
        """))
        result = _detect_debian_dists(tmp_path)

        assert result is not None
        assert result.os_family == "debian"
        assert result.version == "trixie"

    def test_ubuntu_via_dists(self, tmp_path):
        """Recognises Ubuntu origin/label in a Release file."""
        noble = tmp_path / "dists" / "noble"
        noble.mkdir(parents=True)
        (noble / "Release").write_text(textwrap.dedent("""\
            Origin: Ubuntu
            Label: Ubuntu
            Suite: noble
            Codename: noble
            Version: 24.04
        """))
        result = _detect_debian_dists(tmp_path)

        assert result is not None
        assert result.os_family == "ubuntu"
        assert result.vendor == "ubuntu"
        assert result.version == "24.04"

    def test_no_dists_directory(self, tmp_path):
        """Returns None when dists/ does not exist."""
        result = _detect_debian_dists(tmp_path)

        assert result is None

    def test_empty_dists(self, tmp_path):
        """Returns None when dists/ has no subdirectories."""
        (tmp_path / "dists").mkdir()
        result = _detect_debian_dists(tmp_path)

        assert result is None

    def test_release_without_useful_fields(self, tmp_path):
        """Returns None when Release has no version/codename/suite."""
        sid = tmp_path / "dists" / "sid"
        sid.mkdir(parents=True)
        (sid / "Release").write_text("Origin: Debian\nLabel: Debian\n")
        result = _detect_debian_dists(tmp_path)

        assert result is None


# =====================================================================
# _detect_debian_netinst
# =====================================================================

class TestDetectDebianNetinst:

    def test_debian_netinst_with_disk_info(self, tmp_path):
        """Detects Debian netinst via install.amd/ + .disk/info."""
        (tmp_path / "install.amd").mkdir()
        disk = tmp_path / ".disk"
        disk.mkdir()
        (disk / "info").write_text(
            'Debian GNU/Linux 12.5.0 "Bookworm" - Official amd64 NETINST'
        )
        result = _detect_debian_netinst(tmp_path)

        assert result is not None
        assert result.os_family == "debian"
        assert result.vendor == "debian"
        assert result.version == "12"

    def test_debian_netinst_without_disk_info(self, tmp_path):
        """Falls back to version=unknown when .disk/info is absent."""
        (tmp_path / "install.amd").mkdir()
        result = _detect_debian_netinst(tmp_path)

        assert result is not None
        assert result.os_family == "debian"
        assert result.version == "unknown"

    def test_no_install_amd(self, tmp_path):
        """Returns None when install.amd/ is not present."""
        result = _detect_debian_netinst(tmp_path)

        assert result is None

    def test_disk_info_non_matching(self, tmp_path):
        """Falls back to unknown when .disk/info is not a Debian string."""
        (tmp_path / "install.amd").mkdir()
        disk = tmp_path / ".disk"
        disk.mkdir()
        (disk / "info").write_text("Something Else Entirely")
        result = _detect_debian_netinst(tmp_path)

        assert result is not None
        assert result.version == "unknown"


# =====================================================================
# _detect_openbsd
# =====================================================================

class TestDetectOpenBSD:

    def test_openbsd_with_sets(self, tmp_path):
        """Detects OpenBSD and extracts version from base76.tgz."""
        (tmp_path / "bsd.rd").write_text("")
        (tmp_path / "MANIFEST").write_text("")
        (tmp_path / "base76.tgz").write_text("")
        (tmp_path / "man76.tgz").write_text("")

        result = _detect_openbsd(tmp_path)

        assert result is not None
        assert result.os_family == "openbsd"
        assert result.vendor == "openbsd"
        assert result.version == "7.6"

    def test_openbsd_bsdrd_only(self, tmp_path):
        """Detects OpenBSD from bsd.rd alone (version unknown)."""
        (tmp_path / "bsd.rd").write_text("")

        result = _detect_openbsd(tmp_path)

        assert result is not None
        assert result.os_family == "openbsd"
        assert result.version == "unknown"

    def test_openbsd_manifest_only(self, tmp_path):
        """Detects OpenBSD from MANIFEST alone (version unknown)."""
        (tmp_path / "MANIFEST").write_text("")

        result = _detect_openbsd(tmp_path)

        assert result is not None
        assert result.os_family == "openbsd"
        assert result.version == "unknown"

    def test_not_openbsd(self, tmp_path):
        """Returns None when neither MANIFEST nor bsd.rd exists."""
        result = _detect_openbsd(tmp_path)

        assert result is None

    def test_openbsd_version_75(self, tmp_path):
        """Detects version 7.5 from base75.tgz."""
        (tmp_path / "bsd.rd").write_text("")
        (tmp_path / "base75.tgz").write_text("")

        result = _detect_openbsd(tmp_path)

        assert result is not None
        assert result.version == "7.5"


# =====================================================================
# _detect_freebsd
# =====================================================================

class TestDetectFreeBSD:

    def test_freebsd_with_release(self, tmp_path):
        """Detects FreeBSD with version from RELEASE file."""
        (tmp_path / "base.txz").write_text("")
        (tmp_path / "RELEASE").write_text("FreeBSD 14.1-RELEASE")

        result = _detect_freebsd(tmp_path)

        assert result is not None
        assert result.os_family == "freebsd"
        assert result.vendor == "freebsd"
        assert result.version == "14.1"

    def test_freebsd_alt_path(self, tmp_path):
        """Detects FreeBSD when base.txz is in usr/freebsd-dist/."""
        dist = tmp_path / "usr" / "freebsd-dist"
        dist.mkdir(parents=True)
        (dist / "base.txz").write_text("")

        result = _detect_freebsd(tmp_path)

        assert result is not None
        assert result.os_family == "freebsd"
        assert result.version == "unknown"

    def test_freebsd_without_release(self, tmp_path):
        """Falls back to version=unknown when RELEASE is absent."""
        (tmp_path / "base.txz").write_text("")

        result = _detect_freebsd(tmp_path)

        assert result is not None
        assert result.os_family == "freebsd"
        assert result.version == "unknown"

    def test_not_freebsd(self, tmp_path):
        """Returns None when base.txz is not present."""
        result = _detect_freebsd(tmp_path)

        assert result is None

    def test_freebsd_lowercase_release(self, tmp_path):
        """Reads lowercase 'release' file as well."""
        (tmp_path / "base.txz").write_text("")
        (tmp_path / "release").write_text("13.2-RELEASE")

        result = _detect_freebsd(tmp_path)

        assert result is not None
        assert result.version == "13.2"


# =====================================================================
# _detect_volume_label
# =====================================================================

class TestDetectVolumeLabel:

    def test_fedora_volume_label(self, tmp_path):
        """Reads a Fedora volume label from the ISO9660 PVD."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(_make_iso_bytes("Fedora-S-dvd-x86_64-42"))

        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "fedora"
        assert result.version == "42"

    def test_rhel_volume_label(self, tmp_path):
        """Reads a RHEL volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(_make_iso_bytes("RHEL-9.3-0-BaseOS-x86_64"))

        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "rhel"
        assert result.version == "9.3"

    def test_ubuntu_volume_label(self, tmp_path):
        """Reads an Ubuntu volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(_make_iso_bytes("Ubuntu 24.04 LTS amd64"))

        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "ubuntu"
        assert result.vendor == "ubuntu"
        assert result.version == "24.04"

    def test_debian_volume_label(self, tmp_path):
        """Reads a Debian volume label (major version extracted)."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(_make_iso_bytes("Debian 12.5.0 amd64 1"))

        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "debian"
        assert result.version == "12"

    def test_freebsd_volume_label(self, tmp_path):
        """Reads a FreeBSD volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(
            _make_iso_bytes("FreeBSD_14.1_RELEASE_AMD64")
        )
        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "freebsd"
        assert result.version == "14.1"

    def test_openbsd_volume_label(self, tmp_path):
        """Reads an OpenBSD volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(
            _make_iso_bytes("OpenBSD/amd64 7.6 Install CD")
        )
        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "openbsd"
        assert result.version == "7.6"

    def test_rocky_volume_label(self, tmp_path):
        """Reads a Rocky Linux volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(
            _make_iso_bytes("Rocky-9.3-x86_64-dvd")
        )
        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "rocky"
        assert result.version == "9.3"

    def test_centos_stream_volume_label(self, tmp_path):
        """Reads a CentOS Stream volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(
            _make_iso_bytes("CentOS-Stream-9-x86_64")
        )
        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "centos"
        assert result.version == "9"

    def test_alma_volume_label(self, tmp_path):
        """Reads an AlmaLinux volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(
            _make_iso_bytes("AlmaLinux-9.3-x86_64-dvd")
        )
        result = _detect_volume_label(iso)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "alma"
        assert result.version == "9.3"

    def test_missing_iso(self, tmp_path):
        """Returns None when the ISO file does not exist."""
        result = _detect_volume_label(tmp_path / "nope.iso")

        assert result is None

    def test_too_small_file(self, tmp_path):
        """Returns None when the file is smaller than the PVD offset."""
        iso = tmp_path / "tiny.iso"
        iso.write_bytes(b"\x00" * 100)

        result = _detect_volume_label(iso)

        assert result is None

    def test_invalid_pvd_signature(self, tmp_path):
        """Returns None when the PVD has a wrong signature."""
        iso = tmp_path / "bad.iso"
        data = bytearray(32768 + 2048)
        data[32768] = 0xFF  # wrong type
        iso.write_bytes(bytes(data))

        result = _detect_volume_label(iso)

        assert result is None

    def test_empty_volume_id(self, tmp_path):
        """Returns None when the volume ID is blank."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(_make_iso_bytes(""))

        result = _detect_volume_label(iso)

        assert result is None

    def test_unrecognised_volume_id(self, tmp_path):
        """Returns None for an unknown volume label."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(_make_iso_bytes("SOME_RANDOM_LABEL"))

        result = _detect_volume_label(iso)

        assert result is None


# =====================================================================
# _parse_volume_id
# =====================================================================

class TestParseVolumeId:

    @pytest.mark.parametrize(
        "vid,os_family,vendor,version",
        [
            ("Fedora-S-dvd-x86_64-42", "fedora", "fedora", "42"),
            ("RHEL-9.3-0-BaseOS-x86_64", "fedora", "rhel", "9.3"),
            ("Rocky-9.3-x86_64-dvd", "fedora", "rocky", "9.3"),
            ("AlmaLinux-9.3-x86_64-dvd", "fedora", "alma", "9.3"),
            ("CentOS-Stream-9-x86_64", "fedora", "centos", "9"),
            ("CentOS-8.5-x86_64", "fedora", "centos", "8.5"),
            ("Ubuntu 24.04 LTS amd64", "ubuntu", "ubuntu", "24.04"),
            ("Debian 12.5.0 amd64 1", "debian", "debian", "12"),
            ("FreeBSD_14.1_RELEASE_AMD64_DVD", "freebsd", "freebsd", "14.1"),
            ("OpenBSD/amd64 7.6 Install CD", "openbsd", "openbsd", "7.6"),
        ],
    )
    def test_known_labels(self, vid, os_family, vendor, version):
        result = _parse_volume_id(vid)

        assert result is not None
        assert result.os_family == os_family
        assert result.vendor == vendor
        assert result.version == version

    def test_unknown_label(self):
        assert _parse_volume_id("RANDOM_LABEL") is None

    def test_empty_label(self):
        assert _parse_volume_id("") is None


# =====================================================================
# detect_iso  (top-level dispatcher)
# =====================================================================

class TestDetectIso:

    def test_treeinfo_takes_priority(self, tmp_path):
        """Treeinfo is checked first and wins over other indicators."""
        # Create both .treeinfo and .discinfo
        (tmp_path / ".treeinfo").write_text(textwrap.dedent("""\
            [general]
            family = Fedora
            version = 42
        """))
        (tmp_path / ".discinfo").write_text(
            "0\nRed Hat Enterprise Linux 9\nx86_64\n"
        )
        result = detect_iso(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "fedora"
        assert result.version == "42"

    def test_discinfo_when_no_treeinfo(self, tmp_path):
        """Falls back to .discinfo when .treeinfo is absent."""
        (tmp_path / ".discinfo").write_text(
            "0\nRed Hat Enterprise Linux 8.5\nx86_64\n"
        )
        result = detect_iso(tmp_path)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.vendor == "rhel"
        assert result.version == "8.5"

    def test_diskdefines_detection(self, tmp_path):
        """Detects Ubuntu via README.diskdefines."""
        (tmp_path / "README.diskdefines").write_text(textwrap.dedent("""\
            #define DISTRIB_ID  Ubuntu
            #define DISTRIB_RELEASE  24.04
        """))
        result = detect_iso(tmp_path)

        assert result is not None
        assert result.os_family == "ubuntu"
        assert result.version == "24.04"

    def test_debian_dists_detection(self, tmp_path):
        """Detects Debian via dists/stable/Release."""
        stable = tmp_path / "dists" / "stable"
        stable.mkdir(parents=True)
        (stable / "Release").write_text(textwrap.dedent("""\
            Origin: Debian
            Label: Debian
            Version: 12.5
            Codename: bookworm
        """))
        result = detect_iso(tmp_path)

        assert result is not None
        assert result.os_family == "debian"
        assert result.version == "12.5"

    def test_openbsd_detection(self, tmp_path):
        """Detects OpenBSD via bsd.rd + base sets."""
        (tmp_path / "bsd.rd").write_text("")
        (tmp_path / "base76.tgz").write_text("")

        result = detect_iso(tmp_path)

        assert result is not None
        assert result.os_family == "openbsd"
        assert result.version == "7.6"

    def test_freebsd_detection(self, tmp_path):
        """Detects FreeBSD via base.txz + RELEASE."""
        (tmp_path / "base.txz").write_text("")
        (tmp_path / "RELEASE").write_text("14.1-RELEASE")

        result = detect_iso(tmp_path)

        assert result is not None
        assert result.os_family == "freebsd"
        assert result.version == "14.1"

    def test_volume_label_fallback(self, tmp_path):
        """Falls back to volume label when no files match."""
        iso = tmp_path / "test.iso"
        iso.write_bytes(_make_iso_bytes("Fedora-S-dvd-x86_64-42"))

        mount = tmp_path / "mount"
        mount.mkdir()

        result = detect_iso(mount, iso_path=iso)

        assert result is not None
        assert result.os_family == "fedora"
        assert result.version == "42"

    def test_returns_none_when_nothing_matches(self, tmp_path):
        """Returns None when no detection method matches."""
        result = detect_iso(tmp_path)

        assert result is None

    def test_returns_none_without_iso_path(self, tmp_path):
        """Returns None without iso_path for volume label fallback."""
        result = detect_iso(tmp_path, iso_path=None)

        assert result is None

    def test_debian_netinst_detection(self, tmp_path):
        """Detects Debian netinst via install.amd/."""
        (tmp_path / "install.amd").mkdir()
        disk = tmp_path / ".disk"
        disk.mkdir()
        (disk / "info").write_text(
            'Debian GNU/Linux 12.5.0 "Bookworm" - Official amd64 NETINST'
        )
        result = detect_iso(tmp_path)

        assert result is not None
        assert result.os_family == "debian"
        assert result.version == "12"

