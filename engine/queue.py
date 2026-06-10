"""ComfyUI Engine v5.1 - Priority Job Queue.

Extracted from core.py.

Improvements:
- Dedicated module for easy unit-testing.
- Type annotations use modern union syntax.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from engine.metrics import MetricsCollector


class QueueFullError(Exception):
    """Raised when the job queue is at capacity."""


@dataclass(order=True)
class PrioritizedJob:
    """Queue item (lower priority value = dequeued sooner)."""

    priority: int
    created_at: float = field(compare=True)
    job_id: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False)
    meta: dict[str, Any] = field(compare=False)
    future: asyncio.Future[Any] = field(compare=False)


class JobQueue:
    """Async priority queue with back-pressure and optional rate limiting.

    Priority levels: CRITICAL=0, HIGH=1, NORMAL=2, LOW=3.
    """

    def __init__(
        self,
        max_size: int = 100,
        rate_limit: float | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._queue: asyncio.PriorityQueue[PrioritizedJob] = asyncio.PriorityQueue(
            maxsize=max_size
        )
        self.max_size = max_size
        self.rate_limit = rate_limit
        self.metrics = metrics
        self._last_dequeue_time: float | None = None

    async def enqueue(
        self,
        payload: dict[str, Any],
        meta: dict[str, Any],
        priority: int = 2,
        timeout: float | None = None,
    ) -> asyncio.Future[Any]:
        """Add job to queue.  Blocks until space is available (back-pressure).

        Args:
            payload: ComfyUI workflow payload.
            meta: Job metadata; optionally contains ``job_id``.
            priority: 0=CRITICAL, 1=HIGH, 2=NORMAL, 3=LOW.
            timeout: Seconds to wait for queue space before raising.

        Returns:
            Future that resolves when the job completes.

        Raises:
            QueueFullError: if *timeout* expires before space becomes available.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        item = PrioritizedJob(
            priority=priority,
            created_at=time.monotonic(),
            job_id=meta.get("job_id", f"job_{time.monotonic()}"),
            payload=payload,
            meta=meta,
            future=future,
        )
        try:
            await asyncio.wait_for(self._queue.put(item), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise QueueFullError(
                f"Queue full (max={self.max_size}); job rejected after {timeout}s"
            ) from exc

        if self.metrics:
            await self.metrics.gauge("queue_depth", float(self._queue.qsize()))
        return future

    async def dequeue(self) -> PrioritizedJob:
        """Return the highest-priority job, honouring rate limit if set."""
        if self.rate_limit and self._last_dequeue_time is not None:
            elapsed = time.monotonic() - self._last_dequeue_time
            min_interval = 1.0 / self.rate_limit
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)

        item = await self._queue.get()
        self._last_dequeue_time = time.monotonic()

        if self.metrics:
            await self.metrics.gauge("queue_depth", float(self._queue.qsize()))
            wait_time = time.monotonic() - item.created_at
            await self.metrics.observe("queue_wait_time", wait_time)

        return item

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()
