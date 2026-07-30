"""Example FirmwarePlugin for OpenWRT.

This demonstrates how to write a minimal plugin for TftpOS.
Place your real plugins in your own package or in tftpos/plugins/,
not in this examples directory.

Usage:
    from examples.openwrt_plugin import OpenWRTPlugin
    from tftpos.models import ProvisionProfile

    plugin = OpenWRTPlugin()
    profile = ProvisionProfile(
        name="router-lab",
        os_family="openwrt",
        os_version="23.05",
        arch="mipsel",
    )

    errors = plugin.validate_profile(profile)
    if not errors:
        path = plugin.firmware_path(profile)
        print(f"Firmware path: {path}")
"""

from tftpos.plugins.base import FirmwarePlugin
from tftpos.models import ProvisionProfile


class OpenWRTPlugin(FirmwarePlugin):
    """Resolve firmware paths for OpenWRT devices."""

    @property
    def os_family(self) -> str:
        return "openwrt"

    @property
    def supported_versions(self) -> list[str]:
        return ["23.05", "24.10"]

    def firmware_path(self, profile: ProvisionProfile) -> str:
        return f"/srv/tftpos/distros/openwrt/{profile.os_version}/firmware.bin"
