from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar
from uuid import uuid4

__all__ = ["Query", "QueryHandler", "QueryBus"]
Q, R = TypeVar("Q"), TypeVar("R")


@dataclass(frozen=True)
class Query:
    """Base dataclass for all CQRS queries."""

    query_id: str = field(default_factory=lambda: str(uuid4()))


class QueryHandler(ABC, Generic[Q, R]):
    """Base dataclass for all CQRS queries."""

    @abstractmethod
    async def handle(self, query: Q) -> R: ...


class QueryBus:
    """Base dataclass for all CQRS queries."""

    def __init__(self) -> None:
        self._handlers: dict[type, QueryHandler[Any, Any]] = {}
        self._cache: dict[str, Any] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def register(self, t: type, h: QueryHandler[Any, Any]) -> None:
        """Base dataclass for all CQRS queries."""
        self._handlers[t] = h

    async def ask(self, query: Any) -> Any:
        handler = self._handlers.get(type(query))
        if handler is None:
            raise KeyError(f"No handler for {type(query).__name__}")
        return await handler.handle(query)

    async def ask_cached(self, query: Any, cache_key: str) -> Any:
        if cache_key in self._cache:
            return self._cache[cache_key]
        async with self._get_lock():
            if cache_key in self._cache:
                return self._cache[cache_key]
            result = await self.ask(query)
            self._cache[cache_key] = result
            return result

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)
