from __future__ import annotations
import asyncio
import pytest
from collections.abc import AsyncIterator
from engine.pipeline import Pipeline, pipeline


async def ints(n: int) -> AsyncIterator[int]:
    for i in range(n):
        yield i


@pytest.mark.asyncio
async def test_map() -> None:
    assert await Pipeline(ints(5)).map(lambda x: x * 10).drain() == [0, 10, 20, 30, 40]


@pytest.mark.asyncio
async def test_filter() -> None:
    assert await Pipeline(ints(6)).filter(lambda x: x % 2 == 0).drain() == [0, 2, 4]


@pytest.mark.asyncio
async def test_batch_with_tail() -> None:
    assert await Pipeline(ints(4)).batch(3).drain() == [[0, 1, 2], [3]]


@pytest.mark.asyncio
async def test_chain() -> None:
    assert await Pipeline(ints(10)).map(lambda x: x * 2).filter(lambda x: x > 10).drain() == [12, 14, 16, 18]


@pytest.mark.asyncio
async def test_empty() -> None:
    async def empty() -> AsyncIterator[int]:
        return
        yield

    assert await pipeline(empty()).drain() == []


@pytest.mark.asyncio
async def test_async_map() -> None:
    async def double(x: int) -> int:
        await asyncio.sleep(0)
        return x * 2

    assert await Pipeline(ints(3)).map(double).drain() == [0, 2, 4]


@pytest.mark.asyncio
async def test_drain_to() -> None:
    calls: list[int] = []

    async def sink(x: int) -> None:
        calls.append(x)

    count = await Pipeline(ints(4)).drain_to(sink)
    assert count == 4 and calls == [0, 1, 2, 3]
