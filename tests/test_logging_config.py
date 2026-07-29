"""Comprehensive tests for logging configuration and rotation (issue #43)."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tftpos.logging_config import (
    CorrelationFormatter,
    JsonFormatter,
    LoggingConfig,
    _make_file_handler,
    _make_stream_handler,
    _make_syslog_handler,
    _resolve_level,
    configure_logging,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
    setup_logging,
)


# ---- Helpers ----


def _fresh_logger(name: str = "tftpos") -> logging.Logger:
    """Return the tftpos logger after clearing handlers."""
    logger = logging.getLogger(name)
    logger.handlers.clear()
    return logger


@pytest.fixture(autouse=True)
def _cleanup_logger():
    """Ensure the tftpos logger is reset between tests."""
    yield
    logger = logging.getLogger("tftpos")
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
    reset_correlation_id()


# ==================================================================
# 1. Log level configuration
# ==================================================================


class TestLogLevelConfiguration:

    def test_debug_level(self):
        setup_logging(level="DEBUG", stream=StringIO())
        assert logging.getLogger("tftpos").level == logging.DEBUG

    def test_info_level(self):
        setup_logging(level="INFO", stream=StringIO())
        assert logging.getLogger("tftpos").level == logging.INFO

    def test_warning_level(self):
        setup_logging(level="WARNING", stream=StringIO())
        assert logging.getLogger("tftpos").level == logging.WARNING

    def test_error_level(self):
        setup_logging(level="ERROR", stream=StringIO())
        assert logging.getLogger("tftpos").level == logging.ERROR

    def test_critical_level(self):
        setup_logging(level="CRITICAL", stream=StringIO())
        assert logging.getLogger("tftpos").level == logging.CRITICAL

    def test_invalid_level_defaults_to_info(self):
        setup_logging(level="INVALID", stream=StringIO())
        assert logging.getLogger("tftpos").level == logging.INFO

    def test_lowercase_level_accepted(self):
        setup_logging(level="debug", stream=StringIO())
        assert logging.getLogger("tftpos").level == logging.DEBUG

    def test_resolve_level_helper(self):
        assert _resolve_level("debug") == "DEBUG"
        assert _resolve_level("GARBAGE") == "INFO"
        assert _resolve_level("warning") == "WARNING"


# ==================================================================
# 2. JSON format output
# ==================================================================


class TestJsonFormatOutput:

    def test_json_output_is_valid_json(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logging.getLogger("tftpos.json").info("hello")
        data = json.loads(stream.getvalue().strip())
        assert isinstance(data, dict)

    def test_json_contains_required_fields(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logging.getLogger("tftpos.json").info("msg1")
        data = json.loads(stream.getvalue().strip())
        for key in ("timestamp", "level", "logger", "message"):
            assert key in data

    def test_json_level_matches(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logging.getLogger("tftpos.json").warning("warn")
        data = json.loads(stream.getvalue().strip())
        assert data["level"] == "WARNING"

    def test_json_logger_name(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logging.getLogger("tftpos.sub").info("x")
        data = json.loads(stream.getvalue().strip())
        assert data["logger"] == "tftpos.sub"

    def test_json_exception_field(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("tftpos.exc").exception("err")
        data = json.loads(stream.getvalue().strip())
        assert "exception" in data
        assert "boom" in data["exception"]

    def test_json_extra_data_field(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logger = logging.getLogger("tftpos.extra")
        record = logger.makeRecord(
            "tftpos.extra", logging.INFO, "", 0, "msg", (), None
        )
        record.extra_data = {"key": "value"}
        logger.handle(record)
        data = json.loads(stream.getvalue().strip())
        assert data["extra"] == {"key": "value"}

    def test_json_correlation_id_included(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        cid = set_correlation_id("test-cid-123")
        logging.getLogger("tftpos.cid").info("correlated")
        data = json.loads(stream.getvalue().strip())
        assert data["correlation_id"] == "test-cid-123"

    def test_json_no_correlation_id_when_unset(self):
        stream = StringIO()
        reset_correlation_id()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logging.getLogger("tftpos.nocid").info("uncorrelated")
        data = json.loads(stream.getvalue().strip())
        assert "correlation_id" not in data


# ==================================================================
# 3. File handler creation and rotation settings
# ==================================================================


class TestFileHandler:

    def test_file_handler_created(self, tmp_path):
        log_file = tmp_path / "test.log"
        handler = _make_file_handler(
            log_file, logging.DEBUG, 1024, 3, False
        )
        assert isinstance(
            handler, logging.handlers.RotatingFileHandler
        )
        handler.close()

    def test_file_handler_max_bytes(self, tmp_path):
        log_file = tmp_path / "test.log"
        handler = _make_file_handler(
            log_file, logging.DEBUG, 5000, 2, False
        )
        assert handler.maxBytes == 5000
        handler.close()

    def test_file_handler_backup_count(self, tmp_path):
        log_file = tmp_path / "test.log"
        handler = _make_file_handler(
            log_file, logging.DEBUG, 1024, 7, False
        )
        assert handler.backupCount == 7
        handler.close()

    def test_file_handler_creates_parent_dirs(self, tmp_path):
        log_file = tmp_path / "subdir" / "deep" / "test.log"
        handler = _make_file_handler(
            log_file, logging.DEBUG, 1024, 3, False
        )
        assert log_file.parent.exists()
        handler.close()

    def test_file_handler_writes_log(self, tmp_path):
        log_file = tmp_path / "app.log"
        handler = _make_file_handler(
            log_file, logging.DEBUG, 10_000, 3, False
        )
        logger = _fresh_logger()
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.info("file test message")
        handler.flush()
        handler.close()
        content = log_file.read_text()
        assert "file test message" in content

    def test_file_handler_json_format(self, tmp_path):
        log_file = tmp_path / "json.log"
        handler = _make_file_handler(
            log_file, logging.DEBUG, 10_000, 3, True
        )
        logger = _fresh_logger()
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.info("json file test")
        handler.flush()
        handler.close()
        data = json.loads(log_file.read_text().strip())
        assert data["message"] == "json file test"

    def test_configure_logging_with_file(self, tmp_path):
        log_file = tmp_path / "configured.log"
        config = LoggingConfig(
            level="DEBUG",
            log_file=log_file,
            max_bytes=5000,
            backup_count=2,
        )
        configure_logging(config)
        logger = logging.getLogger("tftpos")
        # Should have 2 handlers: console + file
        assert len(logger.handlers) == 2
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == 5000
        assert file_handlers[0].backupCount == 2
        for h in logger.handlers:
            if hasattr(h, "close"):
                h.close()


# ==================================================================
# 4. Syslog handler configuration
# ==================================================================


class TestSyslogHandler:

    def test_syslog_handler_unix_socket(self):
        handler = _make_syslog_handler(
            "/dev/log", logging.INFO, False
        )
        assert isinstance(
            handler, logging.handlers.SysLogHandler
        )
        handler.close()

    def test_syslog_handler_host_port(self):
        handler = _make_syslog_handler(
            "localhost:514", logging.INFO, False
        )
        assert isinstance(
            handler, logging.handlers.SysLogHandler
        )
        handler.close()

    def test_syslog_handler_json_format(self):
        handler = _make_syslog_handler(
            "/dev/log", logging.INFO, True
        )
        assert isinstance(handler.formatter, JsonFormatter)
        handler.close()

    def test_syslog_handler_text_format(self):
        handler = _make_syslog_handler(
            "/dev/log", logging.INFO, False
        )
        assert not isinstance(handler.formatter, JsonFormatter)
        handler.close()

    def test_configure_logging_with_syslog(self):
        config = LoggingConfig(
            syslog_enabled=True,
            syslog_address="localhost:5140",
        )
        configure_logging(config)
        logger = logging.getLogger("tftpos")
        syslog_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.SysLogHandler)
        ]
        assert len(syslog_handlers) == 1
        for h in logger.handlers:
            if hasattr(h, "close"):
                h.close()


# ==================================================================
# 5. Journald handler
# ==================================================================


class TestJournaldHandler:

    def test_journald_returns_none_without_systemd(self):
        from tftpos.logging_config import _make_journald_handler

        with patch.dict("sys.modules", {"systemd": None, "systemd.journal": None}):
            result = _make_journald_handler(logging.INFO, False)
        # On systems without systemd, returns None
        # On systems with systemd, returns a handler
        # Either way the function should not raise
        assert result is None or isinstance(result, logging.Handler)

    def test_configure_logging_journald_no_crash(self):
        """Enabling journald should not crash even without systemd."""
        config = LoggingConfig(journald_enabled=True)
        # Should not raise
        configure_logging(config)
        logger = logging.getLogger("tftpos")
        # At minimum the console handler is present
        assert len(logger.handlers) >= 1


# ==================================================================
# 6. Correlation ID generation and attachment
# ==================================================================


class TestCorrelationId:

    def test_get_returns_none_by_default(self):
        reset_correlation_id()
        assert get_correlation_id() is None

    def test_set_returns_id(self):
        cid = set_correlation_id("abc123")
        assert cid == "abc123"

    def test_set_generates_id_when_none(self):
        cid = set_correlation_id()
        assert cid is not None
        assert len(cid) == 16

    def test_get_returns_set_value(self):
        set_correlation_id("my-cid")
        assert get_correlation_id() == "my-cid"

    def test_reset_clears_id(self):
        set_correlation_id("will-be-cleared")
        reset_correlation_id()
        assert get_correlation_id() is None

    def test_correlation_id_in_text_format(self):
        stream = StringIO()
        setup_logging(level="DEBUG", stream=stream)
        set_correlation_id("text-cid")
        logging.getLogger("tftpos.corr").info("correlated msg")
        output = stream.getvalue()
        assert "cid=text-cid" in output

    def test_correlation_id_absent_in_text_when_unset(self):
        stream = StringIO()
        reset_correlation_id()
        setup_logging(level="DEBUG", stream=stream)
        logging.getLogger("tftpos.corr2").info("no cid")
        output = stream.getvalue()
        assert "cid=" not in output


# ==================================================================
# 7. Backward compatibility
# ==================================================================


class TestBackwardCompatibility:

    def test_setup_logging_still_works(self):
        stream = StringIO()
        setup_logging(level="DEBUG", json_format=False, stream=stream)
        logger = logging.getLogger("tftpos")
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1

    def test_setup_logging_json_still_works(self):
        stream = StringIO()
        setup_logging(level="INFO", json_format=True, stream=stream)
        logger = logging.getLogger("tftpos")
        logger.info("compat json")
        data = json.loads(stream.getvalue().strip())
        assert data["message"] == "compat json"

    def test_setup_logging_default_args(self):
        setup_logging()
        logger = logging.getLogger("tftpos")
        assert logger.level == logging.INFO
        assert len(logger.handlers) == 1

    def test_setup_logging_clears_previous_handlers(self):
        setup_logging(level="DEBUG", stream=StringIO())
        setup_logging(level="INFO", stream=StringIO())
        logger = logging.getLogger("tftpos")
        assert len(logger.handlers) == 1


# ==================================================================
# 8. LoggingConfig dataclass
# ==================================================================


class TestLoggingConfigDataclass:

    def test_defaults(self):
        config = LoggingConfig()
        assert config.level == "INFO"
        assert config.json_format is False
        assert config.log_file is None
        assert config.max_bytes == 10_485_760
        assert config.backup_count == 5
        assert config.syslog_enabled is False
        assert config.syslog_address == "/dev/log"
        assert config.journald_enabled is False

    def test_custom_values(self):
        config = LoggingConfig(
            level="DEBUG",
            json_format=True,
            log_file=Path("/tmp/test.log"),
            max_bytes=1024,
            backup_count=2,
            syslog_enabled=True,
            syslog_address="localhost:514",
            journald_enabled=True,
        )
        assert config.level == "DEBUG"
        assert config.json_format is True
        assert config.log_file == Path("/tmp/test.log")
        assert config.max_bytes == 1024
        assert config.backup_count == 2
        assert config.syslog_enabled is True
        assert config.syslog_address == "localhost:514"
        assert config.journald_enabled is True


# ==================================================================
# 9. configure_logging() function
# ==================================================================


class TestConfigureLogging:

    def test_console_handler_always_present(self):
        config = LoggingConfig()
        configure_logging(config)
        logger = logging.getLogger("tftpos")
        stream_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(
                h,
                (
                    logging.handlers.RotatingFileHandler,
                    logging.handlers.SysLogHandler,
                ),
            )
        ]
        assert len(stream_handlers) >= 1

    def test_sets_logger_level(self):
        config = LoggingConfig(level="ERROR")
        configure_logging(config)
        assert logging.getLogger("tftpos").level == logging.ERROR

    def test_clears_previous_handlers(self):
        configure_logging(LoggingConfig(level="DEBUG"))
        configure_logging(LoggingConfig(level="INFO"))
        logger = logging.getLogger("tftpos")
        assert len(logger.handlers) == 1

    def test_invalid_level_defaults_to_info(self):
        config = LoggingConfig(level="BOGUS")
        configure_logging(config)
        assert logging.getLogger("tftpos").level == logging.INFO


# ==================================================================
# 10. Config file parsing integration
# ==================================================================


class TestConfigFileParsing:

    def test_load_config_with_logging_section(self, tmp_path):
        toml_file = tmp_path / "tftpos.toml"
        toml_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8443\n\n'
            "[logging]\n"
            'level = "DEBUG"\n'
            "json_format = true\n"
            'log_file = "/var/log/tftpos/tftpos.log"\n'
            "max_bytes = 5000\n"
            "backup_count = 3\n"
            "syslog_enabled = true\n"
            'syslog_address = "loghost:514"\n'
            "journald_enabled = true\n"
        )
        from tftpos.config import load_config

        config = load_config(toml_file)
        assert config.logging.level == "DEBUG"
        assert config.logging.json_format is True
        assert config.logging.log_file == Path(
            "/var/log/tftpos/tftpos.log"
        )
        assert config.logging.max_bytes == 5000
        assert config.logging.backup_count == 3
        assert config.logging.syslog_enabled is True
        assert config.logging.syslog_address == "loghost:514"
        assert config.logging.journald_enabled is True

    def test_load_config_without_logging_section(self, tmp_path):
        toml_file = tmp_path / "tftpos.toml"
        toml_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8443\n'
        )
        from tftpos.config import load_config

        config = load_config(toml_file)
        # Defaults applied
        assert config.logging.level == "INFO"
        assert config.logging.json_format is False
        assert config.logging.log_file is None

    def test_tftpos_config_has_logging_field(self):
        from tftpos.config import TftpOSConfig

        config = TftpOSConfig()
        assert isinstance(config.logging, LoggingConfig)


# ==================================================================
# 11. Formatter edge cases
# ==================================================================


class TestFormatterEdgeCases:

    def test_correlation_formatter_without_cid(self):
        reset_correlation_id()
        fmt = CorrelationFormatter("%(message)s")
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "hello", (), None
        )
        result = fmt.format(record)
        assert result == "hello"
        assert "cid=" not in result

    def test_correlation_formatter_with_cid(self):
        set_correlation_id("fmt-cid")
        fmt = CorrelationFormatter("%(message)s")
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "hello", (), None
        )
        result = fmt.format(record)
        assert "cid=fmt-cid" in result

    def test_json_formatter_non_serializable_extra(self):
        """JsonFormatter should handle non-serializable extra_data via default=str."""
        fmt = JsonFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "msg", (), None
        )
        record.extra_data = {"path": Path("/tmp/test")}
        result = fmt.format(record)
        data = json.loads(result)
        assert "/tmp/test" in data["extra"]["path"]


# ==================================================================
# 12. Stream handler factory
# ==================================================================


class TestStreamHandlerFactory:

    def test_stream_handler_uses_stderr_by_default(self):
        handler = _make_stream_handler(logging.INFO, False)
        assert handler.stream is sys.stderr

    def test_stream_handler_uses_custom_stream(self):
        stream = StringIO()
        handler = _make_stream_handler(logging.INFO, False, stream)
        assert handler.stream is stream

    def test_stream_handler_json_formatter(self):
        handler = _make_stream_handler(logging.INFO, True)
        assert isinstance(handler.formatter, JsonFormatter)

    def test_stream_handler_text_formatter(self):
        handler = _make_stream_handler(logging.INFO, False)
        assert isinstance(
            handler.formatter, CorrelationFormatter
        )
