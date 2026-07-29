"""Abstract base class for VM hypervisor backends."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from typing import Optional


class VirtBackend(ABC):
    """Abstract interface for platform-native VM hypervisor backends.

    Each backend wraps a specific hypervisor's CLI tools to create,
    manage, and PXE-boot virtual machines.
    """

    @abstractmethod
    def create_vm(
        self,
        name: str,
        mac: str,
        memory_mb: int = 2048,
        vcpus: int = 2,
        disk_gb: int = 20,
        bridge: Optional[str] = None,
    ) -> dict:
        """Create a VM configured for PXE boot.

        Returns a dict with at least 'name' and 'mac' keys.
        """

    @abstractmethod
    def start_vm(self, name: str) -> None:
        """Start a stopped VM."""

    @abstractmethod
    def stop_vm(self, name: str) -> None:
        """Force-stop a running VM."""

    @abstractmethod
    def delete_vm(self, name: str) -> None:
        """Delete a VM and its associated resources."""

    @abstractmethod
    def get_vm_status(self, name: str) -> str:
        """Return the current status of a VM (e.g. 'running', 'shut off')."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this hypervisor's tools are available on the system."""

    @property
    @abstractmethod
    def hypervisor_name(self) -> str:
        """Return the human-readable name of the hypervisor."""


def detect_hypervisor() -> Optional[VirtBackend]:
    """Auto-detect the available hypervisor on the current platform.

    Checks for hypervisor CLI tools in order of preference:
    libvirt (virsh), bhyve (bhyvectl), vmm (vmctl), Hyper-V (powershell).

    Returns the first available backend, or None if none found.
    """
    from tftpos.client.libvirt_backend import LibvirtBackend
    from tftpos.client.bhyve_backend import BhyveBackend
    from tftpos.client.vmm_backend import VmmBackend
    from tftpos.client.hyperv_backend import HyperVBackend

    candidates = [
        LibvirtBackend(),
        BhyveBackend(),
        VmmBackend(),
        HyperVBackend(),
    ]

    for backend in candidates:
        if backend.is_available():
            return backend

    return None
