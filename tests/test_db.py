"""Tests for the database abstraction layer (tftpos.db)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from tftpos.db import (
    Base,
    JSONBackend,
    MemoryBackend,
    ProvisionRow,
    SQLAlchemyBackend,
    SQLiteBackend,
    StorageBackend,
    _dict_to_record,
    _record_to_dict,
    migrate_json_to_sqlite,
)
from tftpos.state import ProvisionRecord, ProvisionState, ProvisionTracker


# ---- Helpers ----


def _make_record(
    mac: str = "aa:bb:cc:dd:ee:ff",
    profile: str = "fedora-server",
    os_family: str = "fedora",
    os_version: str = "41",
    state: ProvisionState = ProvisionState.REGISTERED,
) -> ProvisionRecord:
    now = time.time()
    return ProvisionRecord(
        mac=mac,
        profile=profile,
        os_family=os_family,
        os_version=os_version,
        state=state,
        started_at=now,
        updated_at=now,
        history=[(state, now)],
    )


# ---- Serialisation round-trip ----


class TestSerialisation:

    def test_record_to_dict_and_back(self):
        original = _make_record()
        d = _record_to_dict(original)
        restored = _dict_to_record(d)
        assert restored.mac == original.mac
        assert restored.profile == original.profile
        assert restored.state == original.state
        assert restored.started_at == original.started_at
        assert restored.netboot_enabled == original.netboot_enabled
        assert len(restored.history) == len(original.history)

    def test_round_trip_with_error_message(self):
        rec = _make_record(state=ProvisionState.FAILED)
        rec.error_message = "disk not found"
        d = _record_to_dict(rec)
        restored = _dict_to_record(d)
        assert restored.error_message == "disk not found"

    def test_round_trip_preserves_netboot_false(self):
        rec = _make_record()
        rec.netboot_enabled = False
        d = _record_to_dict(rec)
        restored = _dict_to_record(d)
        assert restored.netboot_enabled is False

    def test_round_trip_preserves_history(self):
        rec = _make_record()
        now = time.time()
        rec.history.append((ProvisionState.BOOTING, now))
        d = _record_to_dict(rec)
        restored = _dict_to_record(d)
        assert len(restored.history) == 2
        assert restored.history[1][0] == ProvisionState.BOOTING

    def test_dict_to_record_defaults_netboot_true(self):
        d = {
            "mac": "aa:bb:cc:dd:ee:ff",
            "profile": "p",
            "os_family": "fedora",
            "os_version": "41",
            "state": "registered",
        }
        rec = _dict_to_record(d)
        assert rec.netboot_enabled is True


# ---- StorageBackend contract tests ----
# Run the same battery for each concrete backend.


class _BackendContractTests:
    """Mixin: the concrete test class must set self.backend."""

    backend: StorageBackend

    def test_save_and_get(self):
        rec = _make_record()
        self.backend.save(rec)
        got = self.backend.get("aa:bb:cc:dd:ee:ff")
        assert got is not None
        assert got.mac == rec.mac
        assert got.profile == rec.profile

    def test_get_unknown_returns_none(self):
        assert self.backend.get("ff:ff:ff:ff:ff:ff") is None

    def test_get_is_case_insensitive(self):
        rec = _make_record(mac="AA:BB:CC:DD:EE:FF")
        self.backend.save(rec)
        got = self.backend.get("aa:bb:cc:dd:ee:ff")
        assert got is not None

    def test_list_all_empty(self):
        assert self.backend.list_all() == []

    def test_list_all_returns_saved_records(self):
        self.backend.save(_make_record(mac="aa:bb:cc:dd:ee:01"))
        self.backend.save(_make_record(mac="aa:bb:cc:dd:ee:02"))
        records = self.backend.list_all()
        assert len(records) == 2

    def test_save_overwrites_existing(self):
        self.backend.save(
            _make_record(mac="aa:bb:cc:dd:ee:ff", profile="p1")
        )
        self.backend.save(
            _make_record(mac="aa:bb:cc:dd:ee:ff", profile="p2")
        )
        got = self.backend.get("aa:bb:cc:dd:ee:ff")
        assert got is not None
        assert got.profile == "p2"
        assert len(self.backend.list_all()) == 1

    def test_delete_existing(self):
        self.backend.save(_make_record())
        assert self.backend.delete("aa:bb:cc:dd:ee:ff") is True
        assert self.backend.get("aa:bb:cc:dd:ee:ff") is None

    def test_delete_nonexistent(self):
        assert self.backend.delete("ff:ff:ff:ff:ff:ff") is False

    def test_clear_removes_all(self):
        self.backend.save(_make_record(mac="aa:bb:cc:dd:ee:01"))
        self.backend.save(_make_record(mac="aa:bb:cc:dd:ee:02"))
        self.backend.clear()
        assert self.backend.list_all() == []

    def test_save_preserves_state(self):
        rec = _make_record(state=ProvisionState.INSTALLING)
        self.backend.save(rec)
        got = self.backend.get(rec.mac)
        assert got is not None
        assert got.state == ProvisionState.INSTALLING

    def test_save_preserves_error_message(self):
        rec = _make_record(state=ProvisionState.FAILED)
        rec.error_message = "timeout"
        self.backend.save(rec)
        got = self.backend.get(rec.mac)
        assert got is not None
        assert got.error_message == "timeout"

    def test_save_preserves_completed_at(self):
        rec = _make_record(state=ProvisionState.COMPLETE)
        rec.completed_at = time.time()
        self.backend.save(rec)
        got = self.backend.get(rec.mac)
        assert got is not None
        assert got.completed_at == rec.completed_at

    def test_save_preserves_netboot_enabled(self):
        rec = _make_record()
        rec.netboot_enabled = False
        self.backend.save(rec)
        got = self.backend.get(rec.mac)
        assert got is not None
        assert got.netboot_enabled is False

    def test_save_preserves_history(self):
        rec = _make_record()
        now = time.time()
        rec.history.append((ProvisionState.BOOTING, now))
        rec.history.append((ProvisionState.INSTALLING, now + 1))
        self.backend.save(rec)
        got = self.backend.get(rec.mac)
        assert got is not None
        assert len(got.history) == 3
        assert got.history[1][0] == ProvisionState.BOOTING
        assert got.history[2][0] == ProvisionState.INSTALLING


# ---- Concrete backend tests ----


class TestMemoryBackend(_BackendContractTests):

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.backend = MemoryBackend()

    def test_close_is_noop(self):
        self.backend.close()  # should not raise


class TestSQLiteBackend(_BackendContractTests):

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.backend = SQLiteBackend(tmp_path / "test.db")
        yield
        self.backend.close()

    def test_persists_across_instances(self, tmp_path):
        db_path = tmp_path / "persist.db"
        b1 = SQLiteBackend(db_path)
        b1.save(_make_record(mac="aa:bb:cc:dd:ee:ff"))
        b1.close()

        b2 = SQLiteBackend(db_path)
        got = b2.get("aa:bb:cc:dd:ee:ff")
        assert got is not None
        assert got.mac == "aa:bb:cc:dd:ee:ff"
        b2.close()

    def test_query_by_state(self, tmp_path):
        db_path = tmp_path / "query.db"
        b = SQLiteBackend(db_path)
        b.save(_make_record(mac="aa:bb:cc:dd:ee:01",
                            state=ProvisionState.BOOTING))
        b.save(_make_record(mac="aa:bb:cc:dd:ee:02",
                            state=ProvisionState.COMPLETE))
        b.save(_make_record(mac="aa:bb:cc:dd:ee:03",
                            state=ProvisionState.BOOTING))
        booting = b.query_by_state(ProvisionState.BOOTING)
        assert len(booting) == 2
        complete = b.query_by_state(ProvisionState.COMPLETE)
        assert len(complete) == 1
        b.close()

    def test_concurrent_writes(self, tmp_path):
        """10+ simultaneous provisions must not corrupt the database."""
        db_path = tmp_path / "concurrent.db"
        b = SQLiteBackend(db_path)
        errors: list = []

        def _writer(i: int) -> None:
            try:
                mac = f"aa:bb:cc:dd:{i:02x}:00"
                rec = _make_record(mac=mac, profile=f"p{i}")
                b.save(rec)
                # Transition through states
                rec.state = ProvisionState.BOOTING
                rec.updated_at = time.time()
                rec.history.append(
                    (ProvisionState.BOOTING, rec.updated_at)
                )
                b.save(rec)
                rec.state = ProvisionState.COMPLETE
                rec.updated_at = time.time()
                rec.completed_at = rec.updated_at
                rec.history.append(
                    (ProvisionState.COMPLETE, rec.updated_at)
                )
                b.save(rec)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_writer, args=(i,))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent write errors: {errors}"
        records = b.list_all()
        assert len(records) == 20
        for rec in records:
            assert rec.state == ProvisionState.COMPLETE
        b.close()


class TestJSONBackend(_BackendContractTests):

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.backend = JSONBackend(tmp_path / "state.json")
        yield

    def test_persists_to_file(self, tmp_path):
        json_path = tmp_path / "persist.json"
        b1 = JSONBackend(json_path)
        b1.save(_make_record(mac="aa:bb:cc:dd:ee:ff"))

        b2 = JSONBackend(json_path)
        got = b2.get("aa:bb:cc:dd:ee:ff")
        assert got is not None

    def test_handles_missing_file(self, tmp_path):
        b = JSONBackend(tmp_path / "nonexistent.json")
        assert b.list_all() == []
        assert b.get("aa:bb:cc:dd:ee:ff") is None

    def test_handles_corrupt_file(self, tmp_path):
        json_path = tmp_path / "corrupt.json"
        json_path.write_text("not valid json{{{")
        b = JSONBackend(json_path)
        assert b.list_all() == []

    def test_close_is_noop(self, tmp_path):
        b = JSONBackend(tmp_path / "noop.json")
        b.close()  # should not raise


# ---- Migration tests ----


class TestMigration:

    def test_migrate_dict_format(self, tmp_path):
        state = {
            "aa:bb:cc:dd:ee:ff": {
                "mac": "aa:bb:cc:dd:ee:ff",
                "profile": "fedora-server",
                "os_family": "fedora",
                "os_version": "41",
                "state": "registered",
                "started_at": 1000.0,
                "updated_at": 1000.0,
                "history": [
                    {"state": "registered", "timestamp": 1000.0}
                ],
            }
        }
        json_path = tmp_path / "state.json"
        json_path.write_text(json.dumps(state))
        db_path = tmp_path / "state.db"

        count = migrate_json_to_sqlite(json_path, db_path)
        assert count == 1

        b = SQLiteBackend(db_path)
        rec = b.get("aa:bb:cc:dd:ee:ff")
        assert rec is not None
        assert rec.profile == "fedora-server"
        assert rec.state == ProvisionState.REGISTERED
        b.close()

    def test_migrate_list_format(self, tmp_path):
        state = [
            {
                "mac": "aa:bb:cc:dd:ee:01",
                "profile": "p1",
                "os_family": "fedora",
                "os_version": "41",
                "state": "booting",
                "history": [],
            },
            {
                "mac": "aa:bb:cc:dd:ee:02",
                "profile": "p2",
                "os_family": "ubuntu",
                "os_version": "24.04",
                "state": "complete",
                "history": [],
            },
        ]
        json_path = tmp_path / "state.json"
        json_path.write_text(json.dumps(state))
        db_path = tmp_path / "state.db"

        count = migrate_json_to_sqlite(json_path, db_path)
        assert count == 2

    def test_migrate_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            migrate_json_to_sqlite(
                tmp_path / "missing.json",
                tmp_path / "state.db",
            )

    def test_migrate_empty_file(self, tmp_path):
        json_path = tmp_path / "empty.json"
        json_path.write_text("{}")
        db_path = tmp_path / "state.db"
        count = migrate_json_to_sqlite(json_path, db_path)
        assert count == 0

    def test_migrate_skips_malformed_records(self, tmp_path):
        state = {
            "good": {
                "mac": "aa:bb:cc:dd:ee:ff",
                "profile": "p1",
                "os_family": "fedora",
                "os_version": "41",
                "state": "registered",
                "history": [],
            },
            "bad": {
                "not_a_real_field": "whoops",
            },
        }
        json_path = tmp_path / "state.json"
        json_path.write_text(json.dumps(state))
        db_path = tmp_path / "state.db"
        count = migrate_json_to_sqlite(json_path, db_path)
        assert count == 1


# ---- ProvisionTracker with backend ----


class TestTrackerWithSQLiteBackend:

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.db_path = tmp_path / "tracker.db"
        self.backend = SQLiteBackend(self.db_path)
        self.tracker = ProvisionTracker(backend=self.backend)
        yield
        self.backend.close()

    def test_register_persists(self):
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        # Check directly via backend
        rec = self.backend.get("aa:bb:cc:dd:ee:ff")
        assert rec is not None
        assert rec.state == ProvisionState.REGISTERED

    def test_transition_persists(self):
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        self.tracker.transition(
            "aa:bb:cc:dd:ee:ff", ProvisionState.BOOTING
        )
        rec = self.backend.get("aa:bb:cc:dd:ee:ff")
        assert rec is not None
        assert rec.state == ProvisionState.BOOTING

    def test_disable_netboot_persists(self):
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        self.tracker.disable_netboot("aa:bb:cc:dd:ee:ff")
        rec = self.backend.get("aa:bb:cc:dd:ee:ff")
        assert rec is not None
        assert rec.netboot_enabled is False

    def test_enable_netboot_persists(self):
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        self.tracker.disable_netboot("aa:bb:cc:dd:ee:ff")
        self.tracker.enable_netboot("aa:bb:cc:dd:ee:ff")
        rec = self.backend.get("aa:bb:cc:dd:ee:ff")
        assert rec is not None
        assert rec.netboot_enabled is True

    def test_clear_empties_backend(self):
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        self.tracker.clear()
        assert self.backend.list_all() == []

    def test_preloads_from_backend(self):
        """A new tracker should load existing records from the backend."""
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        # Create a fresh tracker with the same backend
        tracker2 = ProvisionTracker(backend=self.backend)
        rec = tracker2.get("aa:bb:cc:dd:ee:ff")
        assert rec is not None
        assert rec.profile == "fedora-server"

    def test_callbacks_still_work_with_backend(self):
        from unittest.mock import MagicMock

        cb = MagicMock()
        self.tracker.on_state_change(ProvisionState.BOOTING, cb)
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        self.tracker.transition(
            "aa:bb:cc:dd:ee:ff", ProvisionState.BOOTING
        )
        cb.assert_called_once()

    def test_survives_restart(self):
        """Records survive backend close + reopen."""
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "fedora-server", "fedora", "41"
        )
        self.tracker.transition(
            "aa:bb:cc:dd:ee:ff", ProvisionState.COMPLETE
        )
        self.backend.close()

        # Reopen
        backend2 = SQLiteBackend(self.db_path)
        tracker2 = ProvisionTracker(backend=backend2)
        rec = tracker2.get("aa:bb:cc:dd:ee:ff")
        assert rec is not None
        assert rec.state == ProvisionState.COMPLETE
        assert len(rec.history) == 2
        backend2.close()


class TestTrackerWithJSONBackend:

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.json_path = tmp_path / "state.json"
        self.backend = JSONBackend(self.json_path)
        self.tracker = ProvisionTracker(backend=self.backend)

    def test_register_writes_json(self):
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "p1", "fedora", "41"
        )
        assert self.json_path.exists()
        data = json.loads(self.json_path.read_text())
        assert "aa:bb:cc:dd:ee:ff" in data

    def test_transition_updates_json(self):
        self.tracker.register(
            "aa:bb:cc:dd:ee:ff", "p1", "fedora", "41"
        )
        self.tracker.transition(
            "aa:bb:cc:dd:ee:ff", ProvisionState.INSTALLING
        )
        data = json.loads(self.json_path.read_text())
        assert data["aa:bb:cc:dd:ee:ff"]["state"] == "installing"


class TestSQLAlchemyBackendDirect(_BackendContractTests):
    """Test the SQLAlchemyBackend directly with SQLite in-memory."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.backend = SQLAlchemyBackend("sqlite:///:memory:")
        yield
        self.backend.close()

    def test_query_by_state(self):
        self.backend.save(
            _make_record(
                mac="aa:bb:cc:dd:ee:01",
                state=ProvisionState.BOOTING,
            )
        )
        self.backend.save(
            _make_record(
                mac="aa:bb:cc:dd:ee:02",
                state=ProvisionState.COMPLETE,
            )
        )
        self.backend.save(
            _make_record(
                mac="aa:bb:cc:dd:ee:03",
                state=ProvisionState.BOOTING,
            )
        )
        booting = self.backend.query_by_state(ProvisionState.BOOTING)
        assert len(booting) == 2
        complete = self.backend.query_by_state(ProvisionState.COMPLETE)
        assert len(complete) == 1

    def test_engine_property(self):
        assert self.backend.engine is not None
        assert self.backend.engine.dialect.name == "sqlite"

    def test_concurrent_writes(self):
        """10+ simultaneous provisions must not corrupt the database."""
        errors: list = []

        def _writer(i: int) -> None:
            try:
                mac = f"aa:bb:cc:dd:{i:02x}:00"
                rec = _make_record(mac=mac, profile=f"p{i}")
                self.backend.save(rec)
                rec.state = ProvisionState.BOOTING
                rec.updated_at = time.time()
                rec.history.append(
                    (ProvisionState.BOOTING, rec.updated_at)
                )
                self.backend.save(rec)
                rec.state = ProvisionState.COMPLETE
                rec.updated_at = time.time()
                rec.completed_at = rec.updated_at
                rec.history.append(
                    (ProvisionState.COMPLETE, rec.updated_at)
                )
                self.backend.save(rec)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_writer, args=(i,))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent write errors: {errors}"
        records = self.backend.list_all()
        assert len(records) == 20
        for rec in records:
            assert rec.state == ProvisionState.COMPLETE


