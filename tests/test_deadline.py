from __future__ import annotations
import asyncio, pytest
from engine.deadline import DEADLINE_VAR, DeadlineContext, check_deadline, get_deadline, remaining_time

@pytest.mark.asyncio
async def test_none_outside() -> None: assert get_deadline() is None and remaining_time() is None

@pytest.mark.asyncio
async def test_sets_var() -> None:
    async with DeadlineContext(5.0): assert get_deadline() is not None

@pytest.mark.asyncio
async def test_resets_after_exit() -> None:
    async with DeadlineContext(5.0): pass
    assert get_deadline() is None

@pytest.mark.asyncio
async def test_narrowing() -> None:
    async with DeadlineContext(10.0) as outer:
        outer_exp = get_deadline()
        async with DeadlineContext(30.0): assert get_deadline() == outer_exp
        async with DeadlineContext(1.0): assert get_deadline() < outer_exp

@pytest.mark.asyncio
async def test_remaining_positive() -> None:
    async with DeadlineContext(5.0) as dl:
        assert 0 < dl.remaining_positive() <= 5.0

@pytest.mark.asyncio
async def test_expired_after_sleep() -> None:
    async with DeadlineContext(0.01) as dl:
        assert not dl.expired(); await asyncio.sleep(0.02); assert dl.expired()

@pytest.mark.asyncio
async def test_check_deadline_raises() -> None:
    async with DeadlineContext(0.01):
        await asyncio.sleep(0.02)
        with pytest.raises(TimeoutError): check_deadline("test")

@pytest.mark.asyncio
async def test_invalid_timeout() -> None:
    with pytest.raises(ValueError): DeadlineContext(0.0)
