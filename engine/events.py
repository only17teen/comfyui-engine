"""ComfyUI Engine v5.1 - Event Bus.

Lightweight, typed publish/subscribe event bus that decouples components:
- API server fires events without knowing about webhooks or metrics.
- Webhooks subscribe to job events without importing the API server.
- Metrics auto-update from events without being passed explicitly.

Usage::

    bus = EventBus()

    @bus.subscribe(JobEvent)
    async def on_job(event: JobEvent) -> None:
        print(event.job_id, event.status)

    await bus.publish(JobEvent(job_id="abc", status="completed"))
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

E = TypeVar("E", bound="BaseEvent")
HandlerFn = Callable[[Any], Awaitable[None] | None]


@dataclass
class BaseEvent:
    """Base class for all engine events."""

    timestamp: float = field(default_factory=time.time)


@dataclass
class JobEvent(BaseEvent):
    """Fired when a job changes state."""

    job_id: str = ""
    status: str = ""  # pending | running | completed | error | cancelled
    prompt_id: str = ""
    error_msg: str = ""
    processing_time: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueueEvent(BaseEvent):
    """Fired when queue depth changes."""

    depth: int = 0
    pending: int = 0
    running: int = 0


@dataclass
class CircuitBreakerEvent(BaseEvent):
    """Fired when a circuit breaker changes state."""

    name: str = ""
    old_state: str = ""
    new_state: str = ""


@dataclass
class HealthEvent(BaseEvent):
    """Fired after a health check run."""

    is_healthy: bool = False
    is_ready: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)


class EventBus:
    """Async publish / subscribe event bus.

    - Handlers run concurrently via ``asyncio.gather``.
    - A failing handler is logged but does NOT block other handlers.
    - Supports both coroutine functions and plain sync callables.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[BaseEvent], list[HandlerFn]] = {}
        self._global_handlers: list[HandlerFn] = []

    def subscribe(self, event_type: type[E]) -> Callable[[HandlerFn], HandlerFn]:
        """Decorator that subscribes a function to *event_type* events."""

        def decorator(fn: HandlerFn) -> HandlerFn:
            self._handlers.setdefault(event_type, []).append(fn)
            return fn

        return decorator

    def subscribe_all(self, fn: HandlerFn) -> HandlerFn:
        """Subscribe *fn* to every event type (wildcard)."""
        self._global_handlers.append(fn)
        return fn

    async def publish(self, event: BaseEvent) -> None:
        """Publish *event* to all relevant subscribers."""
        handlers = self._handlers.get(type(event), []) + self._global_handlers
        if not handlers:
            return

        async def _safe_call(fn: HandlerFn, ev: BaseEvent) -> None:
            try:
                result = fn(ev)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Event handler %s failed for %s", fn, type(ev).__name__
                )

        await asyncio.gather(*[_safe_call(fn, event) for fn in handlers])

    def emit_sync(self, event: BaseEvent) -> None:
        """Fire-and-forget from synchronous code (creates a task)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "emit_sync called outside event loop; event dropped: %s", event
            )
            return
        loop.create_task(self.publish(event))


# Module-level default bus so components can import and use it without DI:
default_bus: EventBus = EventBus()
