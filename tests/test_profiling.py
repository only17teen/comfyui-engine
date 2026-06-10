from __future__ import annotations
import pytest
from engine.profiling import AsyncProfiler, profile_async

@pytest.mark.asyncio
async def test_no_raise_without_yappi() -> None:
    async with AsyncProfiler() as prof: x = 1+1
    assert x == 2

@pytest.mark.asyncio
async def test_print_stats_no_raise() -> None:
    async with AsyncProfiler() as prof: pass
    prof.print_stats(n=5)

@pytest.mark.asyncio
async def test_profile_async_returns_result() -> None:
    async def fn() -> int: return 42
    assert await profile_async(fn()) == 42

@pytest.mark.asyncio
async def test_is_available_bool() -> None:
    async with AsyncProfiler() as prof: pass
    assert isinstance(prof.is_available, bool)
