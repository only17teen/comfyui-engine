from __future__ import annotations
import time
from contextvars import ContextVar
from typing import Any

__all__ = [
    "DeadlineContext",
    "DEADLINE_VAR",
    "get_deadline",
    "remaining_time",
    "check_deadline",
]
DEADLINE_VAR: ContextVar[float | None] = ContextVar("engine.deadline", default=None)


class DeadlineContext:
    """Propagates a deadline via ContextVar; narrows on re-entry."""

    def __init__(self, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")
        self._timeout = timeout
        self._expiry: float = 0.0
        self._token: Any = None

    async def __aenter__(self) -> DeadlineContext:
        """Propagates a deadline via ContextVar; narrows on re-entry."""
        now = time.monotonic()
        new_expiry = now + self._timeout
        existing = DEADLINE_VAR.get()
        self._expiry = min(new_expiry, existing) if existing is not None else new_expiry
        self._token = DEADLINE_VAR.set(self._expiry)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._token is not None:
            DEADLINE_VAR.reset(self._token)

    def expired(self) -> bool:
        return time.monotonic() >= self._expiry

    def remaining(self) -> float:
        return self._expiry - time.monotonic()

    def remaining_positive(self) -> float:
        return max(0.0, self.remaining())


def get_deadline() -> float | None:
    return DEADLINE_VAR.get()


def remaining_time() -> float | None:
    dl = DEADLINE_VAR.get()
    return None if dl is None else dl - time.monotonic()


def check_deadline(label: str = "") -> None:
    rem = remaining_time()
    if rem is not None and rem <= 0:
        raise TimeoutError(
            f"deadline exceeded{f' [{label}]' if label else ''} (overdue by {-rem:.3f}s)"
        )
