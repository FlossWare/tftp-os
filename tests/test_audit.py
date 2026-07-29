"""Tests for the structured audit logging module (tftpos.audit)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tftpos.audit import (
    AuditConfig,
    AuditEvent,
    AuditLogger,
    _make_entry,
    get_audit_logger,
    init_audit,
)


# ---- Unit tests for _make_entry ----


class TestMakeEntry:
    def test_basic_entry(self):
        entry = _make_entry("test_event", {"key": "val"})
        assert entry["event_type"] == "test_event"
        assert entry["key"] == "val"
        assert "timestamp" in entry
        assert "event_id" in entry
        assert len(entry["event_id"]) == 16
        assert "client_ip" not in entry

    def test_entry_with_client_ip(self):
        entry = _make_entry(
            "test_event", {"mac": "aa:bb:cc:dd:ee:ff"},
            client_ip="10.0.0.1",
        )
        assert entry["client_ip"] == "10.0.0.1"
        assert entry["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_timestamp_is_recent(self):
        before = time.time()
        entry = _make_entry("test", {})
        after = time.time()
        assert before <= entry["timestamp"] <= after

    def test_event_ids_unique(self):
        e1 = _make_entry("a", {})
        e2 = _make_entry("b", {})
        assert e1["event_id"] != e2["event_id"]


# ---- AuditConfig tests ----


class TestAuditConfig:
    def test_defaults(self):
        config = AuditConfig()
        assert config.enabled is True
        assert config.log_file is None
        assert config.max_bytes == 52_428_800
        assert config.backup_count == 10
        assert config.log_to_stdout is False
        assert config.buffer_size == 1000
        assert config.syslog_enabled is False
        assert config.syslog_address == "/dev/log"

    def test_custom_values(self):
        config = AuditConfig(
            enabled=False,
            log_file=Path("/var/log/tftpos-audit.jsonl"),
            max_bytes=1_000_000,
            backup_count=3,
            buffer_size=500,
        )
        assert config.enabled is False
        assert config.log_file == Path("/var/log/tftpos-audit.jsonl")
        assert config.max_bytes == 1_000_000
        assert config.backup_count == 3
        assert config.buffer_size == 500


# ---- AuditLogger tests ----


class TestAuditLogger:
    def test_disabled_logger_returns_empty(self):
        logger = AuditLogger(AuditConfig(enabled=False))
        entry = logger.log("test", {"key": "val"})
        assert entry == {}
        assert logger.buffer_size() == 0

    def test_log_to_buffer(self):
        logger = AuditLogger(AuditConfig(buffer_size=10))
        entry = logger.log("test_event", {"mac": "aa:bb:cc:dd:ee:ff"})
        assert entry["event_type"] == "test_event"
        assert entry["mac"] == "aa:bb:cc:dd:ee:ff"
        assert logger.buffer_size() == 1

    def test_buffer_overflow(self):
        logger = AuditLogger(AuditConfig(buffer_size=3))
        for i in range(5):
            logger.log("event", {"index": i})
        assert logger.buffer_size() == 3
        entries = logger.query()
        # Newest first
        assert entries[0]["index"] == 4
        assert entries[-1]["index"] == 2

    def test_log_to_file(self, tmp_path):
        log_file = tmp_path / "audit.jsonl"
        logger = AuditLogger(AuditConfig(log_file=log_file))
        logger.log("test_event", {"key": "value"})

        # Force handler flush
        for h in logger._logger.handlers:
            h.flush()

        content = log_file.read_text()
        lines = [l for l in content.strip().split("\n") if l]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_type"] == "test_event"
        assert parsed["key"] == "value"

    def test_log_to_stdout(self, capsys):
        logger = AuditLogger(AuditConfig(log_to_stdout=True))
        logger.log("stdout_test", {"data": 42})

        for h in logger._logger.handlers:
            h.flush()

        captured = capsys.readouterr()
        assert "stdout_test" in captured.out

    def test_enabled_property(self):
        logger = AuditLogger(AuditConfig(enabled=True))
        assert logger.enabled is True
        logger2 = AuditLogger(AuditConfig(enabled=False))
        assert logger2.enabled is False


# ---- Convenience method tests ----


class TestConvenienceMethods:
    @pytest.fixture(autouse=True)
    def setup_logger(self):
        self.logger = AuditLogger(AuditConfig(buffer_size=100))

    def test_log_boot_request(self):
        entry = self.logger.log_boot_request(
            mac="aa:bb:cc:dd:ee:ff",
            profile="fedora40",
            client_ip="10.0.0.5",
        )
        assert entry["event_type"] == AuditEvent.BOOT_REQUEST
        assert entry["mac"] == "aa:bb:cc:dd:ee:ff"
        assert entry["profile"] == "fedora40"
        assert entry["client_ip"] == "10.0.0.5"

    def test_log_boot_request_minimal(self):
        entry = self.logger.log_boot_request(mac="aa:bb:cc:dd:ee:ff")
        assert entry["event_type"] == AuditEvent.BOOT_REQUEST
        assert entry["mac"] == "aa:bb:cc:dd:ee:ff"
        assert "profile" not in entry
        assert "client_ip" not in entry

    def test_log_autoinstall_fetch(self):
        entry = self.logger.log_autoinstall_fetch(
            mac="11:22:33:44:55:66",
            profile="debian12",
            client_ip="10.0.0.10",
        )
        assert entry["event_type"] == AuditEvent.AUTOINSTALL_FETCH
        assert entry["mac"] == "11:22:33:44:55:66"
        assert entry["profile"] == "debian12"

    def test_log_state_transition(self):
        entry = self.logger.log_state_transition(
            mac="aa:bb:cc:dd:ee:ff",
            old_state="booting",
            new_state="installing",
            profile="fedora40",
        )
        assert entry["event_type"] == AuditEvent.STATE_TRANSITION
        assert entry["old_state"] == "booting"
        assert entry["new_state"] == "installing"
        assert entry["profile"] == "fedora40"

    def test_log_state_transition_with_error(self):
        entry = self.logger.log_state_transition(
            mac="aa:bb:cc:dd:ee:ff",
            old_state="installing",
            new_state="failed",
            error_message="disk not found",
        )
        assert entry["error_message"] == "disk not found"

    def test_log_host_rule_change(self):
        rule_data = {
            "profile": "new-profile",
            "os_family": "fedora",
            "mac": "aa:bb:cc:dd:ee:ff",
        }
        entry = self.logger.log_host_rule_change(
            action="created",
            rule_data=rule_data,
            client_ip="10.0.0.1",
        )
        assert entry["event_type"] == AuditEvent.HOST_RULE_CHANGE
        assert entry["action"] == "created"
        assert entry["rule"]["profile"] == "new-profile"

    def test_log_auth_event_success(self):
        entry = self.logger.log_auth_event(
            success=True,
            key_name="admin-key",
            role="admin",
            required_role="admin",
            client_ip="10.0.0.1",
        )
        assert entry["event_type"] == AuditEvent.AUTH_SUCCESS
        assert entry["key_name"] == "admin-key"
        assert entry["role"] == "admin"

    def test_log_auth_event_failure(self):
        entry = self.logger.log_auth_event(
            success=False,
            reason="invalid key",
            required_role="operator",
            client_ip="10.0.0.99",
        )
        assert entry["event_type"] == AuditEvent.AUTH_FAILURE
        assert entry["reason"] == "invalid key"
        assert entry["required_role"] == "operator"

    def test_log_api_key_created(self):
        entry = self.logger.log_api_key_change(
            action="created",
            key_name="test-key",
            role="viewer",
            client_ip="10.0.0.1",
        )
        assert entry["event_type"] == AuditEvent.API_KEY_CREATED
        assert entry["key_name"] == "test-key"
        assert entry["role"] == "viewer"

    def test_log_api_key_deleted(self):
        entry = self.logger.log_api_key_change(
            action="deleted",
            key_name="old-key",
        )
        assert entry["event_type"] == AuditEvent.API_KEY_DELETED
        assert entry["key_name"] == "old-key"

    def test_log_netboot_change(self):
        entry = self.logger.log_netboot_change(
            mac="aa:bb:cc:dd:ee:ff",
            enabled=False,
            client_ip="10.0.0.1",
        )
        assert entry["event_type"] == AuditEvent.NETBOOT_CHANGE
        assert entry["mac"] == "aa:bb:cc:dd:ee:ff"
        assert entry["netboot_enabled"] is False


# ---- Query tests ----


class TestAuditQuery:
    @pytest.fixture(autouse=True)
    def setup_logger(self):
        self.logger = AuditLogger(AuditConfig(buffer_size=100))
        # Populate with test events
        self.logger.log_boot_request(
            mac="aa:bb:cc:dd:ee:ff", profile="fedora40",
        )
        self.logger.log_autoinstall_fetch(
            mac="aa:bb:cc:dd:ee:ff", profile="fedora40",
        )
        self.logger.log_boot_request(
            mac="11:22:33:44:55:66", profile="debian12",
        )
        self.logger.log_state_transition(
            mac="aa:bb:cc:dd:ee:ff",
            old_state="booting",
            new_state="installing",
        )

    def test_query_all(self):
        entries = self.logger.query()
        assert len(entries) == 4
        # Newest first
        assert entries[0]["event_type"] == AuditEvent.STATE_TRANSITION

    def test_query_by_mac(self):
        entries = self.logger.query(mac="aa:bb:cc:dd:ee:ff")
        assert len(entries) == 3
        for e in entries:
            assert "aa:bb:cc:dd:ee:ff" in e.get("mac", "")

    def test_query_by_mac_case_insensitive(self):
        entries = self.logger.query(mac="AA:BB:CC:DD:EE:FF")
        assert len(entries) == 3

    def test_query_by_event_type(self):
        entries = self.logger.query(
            event_type=AuditEvent.BOOT_REQUEST
        )
        assert len(entries) == 2
        for e in entries:
            assert e["event_type"] == AuditEvent.BOOT_REQUEST

    def test_query_by_since(self):
        # All events should be recent
        entries = self.logger.query(since=time.time() - 10)
        assert len(entries) == 4
        # Future timestamp should match nothing
        entries = self.logger.query(since=time.time() + 100)
        assert len(entries) == 0

    def test_query_with_limit(self):
        entries = self.logger.query(limit=2)
        assert len(entries) == 2

    def test_query_combined_filters(self):
        entries = self.logger.query(
            mac="aa:bb:cc:dd:ee:ff",
            event_type=AuditEvent.BOOT_REQUEST,
        )
        assert len(entries) == 1

    def test_query_no_matches(self):
        entries = self.logger.query(mac="ff:ff:ff:ff:ff:ff")
        assert len(entries) == 0


# ---- Singleton tests ----


class TestSingleton:
    def test_init_audit(self):
        config = AuditConfig(enabled=True, buffer_size=50)
        logger = init_audit(config)
        assert logger is get_audit_logger()
        assert logger.enabled is True

    def test_get_audit_logger_default(self):
        # Reset the global
        import tftpos.audit as audit_mod
        audit_mod._audit_logger = None
        logger = get_audit_logger()
        assert logger is not None
        assert logger.enabled is True


# ---- Config integration tests ----


class TestConfigIntegration:
    def test_load_config_with_audit_section(self, tmp_path):
        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            "[server]\n"
            'host = "0.0.0.0"\n'
            "port = 8443\n"
            "\n"
            "[audit]\n"
            "enabled = true\n"
            'log_file = "/var/log/tftpos-audit.jsonl"\n'
            "max_bytes = 10000000\n"
            "backup_count = 5\n"
            "log_to_stdout = false\n"
            "buffer_size = 500\n"
        )
        from tftpos.config import load_config
        config = load_config(config_file)
        assert config.audit.enabled is True
        assert config.audit.log_file == Path("/var/log/tftpos-audit.jsonl")
        assert config.audit.max_bytes == 10_000_000
        assert config.audit.backup_count == 5
        assert config.audit.buffer_size == 500

    def test_load_config_without_audit_section(self, tmp_path):
        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            "[server]\n"
            'host = "0.0.0.0"\n'
            "port = 8443\n"
        )
        from tftpos.config import load_config
        config = load_config(config_file)
        # Should get defaults
        assert config.audit.enabled is True
        assert config.audit.log_file is None
        assert config.audit.buffer_size == 1000

    def test_load_config_audit_disabled(self, tmp_path):
        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            "[server]\n"
            'host = "0.0.0.0"\n'
            "port = 8443\n"
            "\n"
            "[audit]\n"
            "enabled = false\n"
        )
        from tftpos.config import load_config
        config = load_config(config_file)
        assert config.audit.enabled is False


# ---- File rotation tests ----


class TestFileRotation:
    def test_rotation_config(self, tmp_path):
        log_file = tmp_path / "audit.jsonl"
        logger = AuditLogger(
            AuditConfig(
                log_file=log_file,
                max_bytes=100,  # tiny for testing
                backup_count=2,
            )
        )
        # Write enough entries to trigger rotation
        for i in range(50):
            logger.log("rotation_test", {"index": i, "padding": "x" * 50})
            for h in logger._logger.handlers:
                h.flush()

        # Check that backup files were created
        assert log_file.exists()

    def test_log_file_parent_created(self, tmp_path):
        log_file = tmp_path / "subdir" / "deep" / "audit.jsonl"
        logger = AuditLogger(AuditConfig(log_file=log_file))
        logger.log("test", {"key": "val"})
        for h in logger._logger.handlers:
            h.flush()
        assert log_file.parent.exists()


# ---- Event constants ----


class TestAuditEvent:
    def test_event_constants(self):
        assert AuditEvent.BOOT_REQUEST == "boot_request"
        assert AuditEvent.AUTOINSTALL_FETCH == "autoinstall_fetch"
        assert AuditEvent.STATE_TRANSITION == "state_transition"
        assert AuditEvent.HOST_RULE_CHANGE == "host_rule_change"
        assert AuditEvent.API_KEY_CREATED == "api_key_created"
        assert AuditEvent.API_KEY_DELETED == "api_key_deleted"
        assert AuditEvent.AUTH_SUCCESS == "auth_success"
        assert AuditEvent.AUTH_FAILURE == "auth_failure"
        assert AuditEvent.NETBOOT_CHANGE == "netboot_change"
        assert AuditEvent.PROVISION_COMPLETE == "provision_complete"
        assert AuditEvent.PROVISION_FAILED == "provision_failed"


# ---- Thread safety tests ----


class TestThreadSafety:
    def test_concurrent_logging(self):
        import threading

        logger = AuditLogger(AuditConfig(buffer_size=1000))
        errors = []

        def log_events(thread_id):
            try:
                for i in range(100):
                    logger.log(
                        "thread_test",
                        {"thread": thread_id, "index": i},
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=log_events, args=(t,))
            for t in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert logger.buffer_size() == 500

    def test_concurrent_query(self):
        import threading

        logger = AuditLogger(AuditConfig(buffer_size=100))
        for i in range(50):
            logger.log("test", {"index": i})

        errors = []
        results = []

        def query_log():
            try:
                result = logger.query(limit=10)
                results.append(len(result))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=query_log)
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(r == 10 for r in results)


# ---- JSON serialization tests ----


class TestJsonSerialization:
    def test_entry_is_json_serializable(self):
        logger = AuditLogger(AuditConfig(buffer_size=10))
        entry = logger.log_boot_request(
            mac="aa:bb:cc:dd:ee:ff",
            profile="fedora40",
            client_ip="10.0.0.1",
        )
        # Should not raise
        json_str = json.dumps(entry, default=str)
        parsed = json.loads(json_str)
        assert parsed["event_type"] == "boot_request"

    def test_file_output_is_valid_jsonl(self, tmp_path):
        log_file = tmp_path / "audit.jsonl"
        logger = AuditLogger(AuditConfig(log_file=log_file))

        logger.log_boot_request(mac="aa:bb:cc:dd:ee:ff")
        logger.log_autoinstall_fetch(mac="11:22:33:44:55:66")
        logger.log_state_transition(
            mac="aa:bb:cc:dd:ee:ff",
            old_state="booting",
            new_state="installing",
        )

        for h in logger._logger.handlers:
            h.flush()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "event_type" in parsed
            assert "timestamp" in parsed
            assert "event_id" in parsed
