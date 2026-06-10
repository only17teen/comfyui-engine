from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, Generic, TypeVar

__all__ = ["BatchAccumulator"]
T = TypeVar("T")


class BatchAccumulator(Generic[T]):
    """Accumulates items and flushes on size or timeout triggers."""

    def __init__(self, max_size: int = 100, flush_timeout: float = 1.0) -> None:
        if max_size < 1:
            raise ValueError(f"max_size >= 1 required, got {max_size}")
        if flush_timeout <= 0:
            raise ValueError(f"flush_timeout > 0 required, got {flush_timeout}")
        self._max_size = max_size
        self._flush_timeout = flush_timeout
        self._buffer: list[T] = []
        self._lock: asyncio.Lock | None = None
        self._dirty: asyncio.Event | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _get_dirty(self) -> asyncio.Event:
        if self._dirty is None:
            self._dirty = asyncio.Event()
        return self._dirty

    def _take(self) -> list[T]:
        items, self._buffer = self._buffer, []
        self._get_dirty().clear()
        return items

    async def add(self, item: T) -> list[T] | None:
        async with self._get_lock():
            self._buffer.append(item)
            self._get_dirty().set()
            if len(self._buffer) >= self._max_size:
                return self._take()
        return None

    async def flush(self) -> list[T]:
        async with self._get_lock():
            return self._take()

    @property
    def pending(self) -> int:
        return len(self._buffer)

    async def run(
        self,
        source: AsyncIterator[T],
        on_batch: Callable[[list[T]], Coroutine[Any, Any, None]],
    ) -> None:
        async def _watcher() -> None:
            while True:
                await self._get_dirty().wait()
                await asyncio.sleep(self._flush_timeout)
                b = await self.flush()
                if b:
                    await on_batch(b)

        w = asyncio.ensure_future(_watcher())
        try:
            async for item in source:
                b = await self.add(item)
                if b is not None:
                    await on_batch(b)
            r = await self.flush()
            if r:
                await on_batch(r)
        finally:
            w.cancel()
            try:
                await w
            except asyncio.CancelledError:
                pass
