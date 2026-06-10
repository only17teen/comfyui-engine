from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, Generic, TypeVar
__all__ = ["PipelineStage","MapStage","FilterStage","BatchStage","Pipeline","pipeline"]
T, R = TypeVar("T"), TypeVar("R")

class PipelineStage(ABC, Generic[T, R]):
    @abstractmethod
    async def process(self, items: AsyncIterator[T]) -> AsyncIterator[R]: ...

class MapStage(PipelineStage[T, R]):
    def __init__(self, fn: Callable[[T], Any]) -> None: self._fn = fn
    async def process(self, items: AsyncIterator[T]) -> AsyncIterator[R]:  # type: ignore[override]
        async for item in items:
            r = self._fn(item)
            yield (await r) if asyncio.iscoroutine(r) else r

class FilterStage(PipelineStage[T, T]):
    def __init__(self, pred: Callable[[T], bool]) -> None: self._pred = pred
    async def process(self, items: AsyncIterator[T]) -> AsyncIterator[T]:  # type: ignore[override]
        async for item in items:
            if self._pred(item): yield item

class BatchStage(PipelineStage[T, list[T]]):
    def __init__(self, size: int) -> None:
        if size < 1: raise ValueError(f"size must be >= 1, got {size}")
        self._size = size
    async def process(self, items: AsyncIterator[T]) -> AsyncIterator[list[T]]:  # type: ignore[override]
        batch: list[T] = []
        async for item in items:
            batch.append(item)
            if len(batch) >= self._size: yield batch; batch = []
        if batch: yield batch

class Pipeline(Generic[T]):
    def __init__(self, source: AsyncIterator[T]) -> None: self._stream: AsyncIterator[Any] = source
    def pipe(self, stage: PipelineStage[Any, Any]) -> "Pipeline[Any]":
        self._stream = stage.process(self._stream); return self
    def map(self, fn: Callable[[Any], Any]) -> "Pipeline[Any]": return self.pipe(MapStage(fn))
    def filter(self, pred: Callable[[Any], bool]) -> "Pipeline[Any]": return self.pipe(FilterStage(pred))
    def batch(self, size: int) -> "Pipeline[Any]": return self.pipe(BatchStage(size))
    def __aiter__(self) -> AsyncIterator[Any]: return self._stream.__aiter__()
    async def drain(self) -> list[Any]: return [item async for item in self]
    async def drain_to(self, sink: Callable[[Any], Coroutine[Any, Any, None]]) -> int:
        count = 0
        async for item in self: await sink(item); count += 1
        return count

def pipeline(source: AsyncIterator[T]) -> Pipeline[T]: return Pipeline(source)
