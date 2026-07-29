"""Structured audit logging for provisioning events.

Emits JSON-lines log entries for boot requests, autoinstall fetches,
provision state transitions, profile/host changes, and API key usage.
Log destination is configurable via the ``[audit]`` section in
``tftpos.toml``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional


logger = logging.getLogger("tftpos.audit")


@dataclass
class AuditConfig:
    """Configuration for audit logging (``[audit]`` in tftpos.toml)."""

    enabled: bool = True
    log_file: Optional[Path] = None
    max_bytes: int = 52_428_800  # 50 MB
    backup_count: int = 10
    log_to_stdout: bool = False
    buffer_size: int = 1000  # in-memory ring buffer for API queries
    syslog_enabled: bool = False
    syslog_address: str = "/dev/log"


class AuditEvent:
    """Constants for audit event types."""

    BOOT_REQUEST = "boot_request"
    AUTOINSTALL_FETCH = "autoinstall_fetch"
    STATE_TRANSITION = "state_transition"
    HOST_RULE_CHANGE = "host_rule_change"
    PROFILE_CHANGE = "profile_change"
    API_KEY_CREATED = "api_key_created"
    API_KEY_DELETED = "api_key_deleted"
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    NETBOOT_CHANGE = "netboot_change"
    PROVISION_COMPLETE = "provision_complete"
    PROVISION_FAILED = "provision_failed"
    WEBHOOK_DELIVERY = "webhook_delivery"


def _make_entry(
    event_type: str,
    details: Dict[str, Any],
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a structured audit log entry."""
    entry: Dict[str, Any] = {
        "timestamp": time.time(),
        "event_id": uuid.uuid4().hex[:16],
        "event_type": event_type,
    }
    if client_ip is not None:
        entry["client_ip"] = client_ip
    entry.update(details)
    return entry


