"""Built-in static firmware plugin for directory-convention path resolution.

Resolves firmware paths using a simple directory layout:

    {distro_root}/{os_family}/{os_version}/{filename}

or, when architecture matters:

    {distro_root}/{os_family}/{os_version}/{arch}/{filename}

Works for OpenWRT, DD-WRT, FreshTomato, or any firmware that follows
a flat directory convention.

Usage::

    from tftpos.plugins.static import StaticFirmwarePlugin
    from tftpos.models import ProvisionProfile

    plugin = StaticFirmwarePlugin(
        distro_root="/srv/tftpos/distros",
        os_family="openwrt",
        supported_versions=["23.05", "24.10"],
    )
    profile = ProvisionProfile(
        name="router-lab",
        os_family="openwrt",
        os_version="23.05",
        arch="mipsel",
    )
    path = plugin.firmware_path(profile)
"""

from __future__ import annotations

import re
from pathlib import Path

from tftpos.models import ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin

_SAFE_SEGMENT = re.compile(r"^[\w.\-]+$")


def _validate_segment(value: object, label: str) -> None:
    """Reject path segments that could cause directory traversal."""
    if value is None or value == "":
        raise ValueError(f"{label} must not be empty")
    if not isinstance(value, str):
        raise ValueError(
            f"{label} must be a string, got {type(value).__name__}"
        )
    if value in (".", ".."):
        raise ValueError(
            f"{label} contains invalid characters: {value!r}"
        )
    if not _SAFE_SEGMENT.match(value):
        raise ValueError(
            f"{label} contains invalid characters: {value!r}"
        )


class StaticFirmwarePlugin(FirmwarePlugin):
    """Resolve firmware paths via a static directory convention.

    Parameters
    ----------
    distro_root:
        Absolute path to the root directory that holds firmware trees.
    os_family:
        The OS family this plugin serves (e.g. ``"openwrt"``).
    supported_versions:
        List of version strings this plugin accepts.
    filename:
        Firmware file name.  Defaults to ``"firmware.bin"``.
    include_arch:
        When ``True``, insert the profile's ``arch`` field between
        the version directory and the filename.
    """

    def __init__(
        self,
        distro_root: str | Path,
        os_family: str,
        supported_versions: list[str] | None = None,
        filename: str = "firmware.bin",
        include_arch: bool = False,
    ) -> None:
        self._distro_root = Path(distro_root).resolve()
        _validate_segment(filename, "filename")
        self._os_family = os_family
        self._supported_versions = list(supported_versions or [])
        self._filename = filename
        self._include_arch = include_arch

    @property
    def os_family(self) -> str:
        return self._os_family

    @property
    def supported_versions(self) -> list[str]:
        return list(self._supported_versions)

    def firmware_path(
        self, profile: ProvisionProfile
    ) -> str:
        """Build and validate the firmware file path for *profile*.

        Raises
        ------
        ValueError
            If any path segment contains unsafe characters.
        FileNotFoundError
            If the resolved path does not exist on disk.
        """
        _validate_segment(profile.os_family, "os_family")
        _validate_segment(profile.os_version, "os_version")

        parts: list[str] = [
            profile.os_family,
            profile.os_version,
        ]

        if self._include_arch:
            _validate_segment(profile.arch, "arch")
            parts.append(profile.arch)

        parts.append(self._filename)

        resolved = (self._distro_root / Path(*parts)).resolve()

        # Guard against directory traversal
        if not resolved.is_relative_to(self._distro_root):
            raise ValueError(
                f"resolved path escapes distro_root: {resolved}"
            )

        if not resolved.is_file():
            raise FileNotFoundError(
                f"firmware not found: {resolved}"
            )

        return str(resolved)
