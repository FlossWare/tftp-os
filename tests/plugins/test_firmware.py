"""Minimal firmware plugin for end-to-end testing.

Copies a pre-built iPXE binary (undionly.kpxe) into the TFTP root directory.
Used by integration tests to verify the full provisioning pipeline:
config -> host matching -> plugin -> firmware file -> TFTP serve -> VM boot.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tftpos.models import ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin

IPXE_SYSTEM_PATH = Path("/usr/share/ipxe/undionly.kpxe")


class E2EFirmwarePlugin(FirmwarePlugin):
    """Plugin that serves undionly.kpxe for PXE boot testing."""

    def __init__(
        self,
        tftp_root: Path | None = None,
        ipxe_source: Path | None = None,
    ):
        self._tftp_root = Path(tftp_root) if tftp_root else Path("/srv/tftp")
        self._ipxe_source = ipxe_source or IPXE_SYSTEM_PATH

    def configure(self, tftp_root: Path, ipxe_source: Path | None = None) -> None:
        self._tftp_root = Path(tftp_root)
        if ipxe_source:
            self._ipxe_source = ipxe_source

    @property
    def os_family(self) -> str:
        return "test"

    @property
    def supported_versions(self) -> list[str]:
        return ["1.0"]

    def firmware_path(self, profile: ProvisionProfile) -> str:
        dest = self._tftp_root / "undionly.kpxe"
        if not dest.exists():
            self._tftp_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._ipxe_source, dest)
        marker = self._tftp_root / "test" / "marker.txt"
        if not marker.exists():
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("TFTPOS_E2E_OK\n")
        return str(dest)
