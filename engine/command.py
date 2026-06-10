from __future__ import annotations
import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar
from uuid import uuid4

__all__ = [
    "Command",
    "CommandHandler",
    "CommandBus",
    "logging_middleware",
    "timing_middleware",
    "validation_middleware",
]
C, R = TypeVar("C"), TypeVar("R")
Dispatch = Callable[[Any], Coroutine[Any, Any, Any]]
Middleware = Callable[[Any, Dispatch], Coroutine[Any, Any, Any]]


@dataclass(frozen=True)
class Command:
    """Base dataclass for all CQRS commands."""

    command_id: str = field(default_factory=lambda: str(uuid4()))


class CommandHandler(ABC, Generic[C, R]):
    """Base dataclass for all CQRS commands."""

    @abstractmethod
    async def handle(self, command: C) -> R: ...


class CommandBus:
    """Base dataclass for all CQRS commands."""

    def __init__(self) -> None:
        self._handlers: dict[type, CommandHandler[Any, Any]] = {}
        self._middleware: list[Middleware] = []

    def register(self, t: type, h: CommandHandler[Any, Any]) -> None:
        """Base dataclass for all CQRS commands."""
        self._handlers[t] = h

    def use(self, mw: Middleware) -> None:
        self._middleware.append(mw)

    async def dispatch(self, command: Any) -> Any:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise KeyError(f"No handler for {type(command).__name__}")

        async def base(cmd: Any) -> Any:
            return await handler.handle(cmd)

        pipeline: Dispatch = base
        for mw in reversed(self._middleware):
            _p: Dispatch = pipeline
            _m: Middleware = mw

            async def step(
                cmd: Any, *, _pp: Dispatch = _p, _mm: Middleware = _m
            ) -> Any:
                return await _mm(cmd, _pp)

            pipeline = step
        return await pipeline(command)


async def logging_middleware(command: Any, nxt: Dispatch) -> Any:
    try:
        import structlog

        log = structlog.get_logger().bind(command=type(command).__name__)
        log.info("command.dispatched")
        r = await nxt(command)
        log.info("command.succeeded")
        return r
    except ImportError:
        return await nxt(command)


async def timing_middleware(command: Any, nxt: Dispatch) -> Any:
    t = time.perf_counter()
    try:
        return await nxt(command)
    finally:
        try:
            from opentelemetry import metrics as m

            m.get_meter("comfyui_engine").create_histogram(
                "command.duration_ms", unit="ms"
            ).record(
                (time.perf_counter() - t) * 1000, {"command": type(command).__name__}
            )
        except Exception:
            pass


async def validation_middleware(command: Any, nxt: Dispatch) -> Any:
    v = getattr(command, "validate", None)
    if callable(v):
        r = v()
        if asyncio.iscoroutine(r):
            await r
    return await nxt(command)
