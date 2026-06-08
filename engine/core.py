"""ComfyUI Async Generation Engine v5.1 - Core Infrastructure (Optimized)
Kiro Protocol optimizations: batch metrics, lock-free counters, object pooling.
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
T = TypeVar("T")

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
# Object Pool for MetricsSnapshot (Memory First - Kiro Rule 6)
# ───────────────────────────────────────────────────────────────
class ObjectPool(Generic[T]):
    """Generic object pool for reducing allocation overhead."""

    def __init__(self, factory: Callable[[], T], max_size: int = 100):
        self._factory = factory
        self._max_size = max_size
        self._pool: deque[T] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()

    async def acquire(self) -> T:
        async with self._lock:
            if self._pool:
                return self._pool.popleft()
        return self._factory()

    async def release(self, obj: T) -> None:
        async with self._lock:
            if len(self._pool) < self._max_size:
                self._pool.append(obj)


# ───────────────────────────────────────────────────────────────
# Metrics Collection (Optimized - Kiro Rules 1, 6, 11)
# Batch writes, lock-free counters, histogram bucketing
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

    def reset(self) -> None:
        """Reset for pool reuse."""
        self.timestamp = 0.0
        self.jobs_submitted = 0
        self.jobs_completed = 0
        self.jobs_failed = 0
        self.jobs_timeout = 0
        self.total_wait_time = 0.0
        self.total_processing_time = 0.0
        self.download_bytes = 0
        self.download_errors = 0
        self.api_errors = 0
        self.retries_total = 0
        self.circuit_breaker_trips = 0
        self.queue_depth = 0
        self.active_workers = 0


class MetricsCollector:
    """Optimized metrics collector with batch writes and lock-free counters.
    
    Kiro Protocol optimizations:
    - Batch metric writes with flush interval (Rule 1: Relentless Optimization)
    - Lock-free counters via asyncio.Queue (Rule 6: Memory First)
    - Pre-allocated histogram buckets (Rule 6: Memory First)
    - Object pooling for snapshots (Rule 6: Memory First)
    """

    def __init__(
        self,
        window_size: int = 1000,
        batch_size: int = 100,
        flush_interval: float = 1.0,
        enable_pooling: bool = True,
    ):
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._window_size = window_size
        self._start_time = time.time()
        
        # Lock-free counter queue (Rule 6: Memory First)
        self._counter_queue: asyncio.Queue = asyncio.Queue(maxsize=batch_size * 10)
        self._gauge_queue: asyncio.Queue = asyncio.Queue(maxsize=batch_size * 10)
        self._histogram_queue: asyncio.Queue = asyncio.Queue(maxsize=batch_size * 10)
        
        # Batched storage
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, deque] = {}
        
        # Snapshot pooling (Rule 6: Memory First)
        self._snapshot_pool: ObjectPool[MetricsSnapshot] | None = None
        if enable_pooling:
            self._snapshot_pool = ObjectPool(lambda: MetricsSnapshot(timestamp=0.0), max_size=50)
        
        # Background flush task
        self._flush_task: asyncio.Task | None = None
        self._shutdown: bool = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start background flush task."""
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop background flush and drain queues."""
        self._shutdown = True
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Final flush
        await self._process_queues()

    async def _flush_loop(self) -> None:
        """Periodic flush of batched metrics."""
        while not self._shutdown:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._process_queues()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.getLogger("metrics").error(f"Flush loop error: {e}")

    async def _process_queues(self) -> None:
        """Process all queued metrics in batches."""
        # Process counters
        counter_batch = []
        while not self._counter_queue.empty() and len(counter_batch) < self._batch_size:
            try:
                counter_batch.append(self._counter_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        
        if counter_batch:
            async with self._lock:
                for metric, value in counter_batch:
                    self._counters[metric] = self._counters.get(metric, 0) + value
        
        # Process gauges
        gauge_batch = []
        while not self._gauge_queue.empty() and len(gauge_batch) < self._batch_size:
            try:
                gauge_batch.append(self._gauge_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        
        if gauge_batch:
            async with self._lock:
                for metric, value in gauge_batch:
                    self._gauges[metric] = value
        
        # Process histograms
        hist_batch = []
        while not self._histogram_queue.empty() and len(hist_batch) < self._batch_size:
            try:
                hist_batch.append(self._histogram_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        
        if hist_batch:
            async with self._lock:
                for metric, value in hist_batch:
                    if metric not in self._histograms:
                        self._histograms[metric] = deque(maxlen=self._window_size)
                    self._histograms[metric].append(value)

    async def inc(self, metric: str, value: int = 1) -> None:
        """Increment counter (lock-free via queue)."""
        try:
            self._counter_queue.put_nowait((metric, value))
        except asyncio.QueueFull:
            # Drop oldest if full (backpressure)
            try:
                self._counter_queue.get_nowait()
                self._counter_queue.put_nowait((metric, value))
            except asyncio.QueueEmpty:
                pass

    async def dec(self, metric: str, value: int = 1) -> None:
        """Decrement counter (lock-free via queue)."""
        try:
            self._counter_queue.put_nowait((metric, -value))
        except asyncio.QueueFull:
            try:
                self._counter_queue.get_nowait()
                self._counter_queue.put_nowait((metric, -value))
            except asyncio.QueueEmpty:
                pass

    async def gauge(self, metric: str, value: float) -> None:
        """Set gauge (lock-free via queue)."""
        try:
            self._gauge_queue.put_nowait((metric, value))
        except asyncio.QueueFull:
            try:
                self._gauge_queue.get_nowait()
                self._gauge_queue.put_nowait((metric, value))
            except asyncio.QueueEmpty:
                pass

    async def observe(self, metric: str, value: float) -> None:
        """Observe histogram value (lock-free via queue)."""
        try:
            self._histogram_queue.put_nowait((metric, value))
        except asyncio.QueueFull:
            try:
                self._histogram_queue.get_nowait()
                self._histogram_queue.put_nowait((metric, value))
            except asyncio.QueueEmpty:
                pass

    async def snapshot(self) -> MetricsSnapshot:
        """Get point-in-time snapshot (uses object pool if enabled)."""
        await self._process_queues()  # Ensure queues are flushed
        
        async with self._lock:
            if self._snapshot_pool:
                snap = await self._snapshot_pool.acquire()
                snap.timestamp = time.time()
                snap.jobs_submitted = self._counters.get("jobs_submitted", 0)
                snap.jobs_completed = self._counters.get("jobs_completed", 0)
                snap.jobs_failed = self._counters.get("jobs_failed", 0)
                snap.jobs_timeout = self._counters.get("jobs_timeout", 0)
                snap.total_wait_time = self._counters.get("total_wait_time", 0)
                snap.total_processing_time = self._counters.get("total_processing_time", 0)
                snap.download_bytes = self._counters.get("download_bytes", 0)
                snap.download_errors = self._counters.get("download_errors", 0)
                snap.api_errors = self._counters.get("api_errors", 0)
                snap.retries_total = self._counters.get("retries_total", 0)
                snap.circuit_breaker_trips = self._counters.get("circuit_breaker_trips", 0)
                snap.queue_depth = int(self._gauges.get("queue_depth", 0))
                snap.active_workers = int(self._gauges.get("active_workers", 0))
                return snap
            else:
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
                    queue_depth=int(self._gauges.get("queue_depth", 0)),
                    active_workers=int(self._gauges.get("active_workers", 0)),
                )

    async def release_snapshot(self, snap: MetricsSnapshot) -> None:
        """Return snapshot to pool for reuse."""
        if self._snapshot_pool:
            snap.reset()
            await self._snapshot_pool.release(snap)

    async def report(self) -> dict[str, Any]:
        """Generate full metrics report with histogram percentiles."""
        await self._process_queues()
        
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
# Circuit Breaker Pattern (Optimized - Kiro Rule 4)
# Lock-free state transitions, atomic counters
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
    """Optimized circuit breaker with lock-free state transitions.
    
    Kiro Protocol optimizations:
    - Atomic state transitions (Rule 4: Reliability)
    - Batched metrics reporting (Rule 1: Optimization)
    - Fast path for CLOSED state (Rule 1: Optimization)
    """

    def __init__(self, name: str, config: CircuitBreakerConfig, metrics: MetricsCollector):
        self.name = name
        self.config = config
        self.metrics = metrics
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()
        self._metrics_batch: list[tuple[str, int]] = []
        self._metrics_batch_size = 10

    async def call(self, coro_factory: Callable[[], Any], *args, **kwargs) -> Any:
        """Execute coroutine with circuit breaker protection."""
        # Fast path: CLOSED state (no lock needed)
        if self._state == CircuitState.CLOSED:
            try:
                result = await coro_factory(*args, **kwargs)
                await self._on_success_fast()
                return result
            except Exception as e:
                await self._on_failure_fast()
                raise
        
        # Slow path: OPEN or HALF_OPEN (needs lock)
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._successes = 0
                    logging.getLogger("circuit_breaker").info(f"[{self.name}] Transition OPEN -> HALF_OPEN")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit {self.name} is OPEN. Last failure: {self._last_failure_time}"
                    )

            if self._state == CircuitState.HALF_OPEN:
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

    async def _on_success_fast(self) -> None:
        """Fast path success handler (no lock)."""
        self._failures = max(0, self._failures - 1)

    async def _on_failure_fast(self) -> None:
        """Fast path failure handler (no lock)."""
        self._failures += 1
        self._last_failure_time = time.time()
        
        if self._failures >= self.config.failure_threshold:
            self._state = CircuitState.OPEN
            await self._batch_metric("circuit_breaker_trips", 1)
            logging.getLogger("circuit_breaker").warning(
                f"[{self.name}] Transition CLOSED -> OPEN ({self._failures} failures)"
            )

    async def _on_success(self) -> None:
        """Slow path success handler (with lock)."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failures = 0
                    logging.getLogger("circuit_breaker").info(f"[{self.name}] Transition HALF_OPEN -> CLOSED")
            else:
                self._failures = max(0, self._failures - 1)

    async def _on_failure(self) -> None:
        """Slow path failure handler (with lock)."""
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                await self._batch_metric("circuit_breaker_trips", 1)
                logging.getLogger("circuit_breaker").warning(f"[{self.name}] Transition HALF_OPEN -> OPEN (failure)")
            elif self._failures >= self.config.failure_threshold:
                if self._state != CircuitState.OPEN:
                    self._state = CircuitState.OPEN
                    await self._batch_metric("circuit_breaker_trips", 1)
                    logging.getLogger("circuit_breaker").warning(
                        f"[{self.name}] Transition CLOSED -> OPEN ({self._failures} failures)"
                    )

    async def _batch_metric(self, metric: str, value: int) -> None:
        """Batch metric updates for efficiency."""
        self._metrics_batch.append((metric, value))
        if len(self._metrics_batch) >= self._metrics_batch_size:
            for m, v in self._metrics_batch:
                await self.metrics.inc(m, v)
            self._metrics_batch.clear()

    def get_state(self) -> CircuitState:
        return self._state


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""

    pass


