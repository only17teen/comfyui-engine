"""ComfyUI Engine v5.1 - Health Check Framework.

Provides structured readiness / liveness probes consumed by
Kubernetes, the REST API, and monitoring tools.

  Usage::

    registry = HealthRegistry()
    registry.register("comfyui", check_comfyui_connection)
    status = await registry.run_all()
    if status.is_ready:
        ...
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Individual check outcome."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    latency_ms: float
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
            "message": self.message,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


@dataclass
class AggregateHealth:
    """Aggregated health across all checks."""

    checks: list[CheckResult]
    timestamp: float = field(default_factory=time.time)

    @property
    def is_healthy(self) -> bool:
        """True if every check is HEALTHY."""
        return all(c.status == HealthStatus.HEALTHY for c in self.checks)

    @property
    def is_ready(self) -> bool:
        """True if no check is UNHEALTHY (degraded is acceptable)."""
        return all(c.status != HealthStatus.UNHEALTHY for c in self.checks)

    @property
    def overall_status(self) -> HealthStatus:
        statuses = {c.status for c in self.checks}
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.overall_status.value,
            "is_healthy": self.is_healthy,
            "is_ready": self.is_ready,
            "checks": [c.to_dict() for c in self.checks],
            "timestamp": self.timestamp,
        }


# Type alias for a health check callable
HealthCheckFn = Callable[[], Awaitable[CheckResult]]


class HealthRegistry:
    """Registry of named health checks.

    Each check is an async callable that returns a CheckResult.  Checks run
    concurrently with individual timeouts so a slow check can't block others.
    """

    def __init__(self, default_timeout: float = 5.0) -> None:
        self._checks: dict[str, HealthCheckFn] = {}
        self._default_timeout = default_timeout

    def register(
        self,
        name: str,
        check_fn: HealthCheckFn,
    ) -> None:
        """Register a health check."""
        self._checks[name] = check_fn
        logger.debug("Registered health check: %s", name)

    async def _run_one(self, name: str, fn: HealthCheckFn) -> CheckResult:
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(fn(), timeout=self._default_timeout)
            result.latency_ms = (time.monotonic() - t0) * 1000
            return result
        except asyncio.TimeoutError:
            return CheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.monotonic() - t0) * 1000,
                message=f"Timed out after {self._default_timeout}s",
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.monotonic() - t0) * 1000,
                message=str(exc),
            )

    async def run_all(self) -> AggregateHealth:
        """Run all registered checks concurrently."""
        tasks = [
            asyncio.create_task(self._run_one(name, fn))
            for name, fn in self._checks.items()
        ]
        results = await asyncio.gather(*tasks)
        return AggregateHealth(checks=list(results))

    async def run_one(self, name: str) -> CheckResult:
        """Run a single check by name."""
        fn = self._checks.get(name)
        if fn is None:
            return CheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=0.0,
                message=f"Unknown check: {name!r}",
            )
        return await self._run_one(name, fn)


# ── Built-in check factories ───────────────────────────────────────────────

def make_http_check(name: str, url: str, timeout: float = 3.0) -> HealthCheckFn:
    """Return a check that GETs *url* and expects HTTP 200."""
    import aiohttp

    async def _check() -> CheckResult:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    if r.status == 200:
                        return CheckResult(name=name, status=HealthStatus.HEALTHY, latency_ms=0.0)
                    return CheckResult(
                        name=name,
                        status=HealthStatus.UNHEALTHY,
                        latency_ms=0.0,
                        message=f"HTTP {r.status}",
                    )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=name, status=HealthStatus.UNHEALTHY, latency_ms=0.0, message=str(exc)
            )

    return _check


def make_redis_check(name: str, redis_url: str) -> HealthCheckFn:
    """Return a check that pings Redis."""
    async def _check() -> CheckResult:
        try:
            import redis.asyncio as aioredis
            r = await aioredis.from_url(redis_url)
            await r.ping()
            await r.aclose()
            return CheckResult(name=name, status=HealthStatus.HEALTHY, latency_ms=0.0)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=name, status=HealthStatus.UNHEALTHY, latency_ms=0.0, message=str(exc)
            )

    return _check
