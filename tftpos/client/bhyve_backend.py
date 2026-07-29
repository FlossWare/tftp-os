"""FreeBSD bhyve backend using bhyvectl/bhyve or vm-bhyve CLI tools."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional

from tftpos.client.base import VirtBackend

logger = logging.getLogger("tftpos.client.bhyve")


class BhyveBackend(VirtBackend):
    """Backend wrapping bhyve/bhyvectl for FreeBSD VMs.

    Prefers vm-bhyve (``vm`` command) when available, falling back
    to raw bhyve/bhyvectl commands.
    """

    @property
    def hypervisor_name(self) -> str:
        return "bhyve"

    def is_available(self) -> bool:
        """Check if bhyvectl is available in PATH."""
        return shutil.which("bhyvectl") is not None

    def _has_vm_bhyve(self) -> bool:
        """Check if the vm-bhyve ``vm`` wrapper is available."""
        return shutil.which("vm") is not None

    def create_vm(
        self,
        name: str,
        mac: str,
        memory_mb: int = 2048,
        vcpus: int = 2,
        disk_gb: int = 20,
        bridge: Optional[str] = None,
    ) -> dict:
        """Create a bhyve VM configured for PXE boot."""
        bridge = bridge or "bridge0"

        if self._has_vm_bhyve():
            return self._create_with_vm_bhyve(
                name, mac, memory_mb, vcpus, disk_gb, bridge,
            )
        return self._create_with_bhyve(
            name, mac, memory_mb, vcpus, disk_gb, bridge,
        )

    def _create_with_vm_bhyve(
        self,
        name: str,
        mac: str,
        memory_mb: int,
        vcpus: int,
        disk_gb: int,
        bridge: str,
    ) -> dict:
        """Create VM using vm-bhyve wrapper."""
        logger.info("Creating VM %s with vm-bhyve", name)

        # Create the VM
        subprocess.run(
            ["vm", "create", "-m", str(memory_mb), "-c", str(vcpus),
             "-s", f"{disk_gb}G", name],
            check=True, capture_output=True, text=True,
        )

        return {"name": name, "mac": mac, "bridge": bridge}

    def _create_with_bhyve(
        self,
        name: str,
        mac: str,
        memory_mb: int,
        vcpus: int,
        disk_gb: int,
        bridge: str,
    ) -> dict:
        """Create VM using raw bhyve commands."""
        logger.info("Creating VM %s with raw bhyve", name)

        # Create disk image
        disk_path = f"/vm/{name}/disk0.img"
        subprocess.run(
            ["truncate", "-s", f"{disk_gb}G", disk_path],
            check=True, capture_output=True, text=True,
        )

        # Create a tap interface for networking
        result = subprocess.run(
            ["ifconfig", "tap", "create"],
            check=True, capture_output=True, text=True,
        )
        tap_dev = result.stdout.strip()

        # Add tap to bridge
        subprocess.run(
            ["ifconfig", bridge, "addm", tap_dev],
            check=True, capture_output=True, text=True,
        )

        return {
            "name": name,
            "mac": mac,
            "bridge": bridge,
            "disk": disk_path,
            "tap": tap_dev,
        }

    def start_vm(self, name: str) -> None:
        """Start a bhyve VM."""
        if self._has_vm_bhyve():
            subprocess.run(
                ["vm", "start", name],
                check=True, capture_output=True, text=True,
            )
        else:
            subprocess.run(
                ["bhyve", "-A", "-H", "-P", "-s", "0:0,hostbridge",
                 "-l", "bootrom,/usr/local/share/uefi-firmware/BHYVE_UEFI.fd",
                 name],
                check=True, capture_output=True, text=True,
            )

    def stop_vm(self, name: str) -> None:
        """Stop a bhyve VM."""
        if self._has_vm_bhyve():
            subprocess.run(
                ["vm", "stop", name],
                check=True, capture_output=True, text=True,
            )
        else:
            subprocess.run(
                ["bhyvectl", "--destroy", f"--vm={name}"],
                check=True, capture_output=True, text=True,
            )

    def delete_vm(self, name: str) -> None:
        """Delete a bhyve VM."""
        if self._has_vm_bhyve():
            subprocess.run(
                ["vm", "destroy", name],
                check=True, capture_output=True, text=True,
            )
        else:
            # Destroy the VM instance and clean up
            subprocess.run(
                ["bhyvectl", "--destroy", f"--vm={name}"],
                capture_output=True, text=True,
            )

    def get_vm_status(self, name: str) -> str:
        """Get VM status."""
        if self._has_vm_bhyve():
            result = subprocess.run(
                ["vm", "list"],
                check=True, capture_output=True, text=True,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts and parts[0] == name:
                    return parts[-1] if len(parts) > 1 else "unknown"
            return "not found"
        else:
            result = subprocess.run(
                ["bhyvectl", f"--vm={name}", "--get-all"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return "running"
            return "stopped"