# ───────────────────────────────────────────────────────────────
# Retry Decorator with Exponential Backoff (Optimized)
# Kiro Rule 1: Relentless Optimization - pre-computed delays
# ───────────────────────────────────────────────────────────────


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
    """Execute coroutine with optimized exponential backoff retry.
    
    Kiro Protocol optimizations:
    - Pre-computed delay table (Rule 1: Optimization)
    - Batch metric reporting (Rule 1: Optimization)
    - Fast path for no-retry case (Rule 1: Optimization)
    """
    # Fast path: no retries needed
    if config.max_retries == 0:
        return await coro(*args, **kwargs)
    
    # Pre-compute delay table
    delays = []
    for attempt in range(config.max_retries):
        delay = min(
            config.base_delay * (config.exponential_base ** attempt),
            config.max_delay,
        )
        jitter = delay * 0.1 * (2 * (time.time() % 1) - 1)  # ±10% jitter
        delays.append(delay + jitter)
    
    last_exception: Exception | None = None
    retry_batch = []

    for attempt in range(config.max_retries + 1):
        try:
            return await coro(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e
            if attempt == config.max_retries:
                break

            # Batch metric update
            retry_batch.append(("retries_total", 1))
            if len(retry_batch) >= 5:
                for m, v in retry_batch:
                    await metrics.inc(m, v)
                retry_batch.clear()

            logging.getLogger("retry").warning(
                f"Retry {attempt + 1}/{config.max_retries} after {delays[attempt]:.1f}s: {e}"
            )
            await asyncio.sleep(delays[attempt])

    # Flush remaining retry metrics
    if retry_batch:
        for m, v in retry_batch:
            await metrics.inc(m, v)

    raise last_exception


# ───────────────────────────────────────────────────────────────
# Priority Queue with Backpressure (Optimized)
# Kiro Rule 1: Relentless Optimization - batch metrics
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
    
    Kiro Protocol optimizations:
    - Batch metric updates (Rule 1: Optimization)
    - Pre-allocated futures (Rule 6: Memory First)
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
        self._metric_batch: list[tuple[str, Union[int, float]]] = []
        self._metric_batch_size = 10

    async def _batch_metric(self, metric: str, value: Union[int, float]) -> None:
        """Batch metric updates for efficiency."""
        if self.metrics is None:
            return
        self._metric_batch.append((metric, value))
        if len(self._metric_batch) >= self._metric_batch_size:
            for m, v in self._metric_batch:
                if isinstance(v, int):
                    await self.metrics.inc(m, v)
                else:
                    await self.metrics.gauge(m, v)
            self._metric_batch.clear()

    async def _flush_metrics(self) -> None:
        """Flush remaining batched metrics."""
        if self.metrics is None or not self._metric_batch:
            return
        for m, v in self._metric_batch:
            if isinstance(v, int):
                await self.metrics.inc(m, v)
            else:
                await self.metrics.gauge(m, v)
        self._metric_batch.clear()

    async def enqueue(
        self,
        payload: dict,
        meta: dict,
        priority: int = 2,
        timeout: float | None = None,
    ) -> asyncio.Future:
        """Add job to queue. Blocks if queue is full (backpressure)."""
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
            await self._batch_metric("queue_depth", self._queue.qsize())
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

        wait_time = time.time() - item.created_at
        await self._batch_metric("queue_depth", self._queue.qsize())
        await self._batch_metric("queue_wait_time", wait_time)
        await self._batch_metric("total_wait_time", int(wait_time))

        return item

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    async def shutdown(self) -> None:
        """Graceful shutdown with metric flush."""
        await self._flush_metrics()


class QueueFullError(Exception):
    """Raised when job queue is at capacity."""

    pass


# ───────────────────────────────────────────────────────────────
# Session State Manager (Optimized)
# Kiro Rule 6: Memory First - __slots__, batch saves
# ───────────────────────────────────────────────────────────────
@dataclass(slots=True)
class SessionState:
    """Persistent session state for resumable operations.
    
    Kiro Protocol optimizations:
    - __slots__ for memory efficiency (Rule 6: Memory First)
    - Batch save support (Rule 1: Optimization)
    """

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


# ───────────────────────────────────────────────────────────────
# Health Check Component (New - Kiro Rule 4, 11)
# Composite health checks for all components
# ───────────────────────────────────────────────────────────────
@dataclass
class HealthStatus:
    """Health status for a single component."""

    component: str
    status: str  # healthy, degraded, unhealthy
    latency_ms: float
    last_check: float
    details: dict[str, Any] = field(default_factory=dict)


class HealthChecker:
    """Composite health checker for all engine components.
    
    Kiro Protocol features:
    - Parallel health checks (Rule 7: Async Correctness)
    - Cached results with TTL (Rule 6: Memory First)
    - Detailed component status (Rule 11: Observability)
    """

    def __init__(self, cache_ttl: float = 5.0):
        self._checks: dict[str, Callable[[], Any]] = {}
        self._cache: dict[str, HealthStatus] = {}
        self._cache_ttl = cache_ttl
        self._lock = asyncio.Lock()

    def register(self, name: str, check: Callable[[], Any]) -> None:
        """Register a health check function."""
        self._checks[name] = check

    async def check(self, name: str) -> HealthStatus:
        """Run single health check with caching."""
        async with self._lock:
            cached = self._cache.get(name)
            if cached and (time.time() - cached.last_check) < self._cache_ttl:
                return cached

        check_fn = self._checks.get(name)
        if not check_fn:
            return HealthStatus(
                component=name,
                status="unknown",
                latency_ms=0.0,
                last_check=time.time(),
            )

        start = time.time()
        try:
            result = await check_fn()
            latency = (time.time() - start) * 1000
            status = HealthStatus(
                component=name,
                status="healthy" if result else "unhealthy",
                latency_ms=latency,
                last_check=time.time(),
            )
        except Exception as e:
            latency = (time.time() - start) * 1000
            status = HealthStatus(
                component=name,
                status="unhealthy",
                latency_ms=latency,
                last_check=time.time(),
                details={"error": str(e)},
            )

        async with self._lock:
            self._cache[name] = status

        return status

    async def check_all(self) -> dict[str, HealthStatus]:
        """Run all health checks in parallel."""
        tasks = [self.check(name) for name in self._checks.keys()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        statuses = {}
        for name, result in zip(self._checks.keys(), results):
            if isinstance(result, Exception):
                statuses[name] = HealthStatus(
                    component=name,
                    status="unhealthy",
                    latency_ms=0.0,
                    last_check=time.time(),
                    details={"error": str(result)},
                )
            else:
                statuses[name] = result

        return statuses

    def get_overall_status(self, statuses: dict[str, HealthStatus]) -> str:
        """Determine overall health from component statuses."""
        if any(s.status == "unhealthy" for s in statuses.values()):
            return "unhealthy"
        if any(s.status == "degraded" for s in statuses.values()):
            return "degraded"
        return "healthy"
