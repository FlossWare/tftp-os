"""Hypervisor backend clients."""

from tftpos.client.base import VirtBackend, detect_hypervisor

__all__ = ["VirtBackend", "detect_hypervisor"]
