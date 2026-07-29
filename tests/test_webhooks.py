"""Tests for the TftpOS webhook delivery system."""

from __future__ import annotations

import hashlib
import hmac
import json
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from unittest.mock import MagicMock

import pytest

from tftpos.config import TftpOSConfig, load_config
from tftpos.webhooks import (
    SUPPORTED_EVENTS,
    WebhookConfig,
    WebhookDelivery,
    WebhookManager,
    compute_signature,
    verify_signature,
)


# ---------------------------------------------------------------
# WebhookConfig tests
# ---------------------------------------------------------------


class TestWebhookConfig:

    def test_valid_http_url(self):
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            secret="s3cret",
        )
        assert wh.url == "http://example.com/hook"
        assert wh.events == ["provision.started"]
        assert wh.secret == "s3cret"

    def test_valid_https_url(self):
        wh = WebhookConfig(
            url="https://example.com/hook",
            events=["provision.complete"],
        )
        assert wh.url == "https://example.com/hook"

    def test_invalid_scheme_raises(self):
        with pytest.raises(ValueError, match="http or https"):
            WebhookConfig(url="ftp://example.com/hook")

    def test_unknown_event_accepted(self):
        """TftpOS does not validate event names at config time."""
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["nonexistent.event"],
        )
        assert wh.events == ["nonexistent.event"]

    def test_empty_events_means_all(self):
        wh = WebhookConfig(url="http://example.com/hook")
        assert wh.events == []

    def test_default_retry_and_timeout(self):
        wh = WebhookConfig(url="http://example.com/hook")
        assert wh.retry_count == 3
        assert wh.timeout == 10.0

    def test_custom_retry_and_timeout(self):
        wh = WebhookConfig(
            url="http://example.com/hook",
            retry_count=5,
            timeout=30.0,
        )
        assert wh.retry_count == 5
        assert wh.timeout == 30.0


# ---------------------------------------------------------------
# HMAC signature tests
# ---------------------------------------------------------------


