"""Auto-detection of OS family, vendor, and version from ISO metadata."""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Optional

from tftpos.mnemonics import DistroAlias


def is_live_iso(mount_point: Path) -> bool:
    """Check if a mounted ISO contains a live filesystem.

    Looks for known squashfs rootfs paths used by major distros:
    - Fedora Live: ``LiveOS/squashfs.img``
    - Ubuntu Live: ``casper/filesystem.squashfs``
    - Debian Live: ``live/filesystem.squashfs``
    - Arch Live: ``arch/x86_64/airootfs.sfs``
    """
    live_markers = [
        "LiveOS/squashfs.img",
        "casper/filesystem.squashfs",
        "live/filesystem.squashfs",
        "arch/x86_64/airootfs.sfs",
    ]
    return any(
        (mount_point / marker).is_file()
        for marker in live_markers
    )


def detect_iso(
    mount_point: Path,
    iso_path: Optional[Path] = None,
) -> Optional[DistroAlias]:
    """Detect OS family, vendor, and version from mounted ISO contents.

    Checks multiple metadata sources in order of specificity:

    1. ``.treeinfo`` / ``treeinfo`` (Fedora/RHEL/CentOS/Rocky/Alma)
    2. ``.discinfo`` (older RHEL/CentOS)
    3. ``README.diskdefines`` (Ubuntu)
    4. ``dists/`` directory with Release file (Debian)
    5. ``install.amd`` directory (Debian netinst)
    6. ``MANIFEST`` or ``bsd.rd`` (OpenBSD)
    7. ``base.txz`` with RELEASE file (FreeBSD)
    8. Volume label from ISO9660 primary volume descriptor

    Args:
        mount_point: Path where the ISO is mounted.
        iso_path: Optional path to the ISO file itself (for volume label).

    Returns:
        A ``DistroAlias`` with os_family, vendor, and version, or ``None``
        if the ISO contents cannot be recognised.
    """
    detectors = [
        _detect_treeinfo,
        _detect_discinfo,
        _detect_diskdefines,
        _detect_debian_dists,
        _detect_debian_netinst,
        _detect_openbsd,
        _detect_freebsd,
    ]

    for detector in detectors:
        result = detector(mount_point)
        if result is not None:
            return result

    # Volume label from ISO9660 header as last resort
    if iso_path is not None:
        result = _detect_volume_label(iso_path)
        if result is not None:
            return result

    return None


# ------------------------------------------------------------------
# .treeinfo / treeinfo  (Fedora, RHEL, CentOS, Rocky, Alma)
# ------------------------------------------------------------------

_TREEINFO_FAMILY_MAP = {
    "fedora": ("fedora", "fedora"),
    "red hat enterprise linux": ("fedora", "rhel"),
    "rhel": ("fedora", "rhel"),
    "centos": ("fedora", "centos"),
    "centos stream": ("fedora", "centos"),
    "centos linux": ("fedora", "centos"),
    "rocky": ("fedora", "rocky"),
    "rocky linux": ("fedora", "rocky"),
    "almalinux": ("fedora", "alma"),
    "alma": ("fedora", "alma"),
}


def _detect_treeinfo(mount_point: Path) -> Optional[DistroAlias]:
    """Parse ``.treeinfo`` or ``treeinfo`` for Fedora-family ISOs."""
    for name in (".treeinfo", "treeinfo"):
        path = mount_point / name
        if path.is_file():
            return _parse_treeinfo(path)
    return None


