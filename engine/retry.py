"""ComfyUI Engine v5.1 - Retry Logic.

Extracted from core.py.

Key fixes vs original:
- `raise last_exc` now correctly chains to the ORIGINAL exception via
  `raise last_exc from original_exc` so the full traceback is preserved.
- DECORRELATED_JITTER keeps track of the actual previous delay instead of
  re-computing it from scratch each attempt.
- `retryable_exceptions` default is a proper tuple, not a mutable default.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import aiohttp

from engine.metrics import MetricsCollector

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry behaviour with exponential back-off."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    # FIX: use a proper default tuple via field() to avoid mutable-default issues
    retryable_exceptions: tuple[type[Exception], ...] = field(
        default=(aiohttp.ClientError, asyncio.TimeoutError, OSError)
    )
    strategy: str = "FULL_JITTER"
    jitter_factor: float = 0.2
    retryable_statuses: frozenset[int] = field(
        default=frozenset({408, 429, 500, 502, 503, 504})
    )
    non_retryable_statuses: frozenset[int] = field(
        default=frozenset({400, 401, 403, 404, 405, 422})
    )
    status_based_retry: bool = True


def _compute_delay(
    strategy: str,
    attempt: int,
    base: float,
    exp: float,
    max_d: float,
    prev_delay: float,
) -> float:
    """Compute the delay for a given retry attempt."""
    if strategy == "FIXED":
        raw = base
    elif strategy == "LINEAR":
        raw = base * (attempt + 1)
    elif strategy == "EXPONENTIAL":
        raw = base * (exp**attempt)
    elif strategy == "FULL_JITTER":
        cap = min(max_d, base * (exp**attempt))
        return random.uniform(0.0, cap)
    elif strategy == "DECORRELATED_JITTER":
        # Uses previous delay for better spread across concurrent retriers
        raw = random.uniform(base, min(max_d, prev_delay * 3))
        return min(max_d, raw)
    else:
        raw = base * (exp**attempt)
    return min(max_d, raw)


async def with_retry(
    coro: Callable[..., Any],
    config: RetryConfig,
    metrics: MetricsCollector,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute a coroutine with configurable retry strategies.

    Args:
        coro: Async callable to execute.
        config: Retry configuration.
        metrics: Metrics collector for retry tracking.
        *args: Positional arguments forwarded to *coro*.
        **kwargs: Keyword arguments forwarded to *coro*.

    Returns:
        Result of *coro*.

    Raises:
        The last exception encountered, chained to the original for full traceback.
    """
    first_exc: Exception | None = None
    last_exc: Exception | None = None
    prev_delay: float = config.base_delay

    for attempt in range(config.max_retries + 1):
        try:
            return await coro(*args, **kwargs)
        except config.retryable_exceptions as exc:  # type: ignore[misc]
            if first_exc is None:
                first_exc = exc
            last_exc = exc

            if attempt == config.max_retries:
                break

            # Status-code based non-retry check
            if config.status_based_retry and hasattr(exc, "status"):
                status: int = exc.status  # type: ignore[attr-defined]
                if status in config.non_retryable_statuses:
                    break
                if (
                    config.retryable_statuses
                    and status not in config.retryable_statuses
                ):
                    break

            delay = _compute_delay(
                config.strategy,
                attempt,
                config.base_delay,
                config.exponential_base,
                config.max_delay,
                prev_delay,
            )
            prev_delay = delay
            delay = max(0.0, delay)

            await metrics.inc("retries_total")
            logger.warning(
                "Retry %d/%d via %s in %.2fs: %s",
                attempt + 1,
                config.max_retries,
                config.strategy,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    # FIX: chain to first_exc so the full traceback chain is available
    raise last_exc from first_exc  # type: ignore[misc]