class AuditLogger:
    """Structured audit logger that emits JSON-lines entries.

    Writes to a dedicated file (with rotation), optionally to stdout
    and/or syslog, and keeps an in-memory ring buffer for the
    ``GET /api/v1/audit`` query endpoint.
    """

    _instance_counter: int = 0

    def __init__(self, config: Optional[AuditConfig] = None) -> None:
        self._config = config or AuditConfig()
        self._buffer: Deque[Dict[str, Any]] = deque(
            maxlen=self._config.buffer_size
        )
        self._lock = threading.Lock()
        # Use a unique logger name per instance to avoid
        # cross-contamination when multiple AuditLoggers exist
        # (common in tests).
        AuditLogger._instance_counter += 1
        name = f"tftpos.audit.events.{AuditLogger._instance_counter}"
        self._logger = logging.getLogger(name)
        self._logger.propagate = False
        self._logger.setLevel(logging.INFO)
        # Close existing handlers before clearing
        for h in self._logger.handlers:
            h.close()
        self._logger.handlers.clear()
        self._setup_handlers()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def _setup_handlers(self) -> None:
        """Wire up file, stdout, and syslog handlers."""
        formatter = logging.Formatter("%(message)s")

        if self._config.log_file is not None:
            self._config.log_file.parent.mkdir(
                parents=True, exist_ok=True
            )
            fh = logging.handlers.RotatingFileHandler(
                str(self._config.log_file),
                maxBytes=self._config.max_bytes,
                backupCount=self._config.backup_count,
            )
            fh.setFormatter(formatter)
            self._logger.addHandler(fh)

        if self._config.log_to_stdout:
            import sys

            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(formatter)
            self._logger.addHandler(sh)

        if self._config.syslog_enabled:
            address = self._config.syslog_address
            if ":" in address and not address.startswith("/"):
                host, port_str = address.rsplit(":", 1)
                syslog_addr: Any = (host, int(port_str))
            else:
                syslog_addr = address
            slh = logging.handlers.SysLogHandler(address=syslog_addr)
            slh.setFormatter(formatter)
            self._logger.addHandler(slh)

    def log(
        self,
        event_type: str,
        details: Optional[Dict[str, Any]] = None,
        client_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an audit event.

        Returns the full entry dict (useful for testing).
        """
        if not self._config.enabled:
            return {}
        entry = _make_entry(
            event_type, details or {}, client_ip
        )
        line = json.dumps(entry, default=str)
        self._logger.info(line)
        with self._lock:
            self._buffer.append(entry)
        return entry

    # ---- convenience methods ----

    def log_boot_request(
        self,
        mac: str,
        profile: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {"mac": mac}
        if profile:
            details["profile"] = profile
        return self.log(
            AuditEvent.BOOT_REQUEST, details, client_ip
        )

    def log_autoinstall_fetch(
        self,
        mac: str,
        profile: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {"mac": mac}
        if profile:
            details["profile"] = profile
        return self.log(
            AuditEvent.AUTOINSTALL_FETCH, details, client_ip
        )

    def log_state_transition(
        self,
        mac: str,
        old_state: str,
        new_state: str,
        profile: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "mac": mac,
            "old_state": old_state,
            "new_state": new_state,
        }
        if profile:
            details["profile"] = profile
        if error_message:
            details["error_message"] = error_message
        return self.log(AuditEvent.STATE_TRANSITION, details)

    def log_host_rule_change(
        self,
        action: str,
        rule_data: Dict[str, Any],
        client_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "action": action,
            "rule": rule_data,
        }
        return self.log(
            AuditEvent.HOST_RULE_CHANGE, details, client_ip
        )

    def log_auth_event(
        self,
        success: bool,
        key_name: Optional[str] = None,
        role: Optional[str] = None,
        required_role: Optional[str] = None,
        reason: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        event = (
            AuditEvent.AUTH_SUCCESS
            if success
            else AuditEvent.AUTH_FAILURE
        )
        details: Dict[str, Any] = {}
        if key_name:
            details["key_name"] = key_name
        if role:
            details["role"] = role
        if required_role:
            details["required_role"] = required_role
        if reason:
            details["reason"] = reason
        return self.log(event, details, client_ip)

    def log_api_key_change(
        self,
        action: str,
        key_name: str,
        role: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        event = (
            AuditEvent.API_KEY_CREATED
            if action == "created"
            else AuditEvent.API_KEY_DELETED
        )
        details: Dict[str, Any] = {
            "action": action,
            "key_name": key_name,
        }
        if role:
            details["role"] = role
        return self.log(event, details, client_ip)

    def log_webhook_delivery(
        self,
        delivery_id: str,
        webhook_url: str,
        event: str,
        success: bool,
        attempts: int,
        status_code: Optional[int] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "delivery_id": delivery_id,
            "webhook_url": webhook_url,
            "event": event,
            "success": success,
            "attempts": attempts,
        }
        if status_code is not None:
            details["status_code"] = status_code
        if error:
            details["error"] = error
        return self.log(AuditEvent.WEBHOOK_DELIVERY, details)

    def log_netboot_change(
        self,
        mac: str,
        enabled: bool,
        client_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "mac": mac,
            "netboot_enabled": enabled,
        }
        return self.log(
            AuditEvent.NETBOOT_CHANGE, details, client_ip
        )

    # ---- query interface ----

    def query(
        self,
        mac: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query the in-memory audit buffer.

        Parameters
        ----------
        mac:
            Filter by MAC address (case-insensitive substring match).
        event_type:
            Filter by event type.
        since:
            Unix timestamp; only return events after this time.
        limit:
            Maximum number of entries to return (newest first).
        """
        with self._lock:
            entries = list(self._buffer)

        # Apply filters
        if mac is not None:
            mac_lower = mac.lower()
            entries = [
                e for e in entries
                if mac_lower in e.get("mac", "").lower()
            ]
        if event_type is not None:
            entries = [
                e for e in entries
                if e.get("event_type") == event_type
            ]
        if since is not None:
            entries = [
                e for e in entries
                if e.get("timestamp", 0) >= since
            ]

        # Return newest first, limited
        entries.reverse()
        return entries[:limit]

    def buffer_size(self) -> int:
        """Return the current number of entries in the ring buffer."""
        with self._lock:
            return len(self._buffer)


# ---- Module-level singleton ----

_audit_logger: Optional[AuditLogger] = None


def init_audit(config: Optional[AuditConfig] = None) -> AuditLogger:
    """Initialize (or re-initialize) the global audit logger."""
    global _audit_logger
    _audit_logger = AuditLogger(config)
    return _audit_logger


def get_audit_logger() -> AuditLogger:
    """Return the global audit logger, creating a default if needed."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