def _parse_treeinfo(path: Path) -> Optional[DistroAlias]:
    """Extract OS info from a treeinfo INI file.

    Expected ``[general]`` keys: ``family``, ``version``, optionally ``name``.
    """
    parser = configparser.ConfigParser()
    try:
        parser.read(str(path), encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return None

    if not parser.has_section("general"):
        return None

    family = parser.get("general", "family", fallback="").strip()
    version = parser.get("general", "version", fallback="").strip()
    name = parser.get("general", "name", fallback="").strip()

    if not family and name:
        family = name.split()[0]

    if not family or not version:
        return None

    family_lower = family.lower()

    os_family = None
    vendor = None
    for key, (fam, vnd) in _TREEINFO_FAMILY_MAP.items():
        if family_lower == key or family_lower.startswith(key):
            os_family = fam
            vendor = vnd
            break

    if os_family is None:
        os_family = family_lower
        vendor = family_lower

    return DistroAlias(os_family=os_family, vendor=vendor, version=version)


# ------------------------------------------------------------------
# .discinfo  (older RHEL / CentOS)
# ------------------------------------------------------------------

_DISCINFO_PATTERNS = [
    (r"red\s+hat\s+enterprise\s+linux\s+(\d[\d.]*)", "fedora", "rhel"),
    (r"centos\s+(?:stream\s+|linux\s+)?(\d[\d.]*)", "fedora", "centos"),
    (r"rocky\s+(?:linux\s+)?(\d[\d.]*)", "fedora", "rocky"),
    (r"alma\s*linux\s+(\d[\d.]*)", "fedora", "alma"),
    (r"fedora\s+(?:linux\s+)?(\d[\d.]*)", "fedora", "fedora"),
]


def _detect_discinfo(mount_point: Path) -> Optional[DistroAlias]:
    """Parse ``.discinfo`` for older RHEL/CentOS ISOs.

    Line format::

        line 1: timestamp
        line 2: release description  (e.g. "Red Hat Enterprise Linux 8.5")
        line 3: architecture
    """
    path = mount_point / ".discinfo"
    if not path.is_file():
        return None

    try:
        lines = path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return None

    if len(lines) < 2:
        return None

    release_line = lines[1].strip()
    if not release_line:
        return None

    return _parse_release_string(release_line)


def _parse_release_string(release: str) -> Optional[DistroAlias]:
    """Match a release description against known distro patterns."""
    release_lower = release.lower()
    for pattern, os_family, vendor in _DISCINFO_PATTERNS:
        m = re.search(pattern, release_lower)
        if m:
            return DistroAlias(
                os_family=os_family,
                vendor=vendor,
                version=m.group(1),
            )
    return None


# ------------------------------------------------------------------
# README.diskdefines  (Ubuntu)
# ------------------------------------------------------------------

def _detect_diskdefines(mount_point: Path) -> Optional[DistroAlias]:
    """Parse ``README.diskdefines`` for Ubuntu ISOs.

    Looks for ``#define DISTRIB_ID`` and ``#define DISTRIB_RELEASE``,
    falling back to ``#define DISKNAME`` when those are absent.
    """
    path = mount_point / "README.diskdefines"
    if not path.is_file():
        return None

    try:
        content = path.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None

    distrib_id = None
    distrib_release = None

    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"#define\s+DISTRIB_ID\s+(\S+)", line)
        if m:
            distrib_id = m.group(1).strip()
        m = re.match(r"#define\s+DISTRIB_RELEASE\s+(\S+)", line)
        if m:
            distrib_release = m.group(1).strip()

    if distrib_id and distrib_release:
        dl = distrib_id.lower()
        if dl == "ubuntu":
            return DistroAlias(
                os_family="ubuntu",
                vendor="ubuntu",
                version=distrib_release,
            )
        return DistroAlias(
            os_family=dl, vendor=dl, version=distrib_release,
        )

    # Fallback: parse DISKNAME
    for line in content.splitlines():
        m = re.match(r"#define\s+DISKNAME\s+(.*)", line)
        if m:
            return _parse_diskname(m.group(1).strip())

    return None


def _parse_diskname(diskname: str) -> Optional[DistroAlias]:
    """Parse a ``DISKNAME`` value such as ``Ubuntu 24.04 LTS ...``."""
    m = re.match(r"(\w+)\s+([\d.]+)", diskname)
    if not m:
        return None
    name = m.group(1).lower()
    version = m.group(2)
    if name == "ubuntu":
        return DistroAlias(
            os_family="ubuntu", vendor="ubuntu", version=version,
        )
    return DistroAlias(os_family=name, vendor=name, version=version)


