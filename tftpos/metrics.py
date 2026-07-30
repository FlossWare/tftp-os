"""Prometheus-compatible metrics for TftpOS.

Uses a simple dict-based approach -- no external prometheus_client
dependency required.  The ``/metrics`` endpoint serialises counters
and gauges into the Prometheus text exposition format.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Tuple


class _Counter:
    """Monotonically increasing counter with optional labels."""

    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help_text = help_text
        self._values: Dict[Tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(
        self, amount: float = 1.0, **labels: str
    ) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[key] = (
                self._values.get(key, 0.0) + amount
            )

    def get(self, **labels: str) -> float:
        key = tuple(sorted(labels.items()))
        with self._lock:
            return self._values.get(key, 0.0)

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            else:
                for label_pairs, value in sorted(
                    self._values.items()
                ):
                    label_str = self._format_labels(
                        label_pairs
                    )
                    lines.append(
                        f"{self.name}{label_str} {value}"
                    )
        return "\n".join(lines)

    @staticmethod
    def _format_labels(
        label_pairs: Tuple[Tuple[str, str], ...],
    ) -> str:
        if not label_pairs:
            return ""
        parts = [f'{k}="{v}"' for k, v in label_pairs]
        return "{" + ",".join(parts) + "}"


class _Gauge:
    """Gauge that can go up and down."""

    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help_text = help_text
        self._value: float = 0.0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value -= amount

    def get(self) -> float:
        with self._lock:
            return self._value

    def render(self) -> str:
        with self._lock:
            val = self._value
        return (
            f"# HELP {self.name} {self.help_text}\n"
            f"# TYPE {self.name} gauge\n"
            f"{self.name} {val}"
        )


# ---- Global metric instances ----

provisions_total = _Counter(
    "tftpos_provisions_total",
    "Total provisioning operations",
)

active_provisions = _Gauge(
    "tftpos_active_provisions",
    "Number of currently active provisions",
)

boot_requests_total = _Counter(
    "tftpos_boot_requests_total",
    "Total boot script requests",
)

import_operations_total = _Counter(
    "tftpos_import_operations_total",
    "Total import operations",
)

auth_attempts_total = _Counter(
    "tftpos_auth_attempts_total",
    "Total authentication attempts",
)

# Track server start time for uptime calculation
_start_time: float = time.time()


def get_uptime_seconds() -> float:
    """Return seconds since the metrics module was first imported."""
    return time.time() - _start_time


def render_metrics() -> str:
    """Render all metrics in Prometheus text exposition format."""
    sections = [
        provisions_total.render(),
        active_provisions.render(),
        boot_requests_total.render(),
        import_operations_total.render(),
        auth_attempts_total.render(),
    ]
    # Add uptime gauge
    uptime = get_uptime_seconds()
    sections.append(
        f"# HELP tftpos_uptime_seconds Seconds since server start\n"
        f"# TYPE tftpos_uptime_seconds gauge\n"
        f"tftpos_uptime_seconds {uptime:.1f}"
    )
    return "\n".join(sections) + "\n"


def reset_all() -> None:
    """Reset all metrics -- for testing only."""
    global _start_time
    provisions_total._values.clear()
    active_provisions._value = 0.0
    boot_requests_total._values.clear()
    import_operations_total._values.clear()
    auth_attempts_total._values.clear()
    _start_time = time.time()
