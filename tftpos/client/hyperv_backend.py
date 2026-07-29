"""Windows Hyper-V backend using PowerShell cmdlets."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional

from tftpos.client.base import VirtBackend

logger = logging.getLogger("tftpos.client.hyperv")


class HyperVBackend(VirtBackend):
    """Backend wrapping PowerShell Hyper-V cmdlets for Windows VMs."""

    @property
    def hypervisor_name(self) -> str:
        return "hyperv"

    def is_available(self) -> bool:
        """Check if PowerShell and Hyper-V module are available."""
        ps = shutil.which("powershell.exe") or shutil.which("pwsh")
        if ps is None:
            return False

        # Check if Hyper-V module is available
        try:
            result = subprocess.run(
                [ps, "-NoProfile", "-Command",
                 "Get-Module -ListAvailable Hyper-V | Select-Object -First 1"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0 and "Hyper-V" in result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _ps_cmd(self) -> str:
        """Return the PowerShell executable path."""
        return shutil.which("powershell.exe") or shutil.which("pwsh") or "powershell.exe"

    def create_vm(
        self,
        name: str,
        mac: str,
        memory_mb: int = 2048,
        vcpus: int = 2,
        disk_gb: int = 20,
        bridge: Optional[str] = None,
    ) -> dict:
        """Create a Hyper-V VM configured for PXE boot."""
        bridge = bridge or "Default Switch"
        memory_bytes = memory_mb * 1024 * 1024
        disk_bytes = disk_gb * 1024 * 1024 * 1024

        ps = self._ps_cmd()

        # Create VM
        create_cmd = (
            f"New-VM -Name '{name}' "
            f"-MemoryStartupBytes {memory_bytes} "
            f"-Generation 1 "
            f"-SwitchName '{bridge}' "
            f"-NewVHDPath 'C:\\Hyper-V\\{name}\\{name}.vhdx' "
            f"-NewVHDSizeBytes {disk_bytes}"
        )
        logger.info("Creating Hyper-V VM %s", name)
        subprocess.run(
            [ps, "-NoProfile", "-Command", create_cmd],
            check=True, capture_output=True, text=True,
        )

        # Set processor count
        subprocess.run(
            [ps, "-NoProfile", "-Command",
             f"Set-VMProcessor -VMName '{name}' -Count {vcpus}"],
            check=True, capture_output=True, text=True,
        )

        # Set static MAC address
        mac_no_colons = mac.replace(":", "").replace("-", "")
        subprocess.run(
            [ps, "-NoProfile", "-Command",
             f"Set-VMNetworkAdapter -VMName '{name}' "
             f"-StaticMacAddress '{mac_no_colons}'"],
            check=True, capture_output=True, text=True,
        )

        return {"name": name, "mac": mac, "bridge": bridge}

    def start_vm(self, name: str) -> None:
        """Start a Hyper-V VM."""
        ps = self._ps_cmd()
        subprocess.run(
            [ps, "-NoProfile", "-Command", f"Start-VM -Name '{name}'"],
            check=True, capture_output=True, text=True,
        )

    def stop_vm(self, name: str) -> None:
        """Force-stop a Hyper-V VM."""
        ps = self._ps_cmd()
        subprocess.run(
            [ps, "-NoProfile", "-Command",
             f"Stop-VM -Name '{name}' -Force -TurnOff"],
            check=True, capture_output=True, text=True,
        )

    def delete_vm(self, name: str) -> None:
        """Delete a Hyper-V VM and its files."""
        ps = self._ps_cmd()

        # Stop if running (ignore errors)
        subprocess.run(
            [ps, "-NoProfile", "-Command",
             f"Stop-VM -Name '{name}' -Force -TurnOff -ErrorAction SilentlyContinue"],
            capture_output=True, text=True,
        )

        # Remove VM
        subprocess.run(
            [ps, "-NoProfile", "-Command",
             f"Remove-VM -Name '{name}' -Force"],
            check=True, capture_output=True, text=True,
        )

    def get_vm_status(self, name: str) -> str:
        """Get VM status using Get-VM."""
        ps = self._ps_cmd()
        result = subprocess.run(
            [ps, "-NoProfile", "-Command",
             f"(Get-VM -Name '{name}').State"],
            check=True, capture_output=True, text=True,
        )
        return result.stdout.strip().lower()