# ------------------------------------------------------------------
# dists/  directory  (Debian)
# ------------------------------------------------------------------

def _detect_debian_dists(mount_point: Path) -> Optional[DistroAlias]:
    """Detect Debian via ``dists/<suite>/Release`` files."""
    dists_dir = mount_point / "dists"
    if not dists_dir.is_dir():
        return None

    for subdir in sorted(dists_dir.iterdir()):
        if not subdir.is_dir():
            continue
        release_file = subdir / "Release"
        if release_file.is_file():
            result = _parse_debian_release(release_file)
            if result is not None:
                return result

    return None


def _parse_debian_release(path: Path) -> Optional[DistroAlias]:
    """Parse a Debian ``Release`` file for Suite, Codename, Version."""
    try:
        content = path.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None

    fields: dict[str, str] = {}
    for line in content.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()

    version = fields.get("version", "")
    codename = fields.get("codename", "")
    suite = fields.get("suite", "")
    label = fields.get("label", "").lower()
    origin = fields.get("origin", "").lower()

    if label == "ubuntu" or "ubuntu" in origin:
        os_family = "ubuntu"
        vendor = "ubuntu"
    else:
        os_family = "debian"
        vendor = "debian"

    detected_version = version or codename or suite
    if not detected_version:
        return None

    return DistroAlias(
        os_family=os_family,
        vendor=vendor,
        version=detected_version,
    )


# ------------------------------------------------------------------
# install.amd/  directory  (Debian netinst)
# ------------------------------------------------------------------

def _detect_debian_netinst(
    mount_point: Path,
) -> Optional[DistroAlias]:
    """Detect Debian netinst ISOs by ``install.amd/`` directory."""
    install_amd = mount_point / "install.amd"
    if not install_amd.is_dir():
        return None

    # .disk/info often contains a version string
    disk_info = mount_point / ".disk" / "info"
    if disk_info.is_file():
        try:
            info = disk_info.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        except OSError:
            info = ""
        if info:
            m = re.match(
                r"Debian\s+GNU/Linux\s+([\d.]+)",
                info,
                re.IGNORECASE,
            )
            if m:
                major = m.group(1).split(".")[0]
                return DistroAlias(
                    os_family="debian",
                    vendor="debian",
                    version=major,
                )

    # We know it is Debian but cannot determine the version
    return DistroAlias(
        os_family="debian",
        vendor="debian",
        version="unknown",
    )


# ------------------------------------------------------------------
# MANIFEST / bsd.rd  (OpenBSD)
# ------------------------------------------------------------------

def _detect_openbsd(mount_point: Path) -> Optional[DistroAlias]:
    """Detect OpenBSD by ``MANIFEST`` or ``bsd.rd`` files.

    Version is inferred from set filenames (e.g. ``base76.tgz``).
    """
    has_manifest = (mount_point / "MANIFEST").is_file()
    has_bsdrd = (mount_point / "bsd.rd").is_file()

    if not (has_manifest or has_bsdrd):
        return None

    version = _openbsd_version_from_sets(mount_point)
    if version is None:
        version = "unknown"

    return DistroAlias(
        os_family="openbsd", vendor="openbsd", version=version,
    )


def _openbsd_version_from_sets(
    mount_point: Path,
) -> Optional[str]:
    """Infer OpenBSD version from set filenames like ``base76.tgz``."""
    for pattern in ("base*.tgz", "man*.tgz"):
        matches = list(mount_point.glob(pattern))
        if matches:
            m = re.match(r"(?:base|man)(\d+)", matches[0].stem)
            if m:
                ver = m.group(1)
                if len(ver) == 2:
                    return f"{ver[0]}.{ver[1]}"
                return ver
    return None


# ------------------------------------------------------------------
# base.txz  (FreeBSD)
# ------------------------------------------------------------------

