"""OpenBSD vmm(4)/vmd backend using vmctl CLI."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional

from tftpos.client.base import VirtBackend

logger = logging.getLogger("tftpos.client.vmm")


class VmmBackend(VirtBackend):
    """Backend wrapping vmctl for OpenBSD vmm(4)/vmd virtual machines."""

    @property
    def hypervisor_name(self) -> str:
        return "vmm"

    def is_available(self) -> bool:
        """Check if vmctl is available in PATH."""
        return shutil.which("vmctl") is not None

    def create_vm(
        self,
        name: str,
        mac: str,
        memory_mb: int = 2048,
        vcpus: int = 2,
        disk_gb: int = 20,
        bridge: Optional[str] = None,
    ) -> dict:
        """Create an OpenBSD VM with vmctl configured for PXE boot."""
        bridge = bridge or "vswitch0"
        disk_path = f"/var/vm/{name}.qcow2"

        logger.info("Creating VM disk %s (%dG)", disk_path, disk_gb)

        # Create the disk image
        subprocess.run(
            ["vmctl", "create", "-s", f"{disk_gb}G", disk_path],
            check=True, capture_output=True, text=True,
        )

        return {
            "name": name,
            "mac": mac,
            "bridge": bridge,
            "disk": disk_path,
        }

    def start_vm(self, name: str) -> None:
        """Start a VM using vmctl with PXE boot."""
        disk_path = f"/var/vm/{name}.qcow2"

        subprocess.run(
            ["vmctl", "start", name,
             "-d", disk_path,
             "-n", "vswitch0",
             "-B", "net"],
            check=True, capture_output=True, text=True,
        )

    def stop_vm(self, name: str) -> None:
        """Stop a VM using vmctl."""
        subprocess.run(
            ["vmctl", "stop", name, "-f"],
            check=True, capture_output=True, text=True,
        )

    def delete_vm(self, name: str) -> None:
        """Delete a VM by stopping it and removing its disk."""
        # Stop first (ignore errors if already stopped)
        subprocess.run(
            ["vmctl", "stop", name, "-f"],
            capture_output=True, text=True,
        )

        # Remove disk image
        disk_path = f"/var/vm/{name}.qcow2"
        import os
        try:
            os.unlink(disk_path)
        except FileNotFoundError:
            pass

    def get_vm_status(self, name: str) -> str:
        """Get VM status using vmctl status."""
        result = subprocess.run(
            ["vmctl", "status"],
            check=True, capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and name in parts:
                # vmctl status output has columns: ID PID VCPUS MAXMEM ... NAME
                # The state is implied by whether it has a PID
                if len(parts) >= 6:
                    pid = parts[1]
                    if pid != "-" and pid.isdigit():
                        return "running"
                    return "stopped"
        return "not found"
