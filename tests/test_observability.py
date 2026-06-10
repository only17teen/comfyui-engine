from __future__ import annotations
import asyncio, pytest
from engine.observability import Span, observe

@pytest.mark.asyncio
async def test_no_raise_without_otel() -> None:
    async with observe("test.noop") as span: assert isinstance(span, Span)

@pytest.mark.asyncio
async def test_returns_result() -> None:
    async with observe("test"): result = 42
    assert result == 42

@pytest.mark.asyncio
async def test_name_matches() -> None:
    async with observe("my.op") as span: assert span.name == "my.op"

@pytest.mark.asyncio
async def test_set_attribute_no_raise() -> None:
    async with observe("test") as span:
        span.set_attribute("k","v"); span.set_attribute("score",0.9)

@pytest.mark.asyncio
async def test_record_metric_no_raise() -> None:
    async with observe("test") as span: span.record_metric("req",1.0)

@pytest.mark.asyncio
async def test_propagates_exception() -> None:
    with pytest.raises(ValueError):
        async with observe("test"): raise ValueError("boom")

@pytest.mark.asyncio
async def test_elapsed_positive() -> None:
    async with observe("test") as span:
        await asyncio.sleep(0.01); assert span.elapsed_ms > 0
