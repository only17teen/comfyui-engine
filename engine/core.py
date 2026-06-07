"""ComfyUI Async Generation Engine v2.0 - Core Infrastructure
Fundamental improvements: structured logging, metrics, circuit breaker, retry logic.
Optimized for Arch Linux / Python 3.11+
"""

import asyncio
import functools
import json
import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar, Union

import aiohttp


# ───────────────────────────────────────────────────────────────
# Structured Logging (JSON format for log aggregation)
# ───────────────────────────────────────────────────────────────
class JSONFormatter(logging.Formatter):
    """Emit log records as JSON lines for parsing by jq/vector/loki."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if hasattr(record, "extra"):
            log_obj.update(record.extra)
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False, default=str)


def setup_logging(
    level: int = logging.INFO,
    log_dir: str = "logs",
    json_format: bool = True,
) -> None:
    """Configure dual logging: human-readable to terminal, JSON to file."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_dir) / f"engine_{ts}.log"

    # Terminal handler: plain text
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))

    # File handler: JSON for log aggregation
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [console_handler, file_handler]

    # Reduce aiohttp noise
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


# ───────────────────────────────────────────────────────────────
# Metrics Collection (Prometheus-style counters/gauges/histograms)
# ───────────────────────────────────────────────────────────────
@dataclass
class MetricsSnapshot:
    """Point-in-time metrics snapshot."""

    timestamp: float
    jobs_submitted: int = 0
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_timeout: int = 0
    total_wait_time: float = 0.0
    total_processing_time: float = 0.0
    download_bytes: int = 0
    download_errors: int = 0
    api_errors: int = 0
    retries_total: int = 0
    circuit_breaker_trips: int = 0
    queue_depth: int = 0
    active_workers: int = 0