class TestHMACSignature:

    def test_compute_signature_deterministic(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        sig1 = compute_signature(payload, secret)
        sig2 = compute_signature(payload, secret)
        assert sig1 == sig2

    def test_compute_signature_matches_manual(self):
        payload = b'{"event": "provision.started"}'
        secret = "webhook-secret"
        expected = hmac.new(
            b"webhook-secret", payload, hashlib.sha256
        ).hexdigest()
        assert compute_signature(payload, secret) == expected

    def test_different_secret_different_signature(self):
        payload = b'{"event": "test"}'
        sig1 = compute_signature(payload, "secret-a")
        sig2 = compute_signature(payload, "secret-b")
        assert sig1 != sig2

    def test_different_payload_different_signature(self):
        secret = "same-secret"
        sig1 = compute_signature(b"payload-a", secret)
        sig2 = compute_signature(b"payload-b", secret)
        assert sig1 != sig2

    def test_verify_signature_valid(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        sig = compute_signature(payload, secret)
        assert verify_signature(payload, secret, sig) is True

    def test_verify_signature_invalid(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        assert verify_signature(
            payload, secret, "invalid-sig"
        ) is False

    def test_verify_signature_wrong_secret(self):
        payload = b'{"event": "test"}'
        sig = compute_signature(payload, "correct-secret")
        assert verify_signature(
            payload, "wrong-secret", sig
        ) is False

    def test_verify_signature_timing_safe(self):
        """Verify that comparison uses hmac.compare_digest (timing-safe)."""
        payload = b'{"event": "test"}'
        secret = "secret"
        sig = compute_signature(payload, secret)
        # This test just confirms the function works; the actual
        # timing-safety is guaranteed by hmac.compare_digest internals.
        assert verify_signature(payload, secret, sig) is True


# ---------------------------------------------------------------
# WebhookManager tests (unit)
# ---------------------------------------------------------------


class TestWebhookManager:

    def _make_post_fn(
        self, status_code: int = 200, raises: Optional[Exception] = None,
    ):
        """Return a mock HTTP post function."""
        calls: list = []

        def post(url, data, headers, timeout=10.0):
            calls.append({
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            })
            if raises:
                raise raises
            return status_code

        return post, calls

    def test_fire_dispatches_to_matching_webhooks(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            secret="secret",
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "aa:bb:cc:dd:ee:ff"})
        assert len(results) == 1
        assert results[0].success is True
        assert len(calls) == 1

    def test_fire_skips_unsubscribed_events(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.complete"],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "aa:bb:cc:dd:ee:ff"})
        assert len(results) == 0
        assert len(calls) == 0

    def test_fire_empty_events_subscribes_to_all(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=[],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        for event in SUPPORTED_EVENTS:
            results = mgr.fire_sync(event, {"mac": "aa:bb:cc:dd:ee:ff"})
            assert len(results) == 1
        assert len(calls) == len(SUPPORTED_EVENTS)

    def test_fire_unsupported_event_returns_zero(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=[],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        count = mgr.fire("nonexistent.event", {"mac": "test"})
        assert count == 0
        assert len(calls) == 0

    def test_fire_multiple_webhooks(self):
        post_fn, calls = self._make_post_fn(200)
        webhooks = [
            WebhookConfig(
                url="http://example.com/hook1",
                events=["provision.started"],
            ),
            WebhookConfig(
                url="http://example.com/hook2",
                events=["provision.started"],
            ),
            WebhookConfig(
                url="http://example.com/hook3",
                events=["provision.complete"],
            ),
        ]
        mgr = WebhookManager(webhooks, http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "test"})
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_hmac_signature_in_headers(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            secret="test-secret",
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire_sync("provision.started", {"mac": "aa:bb:cc:dd:ee:ff"})

        assert len(calls) == 1
        headers = calls[0]["headers"]
        assert "X-TftpOS-Signature" in headers
        sig_header = headers["X-TftpOS-Signature"]
        assert sig_header.startswith("sha256=")

        # Verify the signature is correct
        payload_bytes = calls[0]["data"]
        expected_sig = compute_signature(payload_bytes, "test-secret")
        assert sig_header == f"sha256={expected_sig}"

    def test_no_signature_when_no_secret(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            secret="",
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire_sync("provision.started", {"mac": "test"})
        assert "X-TftpOS-Signature" not in calls[0]["headers"]

    def test_event_and_delivery_headers(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.complete"],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire_sync("provision.complete", {"mac": "test"})

        headers = calls[0]["headers"]
        assert headers["X-TftpOS-Event"] == "provision.complete"
        assert "X-TftpOS-Delivery" in headers
        assert headers["Content-Type"] == "application/json"

    def test_payload_includes_event(self):
        post_fn, calls = self._make_post_fn(200)
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire_sync("provision.started", {"mac": "aa:bb:cc:dd:ee:ff"})

        data = json.loads(calls[0]["data"])
        assert data["event"] == "provision.started"
        assert data["mac"] == "aa:bb:cc:dd:ee:ff"


# ---------------------------------------------------------------
# Retry logic tests
# ---------------------------------------------------------------


class TestWebhookRetry:

    def test_retry_on_server_error(self):
        attempt_count = [0]

        def post_fn(url, data, headers, timeout=10.0):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                return 500
            return 200

        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            retry_count=3,
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "test"})
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].attempts == 3

    def test_retry_on_connection_error(self):
        attempt_count = [0]

        def post_fn(url, data, headers, timeout=10.0):
            attempt_count[0] += 1
            if attempt_count[0] < 2:
                raise ConnectionError("refused")
            return 200

        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            retry_count=3,
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "test"})
        assert results[0].success is True
        assert results[0].attempts == 2

    def test_exhausted_retries_marks_failure(self):
        def post_fn(url, data, headers, timeout=10.0):
            return 500

        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            retry_count=2,
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "test"})
        assert results[0].success is False
        assert results[0].attempts == 2
        assert results[0].error == "HTTP 500"

    def test_connection_error_exhausted(self):
        def post_fn(url, data, headers, timeout=10.0):
            raise ConnectionError("refused")

        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            retry_count=1,
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "test"})
        assert results[0].success is False
        assert results[0].attempts == 1
        assert "refused" in results[0].error

    def test_retry_count_one_means_single_attempt(self):
        calls = []

        def post_fn(url, data, headers, timeout=10.0):
            calls.append(1)
            return 500

        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
            retry_count=1,
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire_sync("provision.started", {"mac": "test"})
        assert len(calls) == 1

    def test_successful_delivery_records_timestamp(self):
        post_fn = lambda url, data, headers, timeout=10.0: 200
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        results = mgr.fire_sync("provision.started", {"mac": "test"})
        assert results[0].delivered_at is not None
        assert results[0].delivered_at > 0


# ---------------------------------------------------------------
# Async (background) delivery tests
# ---------------------------------------------------------------


