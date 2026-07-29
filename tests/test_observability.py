"""Tests for logging, metrics, and observability (issues #33 and #34)."""

from __future__ import annotations

import json
import logging
from io import StringIO
import pytest


# ---- Fixtures ----


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset metrics before each test to avoid cross-test contamination."""
    from tftpos.metrics import reset_all

    reset_all()
    yield
    reset_all()


# ---- Logging setup ----


class TestLoggingSetup:

    def test_setup_logging_sets_level(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="DEBUG", stream=stream)
        logger = logging.getLogger("tftpos")
        assert logger.level == logging.DEBUG

    def test_setup_logging_info_level(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="INFO", stream=stream)
        logger = logging.getLogger("tftpos")
        assert logger.level == logging.INFO

    def test_setup_logging_warning_level(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="WARNING", stream=stream)
        logger = logging.getLogger("tftpos")
        assert logger.level == logging.WARNING

    def test_setup_logging_error_level(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="ERROR", stream=stream)
        logger = logging.getLogger("tftpos")
        assert logger.level == logging.ERROR

    def test_setup_logging_invalid_level_defaults_to_info(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="INVALID", stream=stream)
        logger = logging.getLogger("tftpos")
        assert logger.level == logging.INFO

    def test_setup_logging_default_format(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="DEBUG", stream=stream)
        logger = logging.getLogger("tftpos.test_default")
        logger.debug("test message")

        output = stream.getvalue()
        assert "DEBUG" in output
        assert "tftpos.test_default" in output
        assert "test message" in output

    def test_setup_logging_json_format(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logger = logging.getLogger("tftpos.test_json")
        logger.info("json test")

        output = stream.getvalue().strip()
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "tftpos.test_json"
        assert data["message"] == "json test"

    def test_setup_logging_json_format_has_timestamp(self):
        from tftpos.logging_config import setup_logging

        stream = StringIO()
        setup_logging(level="DEBUG", json_format=True, stream=stream)
        logger = logging.getLogger("tftpos.test_ts")
        logger.info("timestamp test")

        output = stream.getvalue().strip()
        data = json.loads(output)
        assert "timestamp" in data

    def test_setup_logging_clears_previous_handlers(self):
        from tftpos.logging_config import setup_logging

        stream1 = StringIO()
        setup_logging(level="DEBUG", stream=stream1)
        stream2 = StringIO()
        setup_logging(level="INFO", stream=stream2)

        logger = logging.getLogger("tftpos")
        assert len(logger.handlers) == 1


# ---- Metrics increment on events ----


class TestMetricsIncrements:

    def test_boot_request_increments_counter(self):
        from tftpos.metrics import boot_requests_total

        assert boot_requests_total.get() == 0.0
        boot_requests_total.inc()
        assert boot_requests_total.get() == 1.0

    def test_provision_counter_with_labels(self):
        from tftpos.metrics import provisions_total

        provisions_total.inc(os_family="fedora", status="success")
        provisions_total.inc(os_family="fedora", status="success")
        provisions_total.inc(os_family="debian", status="success")

        assert provisions_total.get(
            os_family="fedora", status="success"
        ) == 2.0
        assert provisions_total.get(
            os_family="debian", status="success"
        ) == 1.0

    def test_active_provisions_gauge(self):
        from tftpos.metrics import active_provisions

        assert active_provisions.get() == 0.0
        active_provisions.inc()
        active_provisions.inc()
        assert active_provisions.get() == 2.0
        active_provisions.dec()
        assert active_provisions.get() == 1.0

    def test_import_counter_with_labels(self):
        from tftpos.metrics import import_operations_total

        import_operations_total.inc(os_family="fedora", type="iso")
        import_operations_total.inc(os_family="ubuntu", type="url")

        assert import_operations_total.get(
            os_family="fedora", type="iso"
        ) == 1.0
        assert import_operations_total.get(
            os_family="ubuntu", type="url"
        ) == 1.0

    def test_auth_counter_with_labels(self):
        from tftpos.metrics import auth_attempts_total

        auth_attempts_total.inc(result="success")
        auth_attempts_total.inc(result="failure")
        auth_attempts_total.inc(result="failure")

        assert auth_attempts_total.get(result="success") == 1.0
        assert auth_attempts_total.get(result="failure") == 2.0

    def test_render_metrics_includes_all_counters(self):
        from tftpos.metrics import (
            provisions_total,
            render_metrics,
        )

        provisions_total.inc(os_family="arch", status="success")

        output = render_metrics()
        assert 'tftpos_provisions_total{os_family="arch",status="success"} 1' in output

    def test_gauge_set(self):
        from tftpos.metrics import active_provisions

        active_provisions.set(42)
        assert active_provisions.get() == 42.0

    def test_counter_render_no_values(self):
        from tftpos.metrics import _Counter

        c = _Counter("test_empty", "test help")
        rendered = c.render()
        assert "test_empty 0" in rendered

    def test_uptime_positive(self):
        from tftpos.metrics import get_uptime_seconds

        assert get_uptime_seconds() >= 0
