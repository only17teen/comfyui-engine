from __future__ import annotations
import asyncio
import functools
import logging
import random
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar
from engine.deadline import remaining_time

__all__ = ["RetryPolicy", "RetryExhaustedError"]
log = logging.getLogger(__name__)
T = TypeVar("T")


class RetryExhaustedError(RuntimeError):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"all {attempts} attempt(s) failed; last error: {last_error!r}")
        self.last_error = last_error
        self.attempts = attempts


class RetryPolicy:
    """Full-jitter backoff retry policy with deadline awareness."""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 0.1,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        jitter: bool = True,
        retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts>=1 required, got {max_attempts}")
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._backoff_factor = backoff_factor
        self._jitter = jitter
        self._retryable = retryable_exceptions

    def _compute_delay(self, attempt: int) -> float:
        capped = min(self._base_delay * (self._backoff_factor**attempt), self._max_delay)
        return random.uniform(0.0, capped) if self._jitter else capped

    async def execute(self, fn: Callable[[], Coroutine[Any, Any, T]]) -> T:
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(self._max_attempts):
            try:
                return await fn()
            except self._retryable as exc:
                last_exc = exc
                if attempt == self._max_attempts - 1:
                    break
                delay = self._compute_delay(attempt)
                rem = remaining_time()
                if rem is not None and rem < delay:
                    log.warning(
                        "retry_policy.deadline_abort",
                        extra={"attempt": attempt, "remaining_s": round(rem, 4)},
                    )
                    break
                log.info(
                    "retry_policy.retrying",
                    extra={
                        "attempt": attempt + 1,
                        "of": self._max_attempts,
                        "delay_s": round(delay, 4),
                    },
                )
                await asyncio.sleep(delay)
        raise RetryExhaustedError(attempts=attempt + 1, last_error=last_exc)

    def retry(self, fn: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await self.execute(lambda: fn(*args, **kwargs))

        return wrapper  # type: ignore[return-value]

    @property
    def max_attempts(self) -> int:
        return self._max_attempts
