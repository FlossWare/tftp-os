"""OpenWRT firmware plugin for real release-tree directory layouts.

Resolves firmware paths using the OpenWRT target directory convention:

    {distro_root}/openwrt/{version}/targets/{target}/{subtarget}/{filename}

The ``arch`` field on the profile encodes target and subtarget as
``{target}-{subtarget}`` (e.g. ``"ath79-generic"``).  When no ``"-"``
separator is present the arch value is used as the target with a default
subtarget of ``"generic"``.

The filename defaults to ``"{image_type}.bin"`` (where *image_type*
defaults to ``"sysupgrade"``), but can be overridden per-profile via
``extra["filename"]``.

Usage::

    from tftpos.plugins.openwrt import OpenWrtPlugin
    from tftpos.models import ProvisionProfile

    plugin = OpenWrtPlugin(
        distro_root="/srv/tftpos/distros",
        supported_versions=["23.05", "24.10"],
    )
    profile = ProvisionProfile(
        name="router-lab",
        os_family="openwrt",
        os_version="23.05",
        arch="ath79-generic",
        extra={"profile": "tplink_archer-c7-v5"},
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


class OpenWrtPlugin(FirmwarePlugin):
    """Resolve firmware paths via the OpenWRT target directory layout.

    Parameters
    ----------
    distro_root:
        Absolute path to the root directory that holds firmware trees.
    supported_versions:
        List of version strings this plugin accepts.
    image_type:
        Default image type used to build the filename when
        ``extra["filename"]`` is not set.  Defaults to ``"sysupgrade"``.
    """

    def __init__(
        self,
        distro_root: str | Path,
        supported_versions: list[str] | None = None,
        image_type: str = "sysupgrade",
    ) -> None:
        self._distro_root = Path(distro_root).resolve()
        self._supported_versions = list(supported_versions or [])
        _validate_segment(image_type, "image_type")
        self._image_type = image_type

    @property
    def os_family(self) -> str:
        return "openwrt"

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
        _validate_segment(profile.os_version, "os_version")
        _validate_segment(profile.arch, "arch")

        # Split arch into target / subtarget.
        if "-" in profile.arch:
            target, subtarget = profile.arch.split("-", 1)
        else:
            target = profile.arch
            subtarget = "generic"

        _validate_segment(target, "target")
        _validate_segment(subtarget, "subtarget")

        # Determine filename.
        extra = profile.extra if isinstance(profile.extra, dict) else {}
        filename = extra.get("filename")
        if filename:
            _validate_segment(filename, "filename")
        else:
            filename = f"{self._image_type}.bin"

        parts: list[str] = [
            "openwrt",
            profile.os_version,
            "targets",
            target,
            subtarget,
            filename,
        ]

        resolved = (self._distro_root / Path(*parts)).resolve()

        # Guard against directory traversal.
        if not resolved.is_relative_to(self._distro_root):
            raise ValueError(
                f"resolved path escapes distro_root: {resolved}"
            )

        if not resolved.is_file():
            raise FileNotFoundError(
                f"firmware not found: {resolved}"
            )

        return str(resolved)
