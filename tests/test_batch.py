from __future__ import annotations
import asyncio
import pytest
from collections.abc import AsyncIterator
from engine.batch import BatchAccumulator


async def stream_of(items: list[int]) -> AsyncIterator[int]:
    for i in items:
        yield i


@pytest.mark.asyncio
async def test_add_below_max_returns_none() -> None:
    acc: BatchAccumulator[int] = BatchAccumulator(max_size=5)
    assert await acc.add(1) is None and acc.pending == 1


@pytest.mark.asyncio
async def test_add_at_max_returns_batch() -> None:
    acc: BatchAccumulator[int] = BatchAccumulator(max_size=3)
    await acc.add(1)
    await acc.add(2)
    assert await acc.add(3) == [1, 2, 3] and acc.pending == 0


@pytest.mark.asyncio
async def test_flush() -> None:
    acc: BatchAccumulator[int] = BatchAccumulator(max_size=100)
    for i in range(5):
        await acc.add(i)
    assert await acc.flush() == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_run_size_triggered() -> None:
    acc: BatchAccumulator[int] = BatchAccumulator(max_size=3, flush_timeout=10.0)
    batches: list[list[int]] = []

    async def on_batch(b: list[int]) -> None:
        batches.append(b)

    await acc.run(stream_of(list(range(9))), on_batch)
    assert batches == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]


@pytest.mark.asyncio
async def test_run_flushes_remainder() -> None:
    acc: BatchAccumulator[int] = BatchAccumulator(max_size=10, flush_timeout=5.0)
    batches: list[list[int]] = []

    async def on_batch(b: list[int]) -> None:
        batches.append(b)

    await acc.run(stream_of([1, 2, 3]), on_batch)
    assert batches == [[1, 2, 3]]
