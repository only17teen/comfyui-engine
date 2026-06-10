"""Core benchmarks — run: pytest benchmarks/ --benchmark-only --benchmark-sort=mean"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from engine.command import Command, CommandBus, CommandHandler
from engine.cqrs import create_default_command_bus
from engine.query import Query, QueryBus, QueryHandler
from engine.pipeline import pipeline as mk_pipeline

@dataclass(frozen=True)
class BenchCmd(Command): value: int = 0
@dataclass(frozen=True)
class BenchQuery(Query): value: int = 0

class BCH(CommandHandler[BenchCmd, int]):
    async def handle(self, c: BenchCmd) -> int: return c.value+1
class BQH(QueryHandler[BenchQuery, int]):
    async def handle(self, q: BenchQuery) -> int: return q.value*2

def _run(coro): return asyncio.get_event_loop().run_until_complete(coro)

def test_command_bus_bare(benchmark) -> None:
    bus=CommandBus(); bus.register(BenchCmd,BCH()); benchmark(_run,bus.dispatch(BenchCmd(value=42)))
def test_command_bus_full_mw(benchmark) -> None:
    bus=create_default_command_bus(); bus.register(BenchCmd,BCH()); benchmark(_run,bus.dispatch(BenchCmd(value=42)))
def test_query_bus_bare(benchmark) -> None:
    bus=QueryBus(); bus.register(BenchQuery,BQH()); benchmark(_run,bus.ask(BenchQuery(value=10)))
def test_query_bus_warm_cache(benchmark) -> None:
    bus=QueryBus(); bus.register(BenchQuery,BQH()); _run(bus.ask_cached(BenchQuery(value=99),"k:99"))
    benchmark(_run,bus.ask_cached(BenchQuery(value=99),"k:99"))
async def _drain():
    async def src():
        for i in range(100): yield i
    return await mk_pipeline(src()).map(lambda x:x*2).filter(lambda x:x%4==0).drain()
def test_pipeline_100(benchmark) -> None: benchmark(_run,_drain())
