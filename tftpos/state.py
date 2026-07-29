"""Provisioning state tracking and callbacks for TftpOS."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from tftpos.db import StorageBackend


class ProvisionState(enum.Enum):
    REGISTERED = "registered"       # Host registered, waiting for PXE boot
    BOOTING = "booting"             # iPXE script served (GET /boot/{mac} was called)
    INSTALLING = "installing"       # Autoinstall config served (GET /autoinstall/{mac} was called)
    POST_INSTALL = "post_install"   # Post-install scripts running
    COMPLETE = "complete"           # Installation finished successfully
    FAILED = "failed"               # Installation failed


@dataclass
class ProvisionRecord:
    mac: str
    profile: str
    os_family: str
    os_version: str
    state: ProvisionState = ProvisionState.REGISTERED
    started_at: Optional[float] = None
    updated_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    history: List[tuple] = field(default_factory=list)  # [(state, timestamp)]
    netboot_enabled: bool = True  # For boot-once support (issue #19)

    def to_dict(self) -> Dict:
        """Serialize the record to a JSON-friendly dictionary."""
        return {
            "mac": self.mac,
            "profile": self.profile,
            "os_family": self.os_family,
            "os_version": self.os_version,
            "state": self.state.value,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "history": [
                {"state": s.value, "timestamp": ts}
                for s, ts in self.history
            ],
            "netboot_enabled": self.netboot_enabled,
        }


class ProvisionTracker:
    """Provisioning state tracker with callbacks and optional persistence.

    When constructed without a *backend*, records are kept purely
    in memory (the original behaviour).  Pass a
    :class:`~tftpos.db.StorageBackend` to persist every state change
    to SQLite, JSON, or any other implementation.
    """

    def __init__(
        self,
        backend: Optional[StorageBackend] = None,
    ) -> None:
        self._backend = backend
        self._records: Dict[str, ProvisionRecord] = {}  # keyed by MAC
        self._callbacks: Dict[ProvisionState, List[Callable]] = {
            s: [] for s in ProvisionState
        }
        # Pre-load from backend so in-memory cache stays consistent
        if self._backend is not None:
            for rec in self._backend.list_all():
                self._records[rec.mac.lower()] = rec

    def register(
        self,
        mac: str,
        profile: str,
        os_family: str,
        os_version: str,
    ) -> ProvisionRecord:
        """Register a new provisioning record."""
        now = time.time()
        record = ProvisionRecord(
            mac=mac,
            profile=profile,
            os_family=os_family,
            os_version=os_version,
            started_at=now,
            updated_at=now,
            history=[(ProvisionState.REGISTERED, now)],
        )
        self._records[mac.lower()] = record
        if self._backend is not None:
            self._backend.save(record)
        self._fire_callbacks(ProvisionState.REGISTERED, record)
        return record

    def transition(
        self,
        mac: str,
        new_state: ProvisionState,
        error_message: Optional[str] = None,
    ) -> ProvisionRecord:
        """Transition a record to a new state."""
        record = self._records.get(mac.lower())
        if not record:
            raise ValueError(f"No provisioning record for {mac}")
        now = time.time()
        record.state = new_state
        record.updated_at = now
        record.history.append((new_state, now))
        if new_state == ProvisionState.COMPLETE:
            record.completed_at = now
        if error_message:
            record.error_message = error_message
        if self._backend is not None:
            self._backend.save(record)
        self._fire_callbacks(new_state, record)
        return record

    def get(self, mac: str) -> Optional[ProvisionRecord]:
        """Get a provisioning record by MAC address."""
        return self._records.get(mac.lower())

    def list_all(self) -> List[ProvisionRecord]:
        """List all provisioning records."""
        return list(self._records.values())

    def on_state_change(
        self, state: ProvisionState, callback: Callable
    ) -> None:
        """Register a callback for a state transition."""
        self._callbacks[state].append(callback)

    def is_netboot_enabled(self, mac: str) -> bool:
        """Return True if netboot is enabled (default) for this MAC.

        Unknown MACs are treated as netboot-enabled.
        """
        record = self.get(mac)
        if record is None:
            return True
        return record.netboot_enabled

    def disable_netboot(self, mac: str) -> ProvisionRecord:
        """Disable netboot for a MAC (boot-once: stop PXE booting)."""
        record = self.get(mac)
        if record is None:
            raise ValueError(f"No provisioning record for {mac}")
        record.netboot_enabled = False
        if self._backend is not None:
            self._backend.save(record)
        return record

    def enable_netboot(self, mac: str) -> ProvisionRecord:
        """Re-enable netboot for a MAC (allow PXE booting again)."""
        record = self.get(mac)
        if record is None:
            raise ValueError(f"No provisioning record for {mac}")
        record.netboot_enabled = True
        if self._backend is not None:
            self._backend.save(record)
        return record

    def clear(self) -> None:
        """Remove all provisioning records."""
        self._records.clear()
        if self._backend is not None:
            self._backend.clear()

    def _fire_callbacks(
        self, state: ProvisionState, record: ProvisionRecord
    ) -> None:
        for cb in self._callbacks.get(state, []):
            cb(record)
