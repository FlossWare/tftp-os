"""Database abstraction layer for TftpOS provisioning state.

Provides a pluggable storage backend so provisioning records can be
persisted to SQLite, PostgreSQL, or MariaDB via SQLAlchemy (default),
JSON files (fallback), or kept purely in memory (for tests).
"""

from __future__ import annotations

import abc
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Index,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    Session,
    declarative_base,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from tftpos.state import ProvisionRecord, ProvisionState

logger = logging.getLogger("tftpos.db")


# ---- SQLAlchemy ORM model ----

Base = declarative_base()


class ProvisionRow(Base):
    """SQLAlchemy ORM model for the provisions table."""

    __tablename__ = "provisions"

    mac = Column(String(17), primary_key=True)
    profile = Column(String(255), nullable=False)
    os_family = Column(String(64), nullable=False)
    os_version = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False)
    started_at = Column(Float, nullable=True)
    updated_at = Column(Float, nullable=True)
    completed_at = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    history = Column(Text, nullable=False, default="[]")
    netboot_enabled = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_provisions_state", "state"),
        Index("idx_provisions_updated", "updated_at"),
    )

    def to_provision_record(self) -> ProvisionRecord:
        """Convert ORM row to a ProvisionRecord dataclass."""
        history_data = json.loads(self.history or "[]")
        history_tuples = [
            (ProvisionState(h["state"]), h["timestamp"])
            for h in history_data
        ]
        return ProvisionRecord(
            mac=self.mac,
            profile=self.profile,
            os_family=self.os_family,
            os_version=self.os_version,
            state=ProvisionState(self.state),
            started_at=self.started_at,
            updated_at=self.updated_at,
            completed_at=self.completed_at,
            error_message=self.error_message,
            history=history_tuples,
            netboot_enabled=bool(self.netboot_enabled),
        )

    @classmethod
    def from_provision_record(
        cls, record: ProvisionRecord
    ) -> "ProvisionRow":
        """Create an ORM row from a ProvisionRecord dataclass."""
        history_json = json.dumps([
            {"state": s.value, "timestamp": ts}
            for s, ts in record.history
        ])
        return cls(
            mac=record.mac.lower(),
            profile=record.profile,
            os_family=record.os_family,
            os_version=record.os_version,
            state=record.state.value,
            started_at=record.started_at,
            updated_at=record.updated_at,
            completed_at=record.completed_at,
            error_message=record.error_message,
            history=history_json,
            netboot_enabled=record.netboot_enabled,
        )


# ---- Abstract interface ----


class StorageBackend(abc.ABC):
    """Abstract base class for provision-record persistence."""

    @abc.abstractmethod
    def save(self, record: ProvisionRecord) -> None:
        """Persist a provision record (insert or update)."""

    @abc.abstractmethod
    def get(self, mac: str) -> Optional[ProvisionRecord]:
        """Retrieve a provision record by MAC address."""

    @abc.abstractmethod
    def list_all(self) -> List[ProvisionRecord]:
        """Return every stored provision record."""

    @abc.abstractmethod
    def delete(self, mac: str) -> bool:
        """Delete a provision record.  Return True if it existed."""

    @abc.abstractmethod
    def clear(self) -> None:
        """Remove all stored provision records."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release any resources held by the backend."""


# ---- Serialisation helpers ----


def _record_to_dict(record: ProvisionRecord) -> Dict[str, Any]:
    """Serialize a ProvisionRecord to a plain dict for storage."""
    return {
        "mac": record.mac,
        "profile": record.profile,
        "os_family": record.os_family,
        "os_version": record.os_version,
        "state": record.state.value,
        "started_at": record.started_at,
        "updated_at": record.updated_at,
        "completed_at": record.completed_at,
        "error_message": record.error_message,
        "history": [
            {"state": s.value, "timestamp": ts}
            for s, ts in record.history
        ],
        "netboot_enabled": record.netboot_enabled,
    }


