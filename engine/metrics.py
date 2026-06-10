from __future__ import annotations
from typing import Any

__all__ = ["MetricsRegistry", "get_registry"]
try:
    from opentelemetry import metrics as _otel

    _OTEL = True
except ImportError:
    _OTEL = False


class _Noop:
    def add(self, *a: Any, **k: Any) -> None:
        pass

    def record(self, *a: Any, **k: Any) -> None:
        pass


class MetricsRegistry:
    """OTEL-backed metrics registry with counter, histogram and gauge support."""

    def __init__(self, meter_name: str = "comfyui_engine") -> None:
        self._gauge: dict[str, float] = {}
        self._meter: Any = _otel.get_meter(meter_name, version="1.0") if _OTEL else None

    def _c(self, name: str, desc: str = "") -> Any:
        return (
            self._meter.create_counter(name, unit="1", description=desc)
            if self._meter
            else _Noop()
        )

    def _h(self, name: str, unit: str = "ms", desc: str = "") -> Any:
        return (
            self._meter.create_histogram(name, unit=unit, description=desc)
            if self._meter
            else _Noop()
        )

    def request_counter(self) -> Any:
        return self._c("http.server.request_count", "Total HTTP requests")

    def error_counter(self) -> Any:
        return self._c("app.error_count", "Total errors")

    def cache_hit_counter(self) -> Any:
        return self._c("app.cache.hit_count", "Cache hits")

    def cache_miss_counter(self) -> Any:
        return self._c("app.cache.miss_count", "Cache misses")

    def request_duration(self) -> Any:
        return self._h("http.server.duration", "ms", "HTTP duration")

    def queue_processing_time(self) -> Any:
        return self._h("app.queue.processing_time", "ms", "Queue duration")

    def command_duration(self) -> Any:
        return self._h("command.duration_ms", "ms", "Command duration")

    def set_queue_depth(self, v: float, labels: dict[str, str] | None = None) -> None:
        self._gauge[f"queue_depth:{labels!r}"] = v

    def set_active_connections(
        self, v: float, labels: dict[str, str] | None = None
    ) -> None:
        self._gauge[f"active_connections:{labels!r}"] = v

    def set_circuit_breaker_state(self, name: str, is_open: bool) -> None:
        self._gauge[f"circuit_breaker:{name}"] = 1.0 if is_open else 0.0

    def get_gauge_snapshot(self) -> dict[str, float]:
        return dict(self._gauge)


_registry: MetricsRegistry | None = None


def get_registry() -> MetricsRegistry:
    """OTEL-backed metrics registry with counter, histogram and gauge support."""
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry
