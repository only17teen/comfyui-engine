from __future__ import annotations
import asyncio, pytest
from dataclasses import dataclass
from engine.command import Command, CommandBus, CommandHandler, timing_middleware, validation_middleware

@dataclass(frozen=True)
class EchoCmd(Command):
    message: str = ""

@dataclass(frozen=True)
class ValidatedCmd(Command):
    value: int = 0
    def validate(self) -> None:
        if self.value < 0: raise ValueError(f"value must be >= 0, got {self.value}")

class EchoHandler(CommandHandler[EchoCmd, str]):
    async def handle(self, command: EchoCmd) -> str: return f"echo:{command.message}"

class ValidatedHandler(CommandHandler[ValidatedCmd, int]):
    async def handle(self, command: ValidatedCmd) -> int: return command.value * 2

@pytest.mark.asyncio
async def test_basic_dispatch() -> None:
    bus = CommandBus(); bus.register(EchoCmd, EchoHandler())
    assert await bus.dispatch(EchoCmd(message="hello")) == "echo:hello"

@pytest.mark.asyncio
async def test_no_handler_raises() -> None:
    bus = CommandBus()
    with pytest.raises(KeyError): await bus.dispatch(EchoCmd())

@pytest.mark.asyncio
async def test_middleware_order() -> None:
    order: list[str] = []
    async def mw_a(cmd, nxt):
        order.append("A:before"); r = await nxt(cmd); order.append("A:after"); return r
    async def mw_b(cmd, nxt):
        order.append("B:before"); r = await nxt(cmd); order.append("B:after"); return r
    bus = CommandBus(); bus.use(mw_a); bus.use(mw_b); bus.register(EchoCmd, EchoHandler())
    await bus.dispatch(EchoCmd(message="x"))
    assert order == ["A:before","B:before","B:after","A:after"]

@pytest.mark.asyncio
async def test_timing_middleware() -> None:
    bus = CommandBus(); bus.use(timing_middleware); bus.register(EchoCmd, EchoHandler())
    assert await bus.dispatch(EchoCmd(message="t")) == "echo:t"

@pytest.mark.asyncio
async def test_validation_passes() -> None:
    bus = CommandBus(); bus.use(validation_middleware); bus.register(ValidatedCmd, ValidatedHandler())
    assert await bus.dispatch(ValidatedCmd(value=5)) == 10

@pytest.mark.asyncio
async def test_validation_rejects() -> None:
    bus = CommandBus(); bus.use(validation_middleware); bus.register(ValidatedCmd, ValidatedHandler())
    with pytest.raises(ValueError): await bus.dispatch(ValidatedCmd(value=-1))

@pytest.mark.asyncio
async def test_concurrent_dispatch() -> None:
    bus = CommandBus(); bus.register(EchoCmd, EchoHandler())
    results = await asyncio.gather(*[bus.dispatch(EchoCmd(message=str(i))) for i in range(10)])
    assert results == [f"echo:{i}" for i in range(10)]
