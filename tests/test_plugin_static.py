"""Tests for tftpos.plugins.static.StaticFirmwarePlugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from tftpos.models import ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin
from tftpos.plugins.static import StaticFirmwarePlugin


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_profile(**overrides) -> ProvisionProfile:
    defaults = dict(
        name="test-device",
        os_family="openwrt",
        os_version="23.05",
        arch="mipsel",
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

    def test_os_family_returns_configured_value(self):
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="ddwrt",
        )
        assert plugin.os_family == "ddwrt"

    def test_supported_versions_returns_configured_list(self):
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="openwrt",
            supported_versions=["23.05", "24.10"],
        )
        assert plugin.supported_versions == ["23.05", "24.10"]

    def test_supported_versions_defaults_to_empty(self):
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="openwrt",
        )
        assert plugin.supported_versions == []

    def test_supported_versions_returns_copy(self):
        """Mutating the returned list must not affect the plugin."""
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="openwrt",
            supported_versions=["23.05"],
        )
        versions = plugin.supported_versions
        versions.append("99.99")
        assert "99.99" not in plugin.supported_versions

    def test_is_firmware_plugin_subclass(self):
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="openwrt",
        )
        assert isinstance(plugin, FirmwarePlugin)


# ---------------------------------------------------------------------------
# firmware_path — basic (no arch)
# ---------------------------------------------------------------------------

class TestFirmwarePathBasic:

    def test_resolves_basic_path(self, tmp_path):
        _create_firmware(tmp_path, "openwrt", "23.05", "firmware.bin")
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
        )
        profile = _make_profile()
        result = plugin.firmware_path(profile)
        assert result.endswith("openwrt/23.05/firmware.bin")
        assert Path(result).is_file()

    def test_custom_filename(self, tmp_path):
        _create_firmware(
            tmp_path, "ddwrt", "2024.1", "factory-image.img"
        )
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="ddwrt",
            filename="factory-image.img",
        )
        profile = _make_profile(os_family="ddwrt", os_version="2024.1")
        result = plugin.firmware_path(profile)
        assert result.endswith("ddwrt/2024.1/factory-image.img")

    def test_uses_profile_os_family_in_path(self, tmp_path):
        """Path uses the profile's os_family, not the plugin's."""
        _create_firmware(tmp_path, "freshtomato", "2024", "firmware.bin")
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="freshtomato",
        )
        profile = _make_profile(
            os_family="freshtomato", os_version="2024"
        )
        result = plugin.firmware_path(profile)
        assert "freshtomato/2024/firmware.bin" in result


# ---------------------------------------------------------------------------
# firmware_path — with arch
# ---------------------------------------------------------------------------

class TestFirmwarePathWithArch:

    def test_resolves_arch_path(self, tmp_path):
        _create_firmware(
            tmp_path, "openwrt", "23.05", "mipsel", "firmware.bin"
        )
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
            include_arch=True,
        )
        profile = _make_profile()
        result = plugin.firmware_path(profile)
        assert result.endswith(
            "openwrt/23.05/mipsel/firmware.bin"
        )

    def test_arch_with_custom_filename(self, tmp_path):
        _create_firmware(
            tmp_path,
            "openwrt",
            "24.10",
            "arm_cortex-a7",
            "sysupgrade.bin",
        )
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
            filename="sysupgrade.bin",
            include_arch=True,
        )
        profile = _make_profile(
            os_version="24.10", arch="arm_cortex-a7"
        )
        result = plugin.firmware_path(profile)
        assert "24.10/arm_cortex-a7/sysupgrade.bin" in result

    def test_arch_not_included_when_disabled(self, tmp_path):
        """Even if profile has arch, path omits it when include_arch=False."""
        _create_firmware(tmp_path, "openwrt", "23.05", "firmware.bin")
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
            include_arch=False,
        )
        profile = _make_profile(arch="mipsel")
        result = plugin.firmware_path(profile)
        assert "mipsel" not in result


# ---------------------------------------------------------------------------
# firmware_path — error cases
# ---------------------------------------------------------------------------

class TestFirmwarePathErrors:

    def test_raises_when_file_missing(self, tmp_path):
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
        )
        profile = _make_profile()
        with pytest.raises(FileNotFoundError, match="firmware not found"):
            plugin.firmware_path(profile)

    def test_raises_on_empty_os_version(self, tmp_path):
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
        )
        profile = _make_profile(os_version="")
        with pytest.raises(ValueError, match="os_version must not be empty"):
            plugin.firmware_path(profile)

    def test_raises_on_empty_os_family(self, tmp_path):
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
        )
        profile = _make_profile(os_family="")
        with pytest.raises(ValueError, match="os_family must not be empty"):
            plugin.firmware_path(profile)

    def test_raises_on_traversal_attempt(self, tmp_path):
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
        )
        profile = _make_profile(os_version="../../etc")
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)

    def test_raises_on_empty_arch_when_required(self, tmp_path):
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
            include_arch=True,
        )
        profile = _make_profile(arch="")
        with pytest.raises(ValueError, match="arch must not be empty"):
            plugin.firmware_path(profile)

    def test_raises_on_dotdot_traversal(self, tmp_path):
        """Pure '..' without slashes must still be rejected."""
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
        )
        profile = _make_profile(os_version="..")
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)

    def test_raises_on_single_dot(self, tmp_path):
        plugin = StaticFirmwarePlugin(
            distro_root=tmp_path,
            os_family="openwrt",
        )
        profile = _make_profile(os_version=".")
        with pytest.raises(ValueError, match="invalid characters"):
            plugin.firmware_path(profile)


# ---------------------------------------------------------------------------
# validate_profile (inherited from FirmwarePlugin)
# ---------------------------------------------------------------------------

class TestValidateProfile:

    def test_valid_profile_no_errors(self):
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="openwrt",
            supported_versions=["23.05"],
        )
        profile = _make_profile()
        errors = plugin.validate_profile(profile)
        assert errors == []

    def test_os_family_mismatch(self):
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="openwrt",
        )
        profile = _make_profile(os_family="ddwrt")
        errors = plugin.validate_profile(profile)
        assert any("mismatch" in e for e in errors)

    def test_unsupported_version(self):
        plugin = StaticFirmwarePlugin(
            distro_root="/srv",
            os_family="openwrt",
            supported_versions=["23.05"],
        )
        profile = _make_profile(os_version="99.99")
        errors = plugin.validate_profile(profile)
        assert any("unsupported version" in e for e in errors)


# ---------------------------------------------------------------------------
# entry-point discovery
# ---------------------------------------------------------------------------

class TestEntryPointDiscovery:

    def test_plugin_registered_in_entry_points(self):
        """Plugin is importable from the declared entry-point path."""
        from importlib.metadata import entry_points

        eps = entry_points()
        # entry_points() may return a dict (3.9-3.11) or
        # SelectableGroups (3.12+)
        if isinstance(eps, dict):
            group = eps.get("tftpos.plugins", [])
        else:
            group = eps.select(group="tftpos.plugins")

        names = [ep.name for ep in group]
        assert "static" in names, (
            f"'static' not found in tftpos.plugins entry points: {names}"
        )

    def test_entry_point_loads_correct_class(self):
        from importlib.metadata import entry_points

        eps = entry_points()
        if isinstance(eps, dict):
            group = eps.get("tftpos.plugins", [])
        else:
            group = eps.select(group="tftpos.plugins")

        ep = next(ep for ep in group if ep.name == "static")
        cls = ep.load()
        assert cls is StaticFirmwarePlugin