class MetricsCollector:
    """Thread-safe (asyncio-safe) metrics collector.
    Tracks counters, histograms, and gauges for observability.
    """

    def __init__(self, window_size: int = 1000):
        self._lock = asyncio.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, deque] = {}
        self._window_size = window_size
        self._start_time = time.time()

    async def inc(self, metric: str, value: int = 1) -> None:
        async with self._lock:
            self._counters[metric] = self._counters.get(metric, 0) + value

    async def dec(self, metric: str, value: int = 1) -> None:
        async with self._lock:
            self._counters[metric] = self._counters.get(metric, 0) - value

    async def gauge(self, metric: str, value: float) -> None:
        async with self._lock:
            self._gauges[metric] = value

    async def observe(self, metric: str, value: float) -> None:
        async with self._lock:
            if metric not in self._histograms:
                self._histograms[metric] = deque(maxlen=self._window_size)
            self._histograms[metric].append(value)

    async def snapshot(self) -> MetricsSnapshot:
        async with self._lock:
            return MetricsSnapshot(
                timestamp=time.time(),
                jobs_submitted=self._counters.get("jobs_submitted", 0),
                jobs_completed=self._counters.get("jobs_completed", 0),
                jobs_failed=self._counters.get("jobs_failed", 0),
                jobs_timeout=self._counters.get("jobs_timeout", 0),
                total_wait_time=self._counters.get("total_wait_time", 0),
                total_processing_time=self._counters.get("total_processing_time", 0),
                download_bytes=self._counters.get("download_bytes", 0),
                download_errors=self._counters.get("download_errors", 0),
                api_errors=self._counters.get("api_errors", 0),
                retries_total=self._counters.get("retries_total", 0),
                circuit_breaker_trips=self._counters.get("circuit_breaker_trips", 0),
                queue_depth=self._gauges.get("queue_depth", 0),
                active_workers=self._gauges.get("active_workers", 0),
            )

    async def report(self) -> dict[str, Any]:
        """Generate a full metrics report with histogram percentiles."""
        async with self._lock:
            report = {
                "uptime_seconds": time.time() - self._start_time,
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {},
            }
            for name, values in self._histograms.items():
                if not values:
                    continue
                sorted_vals = sorted(values)
                n = len(sorted_vals)
                report["histograms"][name] = {
                    "count": n,
                    "min": sorted_vals[0],
                    "max": sorted_vals[-1],
                    "mean": sum(sorted_vals) / n,
                    "p50": sorted_vals[n // 2],
                    "p95": sorted_vals[int(n * 0.95)] if n >= 20 else sorted_vals[-1],
                    "p99": sorted_vals[int(n * 0.99)] if n >= 100 else sorted_vals[-1],
                }
            return report


# ───────────────────────────────────────────────────────────────
# Circuit Breaker Pattern
# ───────────────────────────────────────────────────────────────
class CircuitState(Enum):
    """Circuit breaker state enumeration."""

    CLOSED = auto()  # Normal operation
    OPEN = auto()  # Failing, reject requests
    HALF_OPEN = auto()  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3
    success_threshold: int = 2


class CircuitBreaker:
    """Circuit breaker for ComfyUI API resilience.
    Prevents cascading failures when GPU server is overloaded or down.
    """

    def __init__(self, name: str, config: CircuitBreakerConfig, metrics: MetricsCollector):
        self.name = name
        self.config = config
        self.metrics = metrics
        self.state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

    async def call(self, coro_factory: Callable[[], Any], *args, **kwargs) -> Any:
        """Execute coroutine with circuit breaker protection."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._successes = 0
                    logging.getLogger("circuit_breaker").info(f"[{self.name}] Transition OPEN -> HALF_OPEN")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit {self.name} is OPEN. Last failure: " f"{self._last_failure_time}"
                    )

            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(f"Circuit {self.name} HALF_OPEN quota exhausted")
                self._half_open_calls += 1

        try:
            result = await coro_factory(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        if self._last_failure_time is None:
            return True
        return time.time() - self._last_failure_time >= self.config.recovery_timeout

    async def _on_success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.config.success_threshold:
                    self.state = CircuitState.CLOSED
                    self._failures = 0
                    logging.getLogger("circuit_breaker").info(f"[{self.name}] Transition HALF_OPEN -> CLOSED")
            else:
                self._failures = max(0, self._failures - 1)

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()

            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                await self.metrics.inc("circuit_breaker_trips")
                logging.getLogger("circuit_breaker").warning(f"[{self.name}] Transition HALF_OPEN -> OPEN (failure)")
            elif self._failures >= self.config.failure_threshold:
                if self.state != CircuitState.OPEN:
                    self.state = CircuitState.OPEN
                    await self.metrics.inc("circuit_breaker_trips")
                    logging.getLogger("circuit_breaker").warning(
                        f"[{self.name}] Transition CLOSED -> OPEN ({self._failures} failures)"
                    )

    def get_state(self) -> CircuitState:
        return self.state


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""

    pass


# ───────────────────────────────────────────────────────────────
# Retry Decorator with Exponential Backoff
# ───────────────────────────────────────────────────────────────
T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for retry behavior with exponential backoff."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    retryable_exceptions: tuple = (aiohttp.ClientError, asyncio.TimeoutError, OSError)


async def with_retry(
    coro: Callable[..., Any],
    config: RetryConfig,
    metrics: MetricsCollector,
    *args,
    **kwargs,
) -> Any:
    """Execute coroutine with exponential backoff retry.

    Args:
        coro: Async callable to execute.
        config: Retry configuration.
        metrics: Metrics collector for retry tracking.
        *args: Positional arguments for coro.
        **kwargs: Keyword arguments for coro.

    Returns:
        Result of coro execution.

    Raises:
        Last exception after all retries exhausted.
    """
    last_exception: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            return await coro(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e
            if attempt == config.max_retries:
                break

            delay = min(
                config.base_delay * (config.exponential_base**attempt),
                config.max_delay,
            )
            jitter = delay * 0.1 * (2 * (time.time() % 1) - 1)  # ±10% jitter
            actual_delay = delay + jitter

            await metrics.inc("retries_total")
            logging.getLogger("retry").warning(
                f"Retry {attempt + 1}/{config.max_retries} after {actual_delay:.1f}s: {e}"
            )
            await asyncio.sleep(actual_delay)

    raise last_exception


# ───────────────────────────────────────────────────────────────
# Priority Queue with Backpressure
# ───────────────────────────────────────────────────────────────
@dataclass(order=True)
class PrioritizedJob:
    """Queue item with priority (lower = higher priority)."""

    priority: int
    created_at: float = field(compare=True)
    job_id: str = field(compare=False)
    payload: dict = field(compare=False)
    meta: dict = field(compare=False)
    future: asyncio.Future = field(compare=False)


class JobQueue:
    """Async priority queue with backpressure and rate limiting.
    Supports priority levels: CRITICAL(0), HIGH(1), NORMAL(2), LOW(3).
    """

    def __init__(
        self,
        max_size: int = 100,
        rate_limit: float | None = None,  # jobs per second
        metrics: MetricsCollector | None = None,
    ):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_size)
        self.max_size = max_size
        self.rate_limit = rate_limit
        self.metrics = metrics
        self._last_dequeue_time: float | None = None
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        payload: dict,
        meta: dict,
        priority: int = 2,
        timeout: float | None = None,
    ) -> asyncio.Future:
        """Add job to queue. Blocks if queue is full (backpressure).

        Args:
            payload: ComfyUI workflow payload.
            meta: Job metadata.
            priority: 0=CRITICAL, 1=HIGH, 2=NORMAL, 3=LOW.
            timeout: Max seconds to wait for queue space.

        Returns:
            Future that resolves to ComfyUIJob when complete.
        """
        future = asyncio.get_event_loop().create_future()
        item = PrioritizedJob(
            priority=priority,
            created_at=time.time(),
            job_id=meta.get("job_id", f"job_{time.time()}"),
            payload=payload,
            meta=meta,
            future=future,
        )

        try:
            await asyncio.wait_for(self._queue.put(item), timeout=timeout)
            if self.metrics:
                await self.metrics.gauge("queue_depth", self._queue.qsize())
            return future
        except asyncio.TimeoutError:
            raise QueueFullError(f"Queue full (max={self.max_size}), job rejected")

    async def dequeue(self) -> PrioritizedJob:
        """Get next job, respecting rate limit."""
        if self.rate_limit and self._last_dequeue_time:
            elapsed = time.time() - self._last_dequeue_time
            min_interval = 1.0 / self.rate_limit
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)

        item = await self._queue.get()
        self._last_dequeue_time = time.time()

        if self.metrics:
            await self.metrics.gauge("queue_depth", self._queue.qsize())
            wait_time = time.time() - item.created_at
            await self.metrics.observe("queue_wait_time", wait_time)
            await self.metrics.inc("total_wait_time", int(wait_time))

        return item

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()


class QueueFullError(Exception):
    """Raised when job queue is at capacity."""

    pass


# ───────────────────────────────────────────────────────────────
# Session State Manager
# ───────────────────────────────────────────────────────────────
@dataclass
class SessionState:
    """Persistent session state for resumable operations."""

    session_id: str
    started_at: float
    completed_jobs: list[str] = field(default_factory=list)
    failed_jobs: list[str] = field(default_factory=list)
    pending_jobs: list[str] = field(default_factory=list)
    total_images: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SessionState":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)
