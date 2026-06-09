"""ComfyUI Async Generation Engine v5.1 - Metrics Collection.

Extracted from core.py for single-responsibility.
Key fix: lazy asyncio.Lock() initialisation — never creates Lock in __init__
so the class is safe to instantiate outside an event loop.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MetricsSnapshot:
    """Point-in-time metrics snapshot."""

    timestamp: float
    jobs_submitted: int = 0
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_timeout: int = 0
    total_wait_time: float = 0.0
    total_processing_time: float = 0.0
    download_bytes: int = 0
    download_errors: int = 0
    api_errors: int = 0
    retries_total: int = 0
    circuit_breaker_trips: int = 0
    queue_depth: int = 0
    active_workers: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetricsCollector:
    """Asyncio-safe metrics collector with Prometheus-style counters/gauges/histograms.

    Lock is created lazily on first use so instances can be created at module
    import time without a running event loop.
    """

    def __init__(self, window_size: int = 1000) -> None:
        self._lock: asyncio.Lock | None = None  # FIX: lazy init
        self._counters: dict[str, int | float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, deque[float]] = {}
        self._window_size = window_size
        self._start_time = time.monotonic()  # FIX: use monotonic for uptime

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create lock on first use inside event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def inc(self, metric: str, value: int | float = 1) -> None:
        """Increment a counter."""
        async with self._get_lock():
            self._counters[metric] = self._counters.get(metric, 0) + value

    async def dec(self, metric: str, value: int | float = 1) -> None:
        """Decrement a counter."""
        async with self._get_lock():
            self._counters[metric] = self._counters.get(metric, 0) - value

    async def gauge(self, metric: str, value: float) -> None:
        """Set a gauge."""
        async with self._get_lock():
            self._gauges[metric] = value

    async def observe(self, metric: str, value: float) -> None:
        """Record a histogram observation."""
        async with self._get_lock():
            if metric not in self._histograms:
                self._histograms[metric] = deque(maxlen=self._window_size)
            self._histograms[metric].append(value)

    async def snapshot(self) -> MetricsSnapshot:
        """Return current metrics as a typed snapshot."""
        async with self._get_lock():
            return MetricsSnapshot(
                timestamp=time.time(),
                jobs_submitted=int(self._counters.get("jobs_submitted", 0)),
                jobs_completed=int(self._counters.get("jobs_completed", 0)),
                jobs_failed=int(self._counters.get("jobs_failed", 0)),
                jobs_timeout=int(self._counters.get("jobs_timeout", 0)),
                total_wait_time=float(self._counters.get("total_wait_time", 0.0)),
                total_processing_time=float(self._counters.get("total_processing_time", 0.0)),
                download_bytes=int(self._counters.get("download_bytes", 0)),
                download_errors=int(self._counters.get("download_errors", 0)),
                api_errors=int(self._counters.get("api_errors", 0)),
                retries_total=int(self._counters.get("retries_total", 0)),
                circuit_breaker_trips=int(self._counters.get("circuit_breaker_trips", 0)),
                queue_depth=int(self._gauges.get("queue_depth", 0)),
                active_workers=int(self._gauges.get("active_workers", 0)),
            )

    async def report(self) -> dict[str, Any]:
        """Generate full report with histogram percentiles."""
        async with self._get_lock():
            result: dict[str, Any] = {
                "uptime_seconds": time.monotonic() - self._start_time,
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {},
            }
            for name, values in self._histograms.items():
                if not values:
                    continue
                sv = sorted(values)
                n = len(sv)
                result["histograms"][name] = {
                    "count": n,
                    "min": sv[0],
                    "max": sv[-1],
                    "mean": sum(sv) / n,
                    "p50": sv[n // 2],
                    "p95": sv[max(0, int(n * 0.95) - 1)] if n >= 20 else sv[-1],
                    "p99": sv[max(0, int(n * 0.99) - 1)] if n >= 100 else sv[-1],
                }
            return result

    def prometheus_lines(self) -> list[str]:
        """Return Prometheus text-format lines (synchronous snapshot)."""
        lines: list[str] = []
        for name, val in self._counters.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines += [
                f"# HELP comfyui_{safe} Counter: {safe}",
                f"# TYPE comfyui_{safe} counter",
                f"comfyui_{safe} {val}",
            ]
        for name, val in self._gauges.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines += [
                f"# HELP comfyui_{safe} Gauge: {safe}",
                f"# TYPE comfyui_{safe} gauge",
                f"comfyui_{safe} {val}",
            ]
        return lines