class TestSQLAlchemyBackendFileDB(_BackendContractTests):
    """Test the SQLAlchemyBackend with a file-based SQLite DB."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        db_path = tmp_path / "test_sa.db"
        self.backend = SQLAlchemyBackend(f"sqlite:///{db_path}")
        yield
        self.backend.close()

    def test_persists_across_instances(self, tmp_path):
        db_path = tmp_path / "persist_sa.db"
        url = f"sqlite:///{db_path}"
        b1 = SQLAlchemyBackend(url)
        b1.save(_make_record(mac="aa:bb:cc:dd:ee:ff"))
        b1.close()

        b2 = SQLAlchemyBackend(url)
        got = b2.get("aa:bb:cc:dd:ee:ff")
        assert got is not None
        assert got.mac == "aa:bb:cc:dd:ee:ff"
        b2.close()


class TestPostgreSQLURLParsing:
    """Test PostgreSQL URL construction and engine creation (mocked)."""

    def test_postgresql_url_accepted(self):
        """SQLAlchemyBackend should accept a PostgreSQL URL."""
        from unittest.mock import MagicMock, patch

        mock_engine = MagicMock()
        mock_engine.dialect.name = "postgresql"
        mock_engine.url = "postgresql://user:pass@localhost/tftpos"

        with patch("tftpos.db.create_engine", return_value=mock_engine) as mock_ce:
            with patch("tftpos.db.Base.metadata.create_all"):
                backend = SQLAlchemyBackend(
                    "postgresql://user:pass@localhost/tftpos"
                )
                mock_ce.assert_called_once_with(
                    "postgresql://user:pass@localhost/tftpos"
                )
                assert backend.engine is mock_engine

    def test_postgresql_url_with_port(self):
        """PostgreSQL URL with custom port should work."""
        from unittest.mock import MagicMock, patch

        mock_engine = MagicMock()
        mock_engine.dialect.name = "postgresql"
        mock_engine.url = "postgresql://user:pass@db.example.com:5433/tftpos"

        with patch("tftpos.db.create_engine", return_value=mock_engine):
            with patch("tftpos.db.Base.metadata.create_all"):
                backend = SQLAlchemyBackend(
                    "postgresql://user:pass@db.example.com:5433/tftpos"
                )
                assert backend.engine is mock_engine

    def test_postgresql_psycopg2_driver(self):
        """PostgreSQL URL with explicit psycopg2 driver."""
        from unittest.mock import MagicMock, patch

        mock_engine = MagicMock()
        mock_engine.dialect.name = "postgresql"
        mock_engine.url = "postgresql+psycopg2://user:pass@localhost/tftpos"

        with patch("tftpos.db.create_engine", return_value=mock_engine) as mock_ce:
            with patch("tftpos.db.Base.metadata.create_all"):
                backend = SQLAlchemyBackend(
                    "postgresql+psycopg2://user:pass@localhost/tftpos"
                )
                mock_ce.assert_called_once_with(
                    "postgresql+psycopg2://user:pass@localhost/tftpos"
                )


class TestMariaDBURLParsing:
    """Test MariaDB/MySQL URL construction and engine creation (mocked)."""

    def test_mariadb_pymysql_url_accepted(self):
        """SQLAlchemyBackend should accept a MariaDB URL with pymysql driver."""
        from unittest.mock import MagicMock, patch

        mock_engine = MagicMock()
        mock_engine.dialect.name = "mysql"
        mock_engine.url = "mysql+pymysql://user:pass@localhost/tftpos"

        with patch("tftpos.db.create_engine", return_value=mock_engine) as mock_ce:
            with patch("tftpos.db.Base.metadata.create_all"):
                backend = SQLAlchemyBackend(
                    "mysql+pymysql://user:pass@localhost/tftpos"
                )
                mock_ce.assert_called_once_with(
                    "mysql+pymysql://user:pass@localhost/tftpos"
                )
                assert backend.engine is mock_engine

    def test_mariadb_url_with_port(self):
        """MariaDB URL with custom port should work."""
        from unittest.mock import MagicMock, patch

        mock_engine = MagicMock()
        mock_engine.dialect.name = "mysql"
        mock_engine.url = "mysql+pymysql://user:pass@mariadb.local:3307/tftpos"

        with patch("tftpos.db.create_engine", return_value=mock_engine):
            with patch("tftpos.db.Base.metadata.create_all"):
                backend = SQLAlchemyBackend(
                    "mysql+pymysql://user:pass@mariadb.local:3307/tftpos"
                )
                assert backend.engine is mock_engine

    def test_mariadb_mariadbconnector_driver(self):
        """MariaDB URL with mariadbconnector driver."""
        from unittest.mock import MagicMock, patch

        mock_engine = MagicMock()
        mock_engine.dialect.name = "mysql"
        mock_engine.url = "mariadb+mariadbconnector://user:pass@localhost/tftpos"

        with patch("tftpos.db.create_engine", return_value=mock_engine) as mock_ce:
            with patch("tftpos.db.Base.metadata.create_all"):
                backend = SQLAlchemyBackend(
                    "mariadb+mariadbconnector://user:pass@localhost/tftpos"
                )
                mock_ce.assert_called_once_with(
                    "mariadb+mariadbconnector://user:pass@localhost/tftpos"
                )


class TestProvisionRowModel:
    """Test the SQLAlchemy ORM model conversion methods."""

    def test_from_provision_record(self):
        rec = _make_record()
        row = ProvisionRow.from_provision_record(rec)
        assert row.mac == rec.mac.lower()
        assert row.profile == rec.profile
        assert row.os_family == rec.os_family
        assert row.os_version == rec.os_version
        assert row.state == rec.state.value
        assert row.started_at == rec.started_at
        assert row.netboot_enabled == rec.netboot_enabled
        # History should be JSON
        history = json.loads(row.history)
        assert len(history) == 1

    def test_to_provision_record(self):
        now = time.time()
        row = ProvisionRow(
            mac="aa:bb:cc:dd:ee:ff",
            profile="fedora-server",
            os_family="fedora",
            os_version="41",
            state="registered",
            started_at=now,
            updated_at=now,
            history=json.dumps([
                {"state": "registered", "timestamp": now}
            ]),
            netboot_enabled=True,
        )
        rec = row.to_provision_record()
        assert rec.mac == "aa:bb:cc:dd:ee:ff"
        assert rec.state == ProvisionState.REGISTERED
        assert rec.netboot_enabled is True
        assert len(rec.history) == 1

    def test_round_trip_via_model(self):
        original = _make_record()
        original.error_message = "test error"
        original.netboot_enabled = False
        row = ProvisionRow.from_provision_record(original)
        restored = row.to_provision_record()
        assert restored.mac == original.mac.lower()
        assert restored.profile == original.profile
        assert restored.error_message == "test error"
        assert restored.netboot_enabled is False
        assert restored.state == original.state


class TestDatabaseConfig:
    """Test the DatabaseConfig dataclass and config loading."""

    def test_default_values(self):
        from tftpos.config import DatabaseConfig

        cfg = DatabaseConfig()
        assert cfg.backend == "sqlite"
        assert cfg.url == "sqlite:///tftpos.db"

    def test_postgresql_config(self):
        from tftpos.config import DatabaseConfig

        cfg = DatabaseConfig(
            backend="postgresql",
            url="postgresql://user:pass@localhost:5432/tftpos",
        )
        assert cfg.backend == "postgresql"
        assert "postgresql" in cfg.url

    def test_mariadb_config(self):
        from tftpos.config import DatabaseConfig

        cfg = DatabaseConfig(
            backend="mariadb",
            url="mysql+pymysql://user:pass@localhost:3306/tftpos",
        )
        assert cfg.backend == "mariadb"
        assert "pymysql" in cfg.url

    def test_load_config_with_database_section(self, tmp_path):
        from tftpos.config import load_config

        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[database]\n'
            'backend = "postgresql"\n'
            'url = "postgresql://admin:secret@db.local:5432/tftpos"\n'
        )
        cfg = load_config(config_file)
        assert cfg.database.backend == "postgresql"
        assert cfg.database.url == "postgresql://admin:secret@db.local:5432/tftpos"

    def test_load_config_without_database_section(self, tmp_path):
        from tftpos.config import load_config

        config_file = tmp_path / "config.toml"
        config_file.write_text("")
        cfg = load_config(config_file)
        assert cfg.database.backend == "sqlite"
        assert cfg.database.url == "sqlite:///tftpos.db"


class TestSQLiteBackendIsAlias:
    """Verify SQLiteBackend is a thin wrapper around SQLAlchemyBackend."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.backend = SQLiteBackend(tmp_path / "alias_test.db")
        yield
        self.backend.close()

    def test_is_sqlalchemy_backend(self):
        assert isinstance(self.backend, SQLAlchemyBackend)

    def test_engine_is_sqlite(self):
        assert self.backend.engine.dialect.name == "sqlite"


class TestTrackerWithoutBackend:
    """Verify that the tracker still works without a backend (original behaviour)."""

    def test_register_and_get(self):
        tracker = ProvisionTracker()
        rec = tracker.register(
            "aa:bb:cc:dd:ee:ff", "p1", "fedora", "41"
        )
        assert tracker.get("aa:bb:cc:dd:ee:ff") is rec

    def test_transition(self):
        tracker = ProvisionTracker()
        tracker.register(
            "aa:bb:cc:dd:ee:ff", "p1", "fedora", "41"
        )
        rec = tracker.transition(
            "aa:bb:cc:dd:ee:ff", ProvisionState.BOOTING
        )
        assert rec.state == ProvisionState.BOOTING

    def test_clear(self):
        tracker = ProvisionTracker()
        tracker.register("aa:bb:cc:dd:ee:ff", "p1", "fedora", "41")
        tracker.clear()
        assert tracker.list_all() == []


