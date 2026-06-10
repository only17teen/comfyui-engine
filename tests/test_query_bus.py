from __future__ import annotations
import asyncio
import pytest
from dataclasses import dataclass
from engine.query import Query, QueryBus, QueryHandler

call_count = 0


@dataclass(frozen=True)
class GetValue(Query):
    key: str = ""


class GetValueHandler(QueryHandler[GetValue, str]):
    async def handle(self, query: GetValue) -> str:
        global call_count
        call_count += 1
        return f"value:{query.key}"


@pytest.fixture(autouse=True)
def reset() -> None:
    global call_count
    call_count = 0


@pytest.mark.asyncio
async def test_basic_ask() -> None:
    bus = QueryBus()
    bus.register(GetValue, GetValueHandler())
    assert await bus.ask(GetValue(key="foo")) == "value:foo"


@pytest.mark.asyncio
async def test_no_handler_raises() -> None:
    with pytest.raises(KeyError):
        await QueryBus().ask(GetValue())


@pytest.mark.asyncio
async def test_ask_cached_single_flight() -> None:
    bus = QueryBus()
    bus.register(GetValue, GetValueHandler())
    results = await asyncio.gather(
        bus.ask_cached(GetValue(key="x"), "k:x"),
        bus.ask_cached(GetValue(key="x"), "k:x"),
        bus.ask_cached(GetValue(key="x"), "k:x"),
    )
    assert all(r == "value:x" for r in results)
    assert call_count == 1


@pytest.mark.asyncio
async def test_invalidate() -> None:
    bus = QueryBus()
    bus.register(GetValue, GetValueHandler())
    await bus.ask_cached(GetValue(key="y"), "k:y")
    bus.invalidate("k:y")
    await bus.ask_cached(GetValue(key="y"), "k:y")
    assert call_count == 2


@pytest.mark.asyncio
async def test_clear_cache() -> None:
    bus = QueryBus()
    bus.register(GetValue, GetValueHandler())
    await bus.ask_cached(GetValue(key="a"), "a")
    await bus.ask_cached(GetValue(key="b"), "b")
    assert bus.cache_size() == 2
    bus.clear_cache()
    assert bus.cache_size() == 0