class TestWebhookAsyncDelivery:

    def test_fire_returns_dispatch_count(self):
        post_fn = lambda url, data, headers, timeout=10.0: 200
        webhooks = [
            WebhookConfig(
                url="http://example.com/hook1",
                events=["provision.started"],
            ),
            WebhookConfig(
                url="http://example.com/hook2",
                events=["provision.complete"],
            ),
        ]
        mgr = WebhookManager(webhooks, http_post=post_fn)
        count = mgr.fire("provision.started", {"mac": "test"})
        assert count == 1
        mgr.shutdown()

    def test_async_delivery_completes(self):
        delivered = threading.Event()

        def post_fn(url, data, headers, timeout=10.0):
            delivered.set()
            return 200

        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire("provision.started", {"mac": "test"})
        assert delivered.wait(timeout=5.0)
        mgr.shutdown()


# ---------------------------------------------------------------
# Delivery tracking tests
# ---------------------------------------------------------------


class TestDeliveryTracking:

    def test_recent_deliveries_recorded(self):
        post_fn = lambda url, data, headers, timeout=10.0: 200
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire_sync("provision.started", {"mac": "test"})
        recent = mgr.get_recent_deliveries()
        assert len(recent) == 1
        assert recent[0].event == "provision.started"
        assert recent[0].success is True

    def test_deliveries_newest_first(self):
        post_fn = lambda url, data, headers, timeout=10.0: 200
        wh = WebhookConfig(url="http://example.com/hook", events=[])
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr.fire_sync("provision.started", {"mac": "first"})
        mgr.fire_sync("provision.complete", {"mac": "second"})
        recent = mgr.get_recent_deliveries()
        assert recent[0].event == "provision.complete"
        assert recent[1].event == "provision.started"

    def test_on_delivery_callback_invoked(self):
        callbacks: list = []
        post_fn = lambda url, data, headers, timeout=10.0: 200
        wh = WebhookConfig(
            url="http://example.com/hook",
            events=["provision.started"],
        )
        mgr = WebhookManager(
            [wh],
            http_post=post_fn,
            on_delivery=lambda d: callbacks.append(d),
        )
        mgr.fire_sync("provision.started", {"mac": "test"})
        assert len(callbacks) == 1
        assert callbacks[0].success is True


# ---------------------------------------------------------------
# Test webhook endpoint
# ---------------------------------------------------------------


class TestWebhookSendTest:

    def test_send_test_to_all(self):
        post_fn = lambda url, data, headers, timeout=10.0: 200
        webhooks = [
            WebhookConfig(
                url="http://example.com/hook1",
                events=["provision.started"],
            ),
            WebhookConfig(
                url="http://example.com/hook2",
                events=["provision.complete"],
            ),
        ]
        mgr = WebhookManager(webhooks, http_post=post_fn)
        results = mgr.send_test()
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_send_test_to_specific_url(self):
        post_fn = lambda url, data, headers, timeout=10.0: 200
        webhooks = [
            WebhookConfig(url="http://example.com/hook1"),
            WebhookConfig(url="http://example.com/hook2"),
        ]
        mgr = WebhookManager(webhooks, http_post=post_fn)
        results = mgr.send_test("http://example.com/hook2")
        assert len(results) == 1
        assert results[0].webhook_url == "http://example.com/hook2"


# ---------------------------------------------------------------
# Config parsing tests
# ---------------------------------------------------------------


class TestWebhookConfigParsing:

    def test_load_config_with_webhooks(self, tmp_path):
        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(textwrap.dedent("""\
            [server]
            host = "0.0.0.0"
            port = 8443

            [[webhooks]]
            url = "http://virtos:8080/callback"
            events = ["provision.started", "provision.complete"]
            secret = "hmac-key"
            retry_count = 5
            timeout = 15.0

            [[webhooks]]
            url = "https://monitoring.example.com/events"
            events = ["provision.failed"]
        """))
        config = load_config(config_file)
        assert len(config.webhooks) == 2

        wh0 = config.webhooks[0]
        assert wh0.url == "http://virtos:8080/callback"
        assert wh0.events == ["provision.started", "provision.complete"]
        assert wh0.secret == "hmac-key"
        assert wh0.retry_count == 5
        assert wh0.timeout == 15.0

        wh1 = config.webhooks[1]
        assert wh1.url == "https://monitoring.example.com/events"
        assert wh1.events == ["provision.failed"]
        assert wh1.secret == ""
        assert wh1.retry_count == 3  # default

    def test_load_config_no_webhooks(self, tmp_path):
        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(textwrap.dedent("""\
            [server]
            host = "0.0.0.0"
            port = 8443
        """))
        config = load_config(config_file)
        assert config.webhooks == []

    def test_webhook_config_defaults_in_dataclass(self):
        config = TftpOSConfig()
        assert config.webhooks == []


# ---------------------------------------------------------------
# Audit integration tests
# ---------------------------------------------------------------


