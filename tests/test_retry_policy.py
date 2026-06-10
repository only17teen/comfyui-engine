from __future__ import annotations
import asyncio
import pytest
from engine.retry_policy import RetryExhaustedError, RetryPolicy
from engine.deadline import DeadlineContext


@pytest.mark.asyncio
async def test_succeeds_first_try() -> None:
    async def fn() -> int:
        return 42

    assert await RetryPolicy(3).execute(fn) == 42


@pytest.mark.asyncio
async def test_succeeds_after_failures() -> None:
    count = 0

    async def flaky() -> str:
        nonlocal count
        count += 1
        if count < 3:
            raise ValueError("not yet")
        return "ok"

    assert await RetryPolicy(5, base_delay=0.001).execute(flaky) == "ok"
    assert count == 3


@pytest.mark.asyncio
async def test_exhausted() -> None:
    async def fail() -> None:
        raise RuntimeError("always")

    with pytest.raises(RetryExhaustedError) as ei:
        await RetryPolicy(3, base_delay=0.001).execute(fail)
    assert isinstance(ei.value.last_error, RuntimeError)


@pytest.mark.asyncio
async def test_decorator() -> None:
    count = 0

    @RetryPolicy(3, base_delay=0.001).retry
    async def fn() -> int:
        nonlocal count
        count += 1
        if count < 2:
            raise RuntimeError("once")
        return 99

    assert await fn() == 99 and count == 2


@pytest.mark.asyncio
async def test_deadline_aborts() -> None:
    count = 0

    async def fail() -> None:
        nonlocal count
        count += 1
        raise RuntimeError("fail")

    async with DeadlineContext(0.05):
        with pytest.raises(RetryExhaustedError):
            await RetryPolicy(10, base_delay=1.0, jitter=False).execute(fail)
    assert count < 10


def test_max_delay_respected() -> None:
    p = RetryPolicy(base_delay=1.0, max_delay=5.0, backoff_factor=10.0, jitter=False)
    assert p._compute_delay(100) == 5.0