def _dict_to_record(d: Dict[str, Any]) -> ProvisionRecord:
    """Deserialize a plain dict back into a ProvisionRecord."""
    history = [
        (ProvisionState(h["state"]), h["timestamp"])
        for h in d.get("history", [])
    ]
    return ProvisionRecord(
        mac=d["mac"],
        profile=d["profile"],
        os_family=d["os_family"],
        os_version=d["os_version"],
        state=ProvisionState(d["state"]),
        started_at=d.get("started_at"),
        updated_at=d.get("updated_at"),
        completed_at=d.get("completed_at"),
        error_message=d.get("error_message"),
        history=history,
        netboot_enabled=d.get("netboot_enabled", True),
    )


# ---- SQLAlchemy backend (supports SQLite, PostgreSQL, MariaDB) ----


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Set SQLite-specific pragmas when a new connection is created."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


class SQLAlchemyBackend(StorageBackend):
    """SQLAlchemy-backed storage supporting SQLite, PostgreSQL, and MariaDB.

    Tables are auto-created on first use via ``create_all``.
    Thread-safe via an internal lock.
    """

    def __init__(self, url: str, **engine_kwargs: Any) -> None:
        self._url = url
        self._lock = threading.Lock()

        # For SQLite file paths, ensure parent directory exists
        if url.startswith("sqlite:///") and not url.startswith(
            "sqlite:///:"
        ):
            db_path_str = url.replace("sqlite:///", "", 1)
            if db_path_str:
                Path(db_path_str).parent.mkdir(
                    parents=True, exist_ok=True
                )

        # For SQLite, allow cross-thread usage (matches old
        # sqlite3.connect(check_same_thread=False) behaviour).
        # In-memory databases use StaticPool so all threads share
        # the same connection and see the same tables/data.
        if url.startswith("sqlite"):
            engine_kwargs.setdefault("connect_args", {})
            engine_kwargs["connect_args"].setdefault(
                "check_same_thread", False
            )
            if ":memory:" in url or url == "sqlite://":
                engine_kwargs.setdefault(
                    "poolclass", StaticPool
                )

        self._engine = create_engine(url, **engine_kwargs)

        # Apply SQLite-specific pragmas
        if self._engine.dialect.name == "sqlite":
            event.listen(
                self._engine, "connect", _set_sqlite_pragmas
            )

        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        logger.info(
            "SQLAlchemy backend opened: %s", self._engine.url
        )

    @property
    def engine(self):
        """Expose the underlying SQLAlchemy engine for advanced use."""
        return self._engine

    # -- public API --

    def save(self, record: ProvisionRecord) -> None:
        with self._lock:
            with self._session_factory() as session:
                row = session.get(
                    ProvisionRow, record.mac.lower()
                )
                if row is not None:
                    # Update existing row
                    history_json = json.dumps([
                        {"state": s.value, "timestamp": ts}
                        for s, ts in record.history
                    ])
                    row.profile = record.profile
                    row.os_family = record.os_family
                    row.os_version = record.os_version
                    row.state = record.state.value
                    row.started_at = record.started_at
                    row.updated_at = record.updated_at
                    row.completed_at = record.completed_at
                    row.error_message = record.error_message
                    row.history = history_json
                    row.netboot_enabled = record.netboot_enabled
                else:
                    row = ProvisionRow.from_provision_record(
                        record
                    )
                    session.add(row)
                session.commit()

    def get(self, mac: str) -> Optional[ProvisionRecord]:
        with self._lock:
            with self._session_factory() as session:
                row = session.get(ProvisionRow, mac.lower())
                if row is None:
                    return None
                return row.to_provision_record()

    def list_all(self) -> List[ProvisionRecord]:
        with self._lock:
            with self._session_factory() as session:
                rows = (
                    session.query(ProvisionRow)
                    .order_by(ProvisionRow.updated_at.desc())
                    .all()
                )
                return [r.to_provision_record() for r in rows]

    def delete(self, mac: str) -> bool:
        with self._lock:
            with self._session_factory() as session:
                row = session.get(ProvisionRow, mac.lower())
                if row is None:
                    return False
                session.delete(row)
                session.commit()
                return True

    def clear(self) -> None:
        with self._lock:
            with self._session_factory() as session:
                session.query(ProvisionRow).delete()
                session.commit()

    def close(self) -> None:
        with self._lock:
            self._engine.dispose()
        logger.info(
            "SQLAlchemy backend closed: %s", self._url
        )

    def query_by_state(
        self, state: ProvisionState
    ) -> List[ProvisionRecord]:
        """Return all records in the given state (uses index)."""
        with self._lock:
            with self._session_factory() as session:
                rows = (
                    session.query(ProvisionRow)
                    .filter(
                        ProvisionRow.state == state.value
                    )
                    .order_by(ProvisionRow.updated_at.desc())
                    .all()
                )
                return [
                    r.to_provision_record() for r in rows
                ]


