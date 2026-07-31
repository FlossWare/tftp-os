"""Tests for tftpos.plugins.openwrt.OpenWrtPlugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from tftpos.models import ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin
from tftpos.plugins.openwrt import OpenWrtPlugin


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_profile(**overrides) -> ProvisionProfile:
    defaults = dict(
        name="router-lab",
        os_family="openwrt",
        os_version="23.05",
        arch="ath79-generic",
    )
    defaults.update(overrides)
    return ProvisionProfile(**defaults)


def _create_firmware(tmp_path: Path, *parts: str) -> Path:
    """Create a firmware file under tmp_path and return its path."""
    firmware = tmp_path.joinpath(*parts)
    firmware.parent.mkdir(parents=True, exist_ok=True)
    firmware.write_bytes(b"\x00" * 64)
    return firmware


# ---------------------------------------------------------------------------
# construction and properties
# ---------------------------------------------------------------------------

class TestProperties:

    def test_os_family_returns_openwrt(self):
        plugin = OpenWrtPlugin(distro_root="/srv")
        assert plugin.os_family == "openwrt"

    def test_supported_versions_returns_configured_list(self):
        plugin = OpenWrtPlugin(
            distro_root="/srv",
            supported_versions=["23.05", "24.10"],
        )
        assert plugin.supported_versions == ["23.05", "24.10"]

    def test_supported_versions_defaults_to_empty(self):
        plugin = OpenWrtPlugin(distro_root="/srv")
        assert plugin.supported_versions == []

    def test_supported_versions_returns_copy(self):
        """Mutating the returned list must not affect the plugin."""
        plugin = OpenWrtPlugin(
            distro_root="/srv",
            supported_versions=["23.05"],
        )
        versions = plugin.supported_versions
        versions.append("99.99")
        assert "99.99" not in plugin.supported_versions

    def test_is_firmware_plugin_subclass(self):
        plugin = OpenWrtPlugin(distro_root="/srv")
        assert isinstance(plugin, FirmwarePlugin)

    def test_rejects_traversal_in_image_type(self):
        with pytest.raises(ValueError, match="image_type"):
            OpenWrtPlugin(
                distro_root="/srv",
                image_type="../etc/passwd",
            )


# ---------------------------------------------------------------------------
# firmware_path -- basic path resolution
# ---------------------------------------------------------------------------

class TestFirmwarePathBasic:

    def test_resolves_target_subtarget_path(self, tmp_path):
        _create_firmware(
            tmp_path,
            "openwrt", "23.05", "targets", "ath79", "generic",
            "sysupgrade.bin",
        )
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile()
        result = plugin.firmware_path(profile)
        assert result.endswith(
            "openwrt/23.05/targets/ath79/generic/sysupgrade.bin"
        )
        assert Path(result).is_file()

    def test_default_image_type_sysupgrade(self, tmp_path):
        _create_firmware(
            tmp_path,
            "openwrt", "24.10", "targets", "ramips", "mt7621",
            "sysupgrade.bin",
        )
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(
            os_version="24.10", arch="ramips-mt7621",
        )
        result = plugin.firmware_path(profile)
        assert "sysupgrade.bin" in result

    def test_custom_image_type_factory(self, tmp_path):
        _create_firmware(
            tmp_path,
            "openwrt", "23.05", "targets", "ath79", "generic",
            "factory.bin",
        )
        plugin = OpenWrtPlugin(
            distro_root=tmp_path, image_type="factory",
        )
        profile = _make_profile()
        result = plugin.firmware_path(profile)
        assert result.endswith(
            "openwrt/23.05/targets/ath79/generic/factory.bin"
        )

    def test_filename_from_profile_extra(self, tmp_path):
        custom = "openwrt-23.05-ath79-generic-tplink_archer-c7-v5-sysupgrade.bin"
        _create_firmware(
            tmp_path,
            "openwrt", "23.05", "targets", "ath79", "generic",
            custom,
        )
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(extra={"filename": custom})
        result = plugin.firmware_path(profile)
        assert result.endswith(custom)

    def test_arch_without_dash_defaults_to_generic(self, tmp_path):
        _create_firmware(
            tmp_path,
            "openwrt", "23.05", "targets", "x86", "generic",
            "sysupgrade.bin",
        )
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(arch="x86")
        result = plugin.firmware_path(profile)
        assert result.endswith(
            "openwrt/23.05/targets/x86/generic/sysupgrade.bin"
        )

    def test_multiple_versions_supported(self, tmp_path):
        _create_firmware(
            tmp_path,
            "openwrt", "23.05", "targets", "ath79", "generic",
            "sysupgrade.bin",
        )
        _create_firmware(
            tmp_path,
            "openwrt", "24.10", "targets", "ath79", "generic",
            "sysupgrade.bin",
        )
        plugin = OpenWrtPlugin(
            distro_root=tmp_path,
            supported_versions=["23.05", "24.10"],
        )

        p1 = _make_profile(os_version="23.05")
        r1 = plugin.firmware_path(p1)
        assert "23.05" in r1

        p2 = _make_profile(os_version="24.10")
        r2 = plugin.firmware_path(p2)
        assert "24.10" in r2


# ---------------------------------------------------------------------------
# firmware_path -- error cases
# ---------------------------------------------------------------------------

class TestFirmwarePathErrors:

    def test_raises_when_file_missing(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile()
        with pytest.raises(FileNotFoundError, match="firmware not found"):
            plugin.firmware_path(profile)

    def test_raises_on_empty_os_version(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(os_version="")
        with pytest.raises(
            ValueError, match="os_version must not be empty"
        ):
            plugin.firmware_path(profile)

    def test_raises_on_empty_arch(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(arch="")
        with pytest.raises(ValueError, match="arch must not be empty"):
            plugin.firmware_path(profile)

    def test_raises_on_traversal_in_os_version(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(os_version="../../etc")
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)

    def test_raises_on_dotdot_os_version(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(os_version="..")
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)

    def test_raises_on_dot_os_version(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(os_version=".")
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)

    def test_raises_on_traversal_in_arch(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(arch="../../etc")
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)

    def test_raises_on_traversal_in_extra_filename(self, tmp_path):
        plugin = OpenWrtPlugin(distro_root=tmp_path)
        profile = _make_profile(
            extra={"filename": "../../../etc/passwd"},
        )
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)


# ---------------------------------------------------------------------------
# validate_profile (inherited from FirmwarePlugin)
# ---------------------------------------------------------------------------

class TestValidateProfile:

    def test_valid_profile_no_errors(self):
        plugin = OpenWrtPlugin(
            distro_root="/srv",
            supported_versions=["23.05"],
        )
        profile = _make_profile()
        errors = plugin.validate_profile(profile)
        assert errors == []

    def test_os_family_mismatch(self):
        plugin = OpenWrtPlugin(distro_root="/srv")
        profile = _make_profile(os_family="ddwrt")
        errors = plugin.validate_profile(profile)
        assert any("mismatch" in e for e in errors)

    def test_unsupported_version(self):
        plugin = OpenWrtPlugin(
            distro_root="/srv",
            supported_versions=["23.05"],
        )
        profile = _make_profile(os_version="99.99")
        errors = plugin.validate_profile(profile)
        assert any("unsupported version" in e for e in errors)