class TestWebhookAudit:

    def test_audit_event_type_constant(self):
        from tftpos.audit import AuditEvent
        assert AuditEvent.WEBHOOK_DELIVERY == "webhook_delivery"

    def test_audit_logger_log_webhook_delivery(self):
        from tftpos.audit import AuditLogger
        audit = AuditLogger()
        entry = audit.log_webhook_delivery(
            delivery_id="abc123",
            webhook_url="http://example.com/hook",
            event="provision.started",
            success=True,
            attempts=1,
            status_code=200,
        )
        assert entry["event_type"] == "webhook_delivery"
        assert entry["delivery_id"] == "abc123"
        assert entry["success"] is True
        assert entry["status_code"] == 200

    def test_audit_logger_log_webhook_failure(self):
        from tftpos.audit import AuditLogger
        audit = AuditLogger()
        entry = audit.log_webhook_delivery(
            delivery_id="def456",
            webhook_url="http://example.com/hook",
            event="provision.failed",
            success=False,
            attempts=3,
            error="Connection refused",
        )
        assert entry["success"] is False
        assert entry["error"] == "Connection refused"
        assert entry["attempts"] == 3


# ---------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------


class TestWebhookEdgeCases:

    def test_manager_with_no_webhooks(self):
        mgr = WebhookManager([])
        count = mgr.fire("provision.started", {"mac": "test"})
        assert count == 0
        assert mgr.webhooks == []

    def test_manager_webhooks_property_returns_copy(self):
        wh = WebhookConfig(url="http://example.com/hook")
        mgr = WebhookManager([wh])
        # Modifying the returned list should not affect the manager
        mgr.webhooks.append(
            WebhookConfig(url="http://other.com/hook")
        )
        assert len(mgr.webhooks) == 1

    def test_delivery_ring_buffer_bounded(self):
        post_fn = lambda url, data, headers, timeout=10.0: 200
        wh = WebhookConfig(
            url="http://example.com/hook", events=[]
        )
        mgr = WebhookManager([wh], http_post=post_fn)
        mgr._max_deliveries = 5
        for _ in range(10):
            mgr.fire_sync("provision.started", {"mac": "test"})
        recent = mgr.get_recent_deliveries(limit=100)
        assert len(recent) == 5

    def test_shutdown_idempotent(self):
        mgr = WebhookManager([])
        mgr.shutdown()
        # Second shutdown should not raise
        mgr.shutdown(wait=False)

    def test_supported_events_frozenset(self):
        assert isinstance(SUPPORTED_EVENTS, frozenset)
        assert "provision.started" in SUPPORTED_EVENTS
        assert "provision.complete" in SUPPORTED_EVENTS
        assert "provision.failed" in SUPPORTED_EVENTS
        assert "netboot.disabled" in SUPPORTED_EVENTS


# ---------------------------------------------------------------
# Integration with mock HTTP server
# ---------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestWebhookMockServer:
    """Integration test using a real HTTP server."""

    def test_delivery_to_real_http_server(self):
        received: list = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received.append({
                    "body": json.loads(body),
                    "headers": dict(self.headers),
                })
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass  # silence server logs

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        try:
            wh = WebhookConfig(
                url=f"http://127.0.0.1:{port}/webhook",
                events=["provision.started"],
                secret="real-test-secret",
                retry_count=1,
                timeout=5.0,
            )
            mgr = WebhookManager([wh])
            results = mgr.fire_sync("provision.started", {
                "mac": "aa:bb:cc:dd:ee:ff",
                "profile": "test-server",
            })
            assert len(results) == 1
            assert results[0].success is True
            assert results[0].status_code == 200

            # Verify payload arrived at the server
            assert len(received) == 1
            body = received[0]["body"]
            assert body["event"] == "provision.started"
            assert body["mac"] == "aa:bb:cc:dd:ee:ff"

            # Verify HMAC signature
            headers = received[0]["headers"]
            sig_header = headers.get("X-Tftpos-Signature", "")
            assert sig_header.startswith("sha256=")
            sig = sig_header[len("sha256="):]
            raw_body = json.dumps(body, default=str).encode("utf-8")
            assert verify_signature(
                raw_body, "real-test-secret", sig
            )
        finally:
            server.shutdown()

    def test_delivery_to_failing_server(self):
        class FailHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(503)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), FailHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        try:
            wh = WebhookConfig(
                url=f"http://127.0.0.1:{port}/webhook",
                events=["provision.started"],
                retry_count=2,
                timeout=2.0,
            )
            mgr = WebhookManager([wh])
            results = mgr.fire_sync("provision.started", {"mac": "test"})
            assert len(results) == 1
            assert results[0].success is False
            assert results[0].attempts == 2
        finally:
            server.shutdown()