def _detect_freebsd(mount_point: Path) -> Optional[DistroAlias]:
    """Detect FreeBSD by ``base.txz`` (root or ``usr/freebsd-dist/``)."""
    base_txz = mount_point / "base.txz"
    alt_base = mount_point / "usr" / "freebsd-dist" / "base.txz"

    if not (base_txz.is_file() or alt_base.is_file()):
        return None

    version = _freebsd_version(mount_point)
    if version is None:
        version = "unknown"

    return DistroAlias(
        os_family="freebsd", vendor="freebsd", version=version,
    )


def _freebsd_version(mount_point: Path) -> Optional[str]:
    """Try to extract the FreeBSD version from a RELEASE file."""
    for name in ("RELEASE", "release"):
        path = mount_point / name
        if path.is_file():
            try:
                content = path.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
                m = re.search(r"(\d+\.\d+)", content)
                if m:
                    return m.group(1)
            except OSError:
                continue
    return None


# ------------------------------------------------------------------
# ISO 9660 volume label
# ------------------------------------------------------------------

def _detect_volume_label(
    iso_path: Path,
) -> Optional[DistroAlias]:
    """Read the volume ID from the ISO9660 primary volume descriptor.

    The PVD sits at sector 16 (byte offset 32768).  The volume
    identifier occupies bytes 40--72 of the descriptor (32 bytes,
    space-padded ASCII).
    """
    try:
        with open(iso_path, "rb") as fh:
            fh.seek(32768)
            pvd = fh.read(2048)
    except OSError:
        return None

    if len(pvd) < 72:
        return None

    # PVD type indicator = 1, standard id = 'CD001'
    if pvd[0:1] != b"\x01" or pvd[1:6] != b"CD001":
        return None

    volume_id = pvd[40:72].decode("ascii", errors="replace").strip()
    if not volume_id:
        return None

    return _parse_volume_id(volume_id)


def _parse_volume_id(volume_id: str) -> Optional[DistroAlias]:
    """Map common ISO volume-label patterns to a ``DistroAlias``.

    Examples::

        Fedora-S-dvd-x86_64-42
        RHEL-9.3-0-BaseOS-x86_64
        Rocky-9.3-x86_64-dvd
        CentOS-Stream-9-x86_64
        Ubuntu 24.04 LTS amd64
        Debian 12.5.0 amd64 1
        FreeBSD_14.1_RELEASE_AMD64_DVD
        OpenBSD/amd64 7.6 Install CD
    """
    vid_lower = volume_id.lower()

    # Fedora: trailing version number
    m = re.match(r"fedora[- ].*?(\d+)\s*$", vid_lower)
    if m:
        return DistroAlias("fedora", "fedora", m.group(1))

    # RHEL
    m = re.match(r"rhel[- ]([\d.]+)", vid_lower)
    if m:
        return DistroAlias("fedora", "rhel", m.group(1))

    # Rocky
    m = re.match(r"rocky[- ]([\d.]+)", vid_lower)
    if m:
        return DistroAlias("fedora", "rocky", m.group(1))

    # AlmaLinux
    m = re.match(r"alma(?:linux)?[- ]([\d.]+)", vid_lower)
    if m:
        return DistroAlias("fedora", "alma", m.group(1))

    # CentOS
    m = re.match(r"centos[- ](?:stream[- ])?([\d.]+)", vid_lower)
    if m:
        return DistroAlias("fedora", "centos", m.group(1))

    # Ubuntu
    m = re.match(r"ubuntu[- ]([\d.]+)", vid_lower)
    if m:
        return DistroAlias("ubuntu", "ubuntu", m.group(1))

    # Debian
    m = re.match(r"debian[- ]([\d.]+)", vid_lower)
    if m:
        major = m.group(1).split(".")[0]
        return DistroAlias("debian", "debian", major)

    # FreeBSD
    m = re.match(r"freebsd[_ ]([\d.]+)", vid_lower)
    if m:
        return DistroAlias("freebsd", "freebsd", m.group(1))

    # OpenBSD: version always contains a dot (e.g. 7.6)
    m = re.search(r"openbsd.*?(\d+\.\d+)", vid_lower)
    if m:
        return DistroAlias("openbsd", "openbsd", m.group(1))

    return None