class SQLiteBackend(SQLAlchemyBackend):
    """SQLite-backed storage (convenience wrapper around SQLAlchemyBackend).

    Accepts a :class:`pathlib.Path` for backward compatibility with
    existing code that passes ``db_path`` directly.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        url = f"sqlite:///{db_path}"
        super().__init__(url)


# ---- JSON file backend (fallback) ----


class JSONBackend(StorageBackend):
    """JSON-file-backed storage.

    Reads/writes the entire state on every operation.  Simple but
    not concurrent-safe -- use SQLiteBackend for production.
    """

    def __init__(self, json_path: Path) -> None:
        self._path = json_path
        self._lock = threading.Lock()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("JSON backend opened: %s", json_path)

    def save(self, record: ProvisionRecord) -> None:
        with self._lock:
            data = self._load()
            data[record.mac.lower()] = _record_to_dict(record)
            self._dump(data)

    def get(self, mac: str) -> Optional[ProvisionRecord]:
        with self._lock:
            data = self._load()
        entry = data.get(mac.lower())
        if entry is None:
            return None
        return _dict_to_record(entry)

    def list_all(self) -> List[ProvisionRecord]:
        with self._lock:
            data = self._load()
        return [_dict_to_record(v) for v in data.values()]

    def delete(self, mac: str) -> bool:
        with self._lock:
            data = self._load()
            if mac.lower() in data:
                del data[mac.lower()]
                self._dump(data)
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._dump({})

    def close(self) -> None:
        pass  # nothing to release

    # -- internal --

    def _load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text()
            return json.loads(text) if text.strip() else {}
        except (json.JSONDecodeError, OSError):
            logger.warning(
                "Failed to read JSON state file: %s", self._path,
            )
            return {}

    def _dump(self, data: Dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, indent=2))


# ---- In-memory backend (for tests / ephemeral use) ----


class MemoryBackend(StorageBackend):
    """Purely in-memory backend -- no persistence."""

    def __init__(self) -> None:
        self._records: Dict[str, ProvisionRecord] = {}

    def save(self, record: ProvisionRecord) -> None:
        self._records[record.mac.lower()] = record

    def get(self, mac: str) -> Optional[ProvisionRecord]:
        return self._records.get(mac.lower())

    def list_all(self) -> List[ProvisionRecord]:
        return list(self._records.values())

    def delete(self, mac: str) -> bool:
        return self._records.pop(mac.lower(), None) is not None

    def clear(self) -> None:
        self._records.clear()

    def close(self) -> None:
        pass


# ---- Migration helpers ----


def migrate_json_to_sqlite(
    json_path: Path,
    db_path: Path,
) -> int:
    """Import records from a JSON state file into a SQLite database.

    Returns the number of records migrated.
    """
    if not json_path.exists():
        raise FileNotFoundError(
            f"State file not found: {json_path}"
        )

    text = json_path.read_text()
    raw = json.loads(text) if text.strip() else {}

    # The JSON file might be a flat dict keyed by MAC, or a list.
    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        records = list(raw.values())
    else:
        raise ValueError(
            f"Unexpected JSON structure in {json_path}"
        )

    backend = SQLiteBackend(db_path)
    count = 0
    for entry in records:
        try:
            record = _dict_to_record(entry)
            backend.save(record)
            count += 1
        except (KeyError, ValueError) as exc:
            logger.warning(
                "Skipping malformed record: %s (%s)", entry, exc,
            )
    backend.close()
    return count
