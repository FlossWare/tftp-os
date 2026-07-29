"""Structured logging configuration for TftpOS.

Provides centralized logging setup with support for:
- Console, file (with rotation), syslog, and journald handlers
- JSON structured output for log aggregation
- Per-request correlation IDs
- Backward-compatible ``setup_logging()`` entry point
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# ---- Correlation ID support ----

_correlation_id: ContextVar[Optional[str]] = ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> Optional[str]:
    """Return the current correlation ID (if any)."""
    return _correlation_id.get()


def set_correlation_id(cid: Optional[str] = None) -> str:
    """Set (or generate) a correlation ID for the current context.

    Returns the correlation ID that was set.
    """
    if cid is None:
        cid = uuid.uuid4().hex[:16]
    _correlation_id.set(cid)
    return cid


def reset_correlation_id() -> None:
    """Clear the current correlation ID."""
    _correlation_id.set(None)


# ---- Logging config dataclass ----


@dataclass
class LoggingConfig:
    """Logging configuration consumed by ``configure_logging()``."""

    level: str = "INFO"
    json_format: bool = False
    log_file: Optional[Path] = None
    max_bytes: int = 10_485_760  # 10 MB
    backup_count: int = 5
    syslog_enabled: bool = False
    syslog_address: str = "/dev/log"
    journald_enabled: bool = False


# ---- Formatters ----


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Attach correlation ID when available
        cid = get_correlation_id()
        if cid is not None:
            log_entry["correlation_id"] = cid

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(
                record.exc_info
            )

        if hasattr(record, "extra_data"):
            log_entry["extra"] = record.extra_data

        return json.dumps(log_entry, default=str)


class CorrelationFormatter(logging.Formatter):
    """Standard text formatter that appends the correlation ID."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        cid = get_correlation_id()
        if cid is not None:
            return f"{base} cid={cid}"
        return base


_DEFAULT_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s %(message)s"
)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _resolve_level(level: str) -> str:
    """Normalize and validate a log level string."""
    level = level.upper()
    if level not in _VALID_LEVELS:
        level = "INFO"
    return level


# ---- Handler factories ----


def _make_stream_handler(
    level: int,
    json_format: bool,
    stream: Optional[object] = None,
) -> logging.StreamHandler:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(level)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            CorrelationFormatter(_DEFAULT_FORMAT)
        )
    return handler


def _make_file_handler(
    path: Path,
    level: int,
    max_bytes: int,
    backup_count: int,
    json_format: bool,
) -> logging.handlers.RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        str(path),
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    handler.setLevel(level)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            CorrelationFormatter(_DEFAULT_FORMAT)
        )
    return handler


def _make_syslog_handler(
    address: str,
    level: int,
    json_format: bool,
) -> logging.handlers.SysLogHandler:
    # Determine socket type vs. (host, port)
    if ":" in address and not address.startswith("/"):
        host, port_str = address.rsplit(":", 1)
        syslog_addr: Any = (host, int(port_str))
    else:
        syslog_addr = address

    handler = logging.handlers.SysLogHandler(address=syslog_addr)
    handler.setLevel(level)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "tftpos: %(levelname)s %(name)s %(message)s"
        ))
    return handler


def _make_journald_handler(
    level: int,
    json_format: bool,
) -> Optional[logging.Handler]:
    """Try to create a systemd journal handler.

    Returns ``None`` when the ``systemd.journal`` module is not
    available (non-systemd systems).
    """
    try:
        from systemd.journal import JournalHandler  # type: ignore[import-untyped]

        handler = JournalHandler(SYSLOG_IDENTIFIER="tftpos")
        handler.setLevel(level)
        if json_format:
            handler.setFormatter(JsonFormatter())
        return handler
    except ImportError:
        return None


# ---- Public API ----


def configure_logging(config: LoggingConfig) -> None:
    """Configure the ``tftpos`` logger hierarchy.

    Accepts a :class:`LoggingConfig` dataclass and wires up the
    requested handlers (console, file, syslog, journald).
    """
    level_name = _resolve_level(config.level)
    level = getattr(logging, level_name)

    root_logger = logging.getLogger("tftpos")
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()

    # Always add a stderr console handler
    root_logger.addHandler(
        _make_stream_handler(level, config.json_format)
    )

    # Optional file handler with rotation
    if config.log_file is not None:
        root_logger.addHandler(
            _make_file_handler(
                config.log_file,
                level,
                config.max_bytes,
                config.backup_count,
                config.json_format,
            )
        )

    # Optional syslog handler
    if config.syslog_enabled:
        root_logger.addHandler(
            _make_syslog_handler(
                config.syslog_address,
                level,
                config.json_format,
            )
        )

    # Optional journald handler
    if config.journald_enabled:
        jh = _make_journald_handler(level, config.json_format)
        if jh is not None:
            root_logger.addHandler(jh)


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    stream: Optional[object] = None,
) -> None:
    """Configure the root tftpos logger (backward-compatible API).

    Parameters
    ----------
    level:
        Log level name (DEBUG, INFO, WARNING, ERROR).
    json_format:
        If True, emit JSON-formatted log lines.
    stream:
        Output stream (defaults to sys.stderr).
    """
    level_name = _resolve_level(level)
    numeric_level = getattr(logging, level_name)

    root_logger = logging.getLogger("tftpos")
    root_logger.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()

    handler = _make_stream_handler(
        numeric_level, json_format, stream
    )
    root_logger.addHandler(handler)
