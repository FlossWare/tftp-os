"""Webhook delivery system for provisioning events.

Fires HTTP POST callbacks with HMAC-SHA256 signed payloads
when provisioning state changes occur.  Delivery is performed
in background threads so it never blocks the provisioning flow.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("tftpos.webhooks")


# Supported event types that can trigger webhooks
SUPPORTED_EVENTS = frozenset({
    "provision.started",
    "provision.complete",
    "provision.failed",
    "netboot.disabled",
})


@dataclass
class WebhookConfig:
    """Configuration for a single webhook endpoint."""

    url: str
    events: List[str] = field(default_factory=list)
    secret: str = ""
    retry_count: int = 3
    timeout: float = 10.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"webhook url must use http or https: {self.url!r}"
            )


@dataclass
class WebhookDelivery:
    """Record of a single webhook delivery attempt."""

    delivery_id: str
    webhook_url: str
    event: str
    payload: Dict[str, Any]
    success: bool = False
    attempts: int = 0
    status_code: Optional[int] = None
    error: Optional[str] = None
    delivered_at: Optional[float] = None


def compute_signature(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for a webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    payload_bytes: bytes, secret: str, signature: str
) -> bool:
    """Verify an HMAC-SHA256 signature against a payload."""
    expected = compute_signature(payload_bytes, secret)
    return hmac.compare_digest(expected, signature)


class WebhookManager:
    """Manages webhook configuration and async delivery.

    Webhook POSTs are dispatched in a background thread pool
    so they never block the caller.  Failed deliveries are
    retried with exponential backoff (1s, 2s, 4s by default).
    """

    def __init__(
        self,
        webhooks: Optional[List[WebhookConfig]] = None,
        *,
        max_workers: int = 4,
        http_post: Optional[Callable] = None,
        on_delivery: Optional[Callable[[WebhookDelivery], None]] = None,
        supported_events: Optional[FrozenSet[str]] = None,
    ) -> None:
        self._webhooks: List[WebhookConfig] = list(webhooks or [])
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="webhook",
        )
        # Allow injecting a custom HTTP post function for testing
        self._http_post = http_post or self._default_http_post
        # Optional callback invoked after each delivery (for audit)
        self._on_delivery = on_delivery
        self._supported_events = supported_events or SUPPORTED_EVENTS
        self._lock = threading.Lock()
        self._deliveries: list[WebhookDelivery] = []
        self._max_deliveries = 500  # ring-buffer size

    @property
    def webhooks(self) -> List[WebhookConfig]:
        """Return a copy of the configured webhooks."""
        return list(self._webhooks)

    def fire(
        self,
        event: str,
        payload: Dict[str, Any],
    ) -> int:
        """Fire an event to all matching webhooks.

        Returns the number of webhooks that were dispatched.
        Delivery happens asynchronously in background threads.
        """
        if event not in self._supported_events:
            logger.warning("Ignoring unsupported event: %s", event)
            return 0

        dispatched = 0
        for wh in self._webhooks:
            if wh.events and event not in wh.events:
                continue
            # If events list is empty, subscribe to all events
            self._executor.submit(
                self._deliver, wh, event, payload
            )
            dispatched += 1

        return dispatched

    def fire_sync(
        self,
        event: str,
        payload: Dict[str, Any],
    ) -> List[WebhookDelivery]:
        """Fire an event synchronously (useful for testing).

        Returns the list of delivery results.
        """
        if event not in self._supported_events:
            return []

        results: List[WebhookDelivery] = []
        for wh in self._webhooks:
            if wh.events and event not in wh.events:
                continue
            delivery = self._deliver(wh, event, payload)
            results.append(delivery)
        return results

    def send_test(
        self, webhook_url: Optional[str] = None
    ) -> List[WebhookDelivery]:
        """Send a test webhook to one or all configured endpoints.

        This is a synchronous call so the API can return results.
        """
        test_payload = {
            "event": "test",
            "message": "TftpOS webhook test",
            "timestamp": time.time(),
        }
        targets = self._webhooks
        if webhook_url:
            targets = [
                wh for wh in self._webhooks
                if wh.url == webhook_url
            ]
        results: List[WebhookDelivery] = []
        for wh in targets:
            delivery = self._deliver(
                wh, "test", test_payload, is_test=True
            )
            results.append(delivery)
        return results

    def get_recent_deliveries(
        self, limit: int = 50
    ) -> List[WebhookDelivery]:
        """Return the most recent deliveries (newest first)."""
        with self._lock:
            entries = list(self._deliveries)
        entries.reverse()
        return entries[:limit]

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the background thread pool."""
        self._executor.shutdown(wait=wait)

    def _deliver(
        self,
        wh: WebhookConfig,
        event: str,
        payload: Dict[str, Any],
        *,
        is_test: bool = False,
    ) -> WebhookDelivery:
        """Deliver a webhook with retry and exponential backoff."""
        delivery_id = uuid.uuid4().hex[:16]
        delivery_payload = {**payload, "event": event}
        delivery = WebhookDelivery(
            delivery_id=delivery_id,
            webhook_url=wh.url,
            event=event,
            payload=delivery_payload,
        )

        payload_bytes = json.dumps(
            delivery_payload, default=str
        ).encode("utf-8")

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "X-TftpOS-Event": event,
            "X-TftpOS-Delivery": delivery_id,
        }
        if wh.secret:
            sig = compute_signature(payload_bytes, wh.secret)
            headers["X-TftpOS-Signature"] = f"sha256={sig}"

        max_attempts = max(1, wh.retry_count)
        backoff_base = 1.0  # seconds

        for attempt in range(max_attempts):
            delivery.attempts = attempt + 1
            try:
                status_code = self._http_post(
                    wh.url,
                    payload_bytes,
                    headers,
                    timeout=wh.timeout,
                )
                delivery.status_code = status_code
                if 200 <= status_code < 300:
                    delivery.success = True
                    delivery.delivered_at = time.time()
                    logger.info(
                        "Webhook delivered id=%s url=%s event=%s "
                        "status=%d attempt=%d",
                        delivery_id, wh.url, event,
                        status_code, attempt + 1,
                    )
                    break
                else:
                    delivery.error = (
                        f"HTTP {status_code}"
                    )
                    logger.warning(
                        "Webhook failed id=%s url=%s "
                        "status=%d attempt=%d/%d",
                        delivery_id, wh.url,
                        status_code, attempt + 1,
                        max_attempts,
                    )
            except Exception as exc:
                delivery.error = str(exc)
                logger.warning(
                    "Webhook error id=%s url=%s "
                    "error=%s attempt=%d/%d",
                    delivery_id, wh.url,
                    exc, attempt + 1, max_attempts,
                )

            # Exponential backoff: 1s, 2s, 4s, ...
            if attempt < max_attempts - 1:
                delay = backoff_base * (2 ** attempt)
                time.sleep(delay)

        if not delivery.success:
            logger.error(
                "Webhook exhausted retries id=%s url=%s event=%s",
                delivery_id, wh.url, event,
            )

        # Record delivery
        with self._lock:
            self._deliveries.append(delivery)
            if len(self._deliveries) > self._max_deliveries:
                self._deliveries = self._deliveries[
                    -self._max_deliveries:
                ]

        # Notify audit callback
        if self._on_delivery is not None:
            try:
                self._on_delivery(delivery)
            except Exception:
                logger.exception("on_delivery callback failed")

        return delivery

    @staticmethod
    def _default_http_post(
        url: str,
        data: bytes,
        headers: Dict[str, str],
        timeout: float = 10.0,
    ) -> int:
        """Perform an HTTP POST using urllib (no extra deps)."""
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError

        req = Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.status
        except HTTPError as exc:
            return exc.code
        except URLError as exc:
            raise ConnectionError(
                f"webhook POST to {url} failed: {exc.reason}"
            ) from exc
