"""Token-bucket rate limiter for TftpOS API.

Uses only the standard library -- no external dependencies.
Provides per-IP rate limiting with configurable limits for
different endpoint groups (TFTP, API, auth).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

try:
    from fastapi import Request, Response
    from starlette.middleware.base import (
        BaseHTTPMiddleware,
        RequestResponseEndpoint,
    )
    from starlette.responses import JSONResponse
except ImportError:
    Request = Response = None
    BaseHTTPMiddleware = object
    RequestResponseEndpoint = None
    JSONResponse = None

logger = logging.getLogger("tftpos.rate_limit")


class EndpointGroup(str, Enum):
    """Classifies endpoints into rate-limit tiers."""

    TFTP = "tftp"
    API = "api"
    AUTH = "auth"


@dataclass
class RateLimitConfig:
    """Configuration for a single rate-limit tier."""

    requests_per_minute: float = 60.0
    burst: int = 10

    @property
    def rate(self) -> float:
        """Tokens added per second."""
        return self.requests_per_minute / 60.0


@dataclass
class _Bucket:
    """Internal token-bucket state for a single key."""

    tokens: float
    capacity: int
    last_refill: float


class RateLimiter:
    """Thread-safe per-key token-bucket rate limiter.

    Each key (typically a client IP) gets an independent bucket.
    Tokens are added at a fixed rate up to a burst capacity.
    """

    def __init__(
        self,
        requests_per_minute: float = 60.0,
        burst: int = 10,
    ) -> None:
        self._rate = requests_per_minute / 60.0  # tokens/sec
        self._burst = burst
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @property
    def rate(self) -> float:
        """Tokens added per second."""
        return self._rate

    @property
    def burst(self) -> int:
        return self._burst

    def check(self, key: str) -> bool:
        """Consume one token for *key*.

        Returns ``True`` if the request is allowed, ``False`` if
        rate-limited.
        """
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                # First request from this key -- full bucket minus
                # the token we are about to consume.
                bucket = _Bucket(
                    tokens=self._burst - 1,
                    capacity=self._burst,
                    last_refill=now,
                )
                self._buckets[key] = bucket
                return True

            # Refill tokens based on elapsed time.
            elapsed = now - bucket.last_refill
            bucket.tokens = min(
                bucket.capacity,
                bucket.tokens + elapsed * self._rate,
            )
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False

    def remaining(self, key: str) -> int:
        """Return the (approximate) number of tokens left for *key*."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return self._burst
            elapsed = now - bucket.last_refill
            tokens = min(
                bucket.capacity,
                bucket.tokens + elapsed * self._rate,
            )
            return int(tokens)

    def retry_after(self, key: str) -> float:
        """Seconds until the next token becomes available for *key*."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0.0
            elapsed = now - bucket.last_refill
            tokens = min(
                bucket.capacity,
                bucket.tokens + elapsed * self._rate,
            )
            if tokens >= 1.0:
                return 0.0
            needed = 1.0 - tokens
            if self._rate <= 0:
                return 0.0
            return needed / self._rate

    def reset(self) -> None:
        """Clear all tracked buckets (useful for testing)."""
        with self._lock:
            self._buckets.clear()

    def cleanup(self, max_age_seconds: float = 3600.0) -> int:
        """Remove stale bucket entries older than *max_age_seconds*.

        Returns the number of entries removed.
        """
        now = time.monotonic()
        removed = 0
        with self._lock:
            stale_keys = [
                k
                for k, b in self._buckets.items()
                if (now - b.last_refill) > max_age_seconds
            ]
            for k in stale_keys:
                del self._buckets[k]
                removed += 1
        return removed


def classify_endpoint(path: str) -> EndpointGroup:
    """Classify a request path into a rate-limit group."""
    if path.startswith("/api/v1/boot/") or path.startswith(
        "/api/v1/autoinstall/"
    ):
        return EndpointGroup.TFTP
    if path.startswith("/api/v1/auth/"):
        return EndpointGroup.AUTH
    return EndpointGroup.API


# --------------- Global limiter registry ---------------

_limiters: Dict[EndpointGroup, RateLimiter] = {}
_enabled: bool = False


def configure_rate_limiting(
    enabled: bool = False,
    tftp_rpm: float = 300.0,
    tftp_burst: int = 50,
    api_rpm: float = 60.0,
    api_burst: int = 20,
    auth_rpm: float = 10.0,
    auth_burst: int = 5,
) -> None:
    """Initialise global rate limiters from configuration values."""
    global _enabled
    _enabled = enabled
    _limiters[EndpointGroup.TFTP] = RateLimiter(tftp_rpm, tftp_burst)
    _limiters[EndpointGroup.API] = RateLimiter(api_rpm, api_burst)
    _limiters[EndpointGroup.AUTH] = RateLimiter(auth_rpm, auth_burst)


def get_limiter(group: EndpointGroup) -> Optional[RateLimiter]:
    """Return the limiter for *group*, or ``None`` if not configured."""
    return _limiters.get(group)


def is_enabled() -> bool:
    return _enabled


def reset_all_limiters() -> None:
    """Reset every configured limiter (for testing)."""
    for limiter in _limiters.values():
        limiter.reset()


def _client_ip(request: Request) -> str:
    """Extract the client IP from a request.

    Respects X-Forwarded-For when present (first entry).
    Falls back to the direct client address.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


# Paths that are exempt from rate limiting.
_EXEMPT_PATHS = frozenset({"/api/v1/health", "/metrics"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that applies per-IP token-bucket rate limiting."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not _enabled:
            return await call_next(request)

        path = request.url.path
        if path in _EXEMPT_PATHS:
            return await call_next(request)

        group = classify_endpoint(path)
        limiter = _limiters.get(group)
        if limiter is None:
            return await call_next(request)

        ip = _client_ip(request)
        key = f"{group.value}:{ip}"

        if not limiter.check(key):
            retry = limiter.retry_after(key)
            logger.warning(
                "Rate limited ip=%s path=%s group=%s "
                "retry_after=%.1f",
                ip,
                path,
                group.value,
                retry,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please try again later."
                },
                headers={
                    "Retry-After": str(int(retry) + 1),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time() + retry + 1)),
                },
            )

        response = await call_next(request)
        remaining = limiter.remaining(key)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(
            int(time.time() + 60)
        )
        return response
