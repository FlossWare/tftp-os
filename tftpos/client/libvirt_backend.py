"""Libvirt/KVM/QEMU backend using virt-install and virsh CLI tools."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional

from tftpos.client.base import VirtBackend

logger = logging.getLogger("tftpos.client.libvirt")


class LibvirtBackend(VirtBackend):
    """Backend wrapping virt-install and virsh for KVM/QEMU VMs."""

    @property
    def hypervisor_name(self) -> str:
        return "libvirt"

    def is_available(self) -> bool:
        """Check if virsh is available in PATH."""
        return shutil.which("virsh") is not None

    def create_vm(
        self,
        name: str,
        mac: str,
        memory_mb: int = 2048,
        vcpus: int = 2,
        disk_gb: int = 20,
        bridge: Optional[str] = None,
    ) -> dict:
        """Create a KVM/QEMU VM configured for PXE boot using virt-install."""
        bridge = bridge or "virbr0"

        cmd = [
            "virt-install",
            "--name", name,
            "--memory", str(memory_mb),
            "--vcpus", str(vcpus),
            "--disk", f"size={disk_gb}",
            "--network", f"bridge={bridge},mac={mac}",
            "--pxe",
            "--os-variant", "generic",
            "--noautoconsole",
            "--noreboot",
        ]

        logger.info("Creating VM %s: %s", name, " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        return {"name": name, "mac": mac, "bridge": bridge}

    def start_vm(self, name: str) -> None:
        """Start a VM using virsh."""
        subprocess.run(
            ["virsh", "start", name],
            check=True, capture_output=True, text=True,
        )

    def stop_vm(self, name: str) -> None:
        """Force-stop a VM using virsh destroy."""
        subprocess.run(
            ["virsh", "destroy", name],
            check=True, capture_output=True, text=True,
        )

    def delete_vm(self, name: str) -> None:
        """Delete a VM and its storage using virsh undefine."""
        subprocess.run(
            ["virsh", "undefine", name, "--remove-all-storage"],
            check=True, capture_output=True, text=True,
        )

    def get_vm_status(self, name: str) -> str:
        """Get VM status using virsh domstate."""
        result = subprocess.run(
            ["virsh", "domstate", name],
            check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()
