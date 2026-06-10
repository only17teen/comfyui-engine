"""ComfyUI Engine v5.1 - Circuit Breaker.

Extracted from core.py. Prevents cascading failures when the ComfyUI GPU
server is overloaded or down.

Improvements vs original:
- State-change callbacks (observability hook)
- Cleaner HALF_OPEN logic: max_calls checked before increment
- get_status() helper for dashboards / health endpoints
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable

from engine.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker state."""

    CLOSED = auto()    # Normal operation
    OPEN = auto()      # Failing - reject all calls
    HALF_OPEN = auto() # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3
    success_threshold: int = 2


class CircuitBreakerOpenError(Exception):
    """Raised when circuit is OPEN and calls are rejected."""


class CircuitBreaker:
    """Async circuit breaker protecting an external service.

    Usage::

        cb = CircuitBreaker("comfyui", CircuitBreakerConfig(), metrics)
        result = await cb.call(my_async_fn, arg1, arg2)
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig,
        metrics: MetricsCollector,
        on_state_change: Callable[[CircuitState, CircuitState], None] | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.metrics = metrics
        self._on_state_change = on_state_change
        self._lock: asyncio.Lock | None = None  # lazy

        self.state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0
        self._last_failure_time: float | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _transition(self, new_state: CircuitState) -> None:
        """Perform a state transition and fire callback."""
        old_state = self.state
        self.state = new_state
        logger.info("[%s] %s -> %s", self.name, old_state.name, new_state.name)
        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state)
            except Exception:  # noqa: BLE001
                logger.exception("State-change callback raised")

    async def call(self, coro_factory: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute coroutine with circuit breaker protection."""
        async with self._get_lock():
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._transition(CircuitState.HALF_OPEN)
                    self._half_open_calls = 0
                    self._successes = 0
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit {self.name!r} is OPEN "
                        f"(last failure {self._last_failure_time:.1f})"
                    )

            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        f"Circuit {self.name!r} HALF_OPEN quota exhausted"
                    )
                self._half_open_calls += 1

        try:
            result = await coro_factory(*args, **kwargs)
        except Exception:
            await self._on_failure()
            raise

        await self._on_success()
        return result

    def _should_attempt_reset(self) -> bool:
        if self._last_failure_time is None:
            return True
        return time.monotonic() - self._last_failure_time >= self.config.recovery_timeout

    async def _on_success(self) -> None:
        async with self._get_lock():
            if self.state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.config.success_threshold:
                    self._transition(CircuitState.CLOSED)
                    self._failures = 0
            else:
                # Slowly drain failure counter on healthy calls
                self._failures = max(0, self._failures - 1)

    async def _on_failure(self) -> None:
        async with self._get_lock():
            self._failures += 1
            self._last_failure_time = time.monotonic()

            if self.state == CircuitState.HALF_OPEN:
                self._transition(CircuitState.OPEN)
                await self.metrics.inc("circuit_breaker_trips")
            elif (
                self.state == CircuitState.CLOSED
                and self._failures >= self.config.failure_threshold
            ):
                self._transition(CircuitState.OPEN)
                await self.metrics.inc("circuit_breaker_trips")

    def get_status(self) -> dict[str, Any]:
        """Return circuit state for health / dashboard endpoints."""
        return {
            "name": self.name,
            "state": self.state.name,
            "failures": self._failures,
            "last_failure_time": self._last_failure_time,
        }
