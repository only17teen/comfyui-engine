"""ComfyUI Async Generation Engine v5.0 - Type Stubs
Type safety improvements and protocol definitions.
Enhanced with Kiro Protocol v3.0 optimizations.
"""

from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
    Callable,
    AsyncIterator,
)
from dataclasses import dataclass


@runtime_checkable
class EngineProtocol(Protocol):
    """Protocol for engine implementations."""

    async def health_check(self) -> bool:
        """Check if the engine is healthy."""
        ...

    async def submit_job(self, job_data: dict[str, Any]) -> str:
        """Submit a job and return job ID."""
        ...

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Get status of a specific job."""
        ...

    async def list_jobs(self, status: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        """List jobs with optional filtering."""
        ...

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job."""
        ...

    async def get_queue_status(self) -> dict[str, Any]:
        """Get current queue status."""
        ...

    async def pause_queue(self) -> None:
        """Pause job processing."""
        ...

    async def resume_queue(self) -> None:
        """Resume job processing."""
        ...

    async def get_metrics(self) -> dict[str, Any]:
        """Get engine metrics."""
        ...

    async def get_prometheus_metrics(self) -> list[str]:
        """Get metrics in Prometheus format."""
        ...

    async def shutdown(self) -> None:
        """Gracefully shutdown the engine."""
        ...

    # Kiro Protocol v3.0 Enhancements

    async def configure_gc_tuner(self, config: dict[str, Any]) -> None:
        """Configure garbage collection tuning for stable latency."""
        ...

    async def get_gc_stats(self) -> dict[str, Any]:
        """Get garbage collection statistics."""
        ...

    async def configure_retry_policy(self, policy: dict[str, Any]) -> None:
        """Configure advanced retry policies with jitter and discrimination."""
        ...

    async def initialize_tracing(self, config: dict[str, Any]) -> None:
        """Initialize OpenTelemetry tracing."""
        ...

    async def get_trace_context(self) -> dict[str, Any]:
        """Get current trace context for propagation."""
        ...

    async def configure_gpu_optimization(self, config: dict[str, Any]) -> None:
        """Configure GPU-specific optimizations (memory, streams, etc.)."""
        ...

    async def get_gpu_stats(self) -> dict[str, Any]:
        """Get GPU utilization and memory statistics."""
        ...

    async def enable_advanced_batching(self, enabled: bool) -> None:
        """Enable or disable advanced batching optimizations."""
        ...

    async def get_batch_stats(self) -> dict[str, Any]:
        """Get batching statistics."""
        ...


@runtime_checkable
class MetricsProtocol(Protocol):
    """Protocol for metrics collectors."""

    async def inc(self, metric_name: str, value: int = 1) -> None:
        """Increment a counter metric."""
        ...

    async def gauge(self, metric_name: str, value: float) -> None:
        """Set a gauge metric."""
        ...

    async def observe(self, metric_name: str, value: float) -> None:
        """Observe a value for histogram/summary."""
        ...

    async def histogram(self, metric_name: str, value: float) -> None:
        """Record a value in a histogram."""
        ...

    # Enhanced metrics from Kiro Protocol
    async def timer(self, metric_name: str) -> AsyncIterator[None]:
        """Context manager for timing operations."""
        ...

    async def set_tags(self, tags: dict[str, str]) -> None:
        """Set default tags for all metrics."""
        ...


@runtime_checkable
class QueueProtocol(Protocol):
    """Protocol for job queues."""

    async def enqueue(
        self,
        payload: dict[str, Any],
        meta: dict[str, Any],
        priority: int = 2,
        timeout: float | None = None,
    ) -> Any:
        """Enqueue a job with priority and metadata."""
        ...

    async def dequeue(self) -> Any:
        """Dequeue a job."""
        ...

    def qsize(self) -> int:
        """Get approximate queue size."""
        ...

    def empty(self) -> bool:
        """Check if queue is empty."""
        ...

    # Enhanced queue features
    async def set_rate_limit(self, rate: float, burst: int) -> None:
        """Set rate limiting for the queue."""
        ...

    async def get_queue_latency(self) -> float:
        """Get average queue latency."""
        ...

    async def pause_consumption(self) -> None:
        """Pause consumption from queue."""
        ...

    async def resume_consumption(self) -> None:
        """Resume consumption from queue."""
        ...


@runtime_checkable
class StorageProtocol(Protocol):
    """Protocol for storage backends."""

    async def save(self, key: str, data: dict[str, Any]) -> None:
        """Save data with key."""
        ...

    async def load(self, key: str) -> dict[str, Any] | None:
        """Load data by key."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete data by key."""
        ...

    async def list_keys(self, prefix: str = "") -> list[str]:
        """List keys with optional prefix."""
        ...

    # Enhanced storage features
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        ...

    async def update(self, key: str, data: dict[str, Any]) -> None:
        """Update existing data."""
        ...

    async def get_with_ttl(self, key: str) -> tuple[dict[str, Any] | None, float]:
        """Get data with remaining TTL."""
        ...


@runtime_checkable
class NotifierProtocol(Protocol):
    """Protocol for notification services."""

    async def send(self, message: str, level: str = "info", metadata: dict[str, Any] | None = None) -> bool:
        """Send a notification."""
        ...

    async def alert(self, message: str, metadata: dict[str, Any] | None = None) -> bool:
        """Send an alert notification."""
        ...

    # Enhanced notifier features
    async def register_webhook(self, url: str, events: list[str], secret: str | None = None) -> str:
        """Register a webhook for notifications."""
        ...

    async def unregister_webhook(self, webhook_id: str) -> bool:
        """Unregister a webhook."""
        ...

    async def send_batch_notification(self, batch_id: str, results: dict[str, Any]) -> bool:
        """Send a batch completion notification."""
        ...


# Type aliases for common patterns
JobPayload = dict[str, Any]
JobMeta = dict[str, Any]
JobResult = dict[str, Any]
WorkflowConfig = dict[str, Any]
LoRAConfig = dict[str, Any]
SamplingParams = dict[str, Any]
Resolution = tuple[int, int]
SeedValue = int


# Kiro Protocol v3.0 specific types
@dataclass
class GCTunerConfig:
    """Configuration for GC tuning."""

    freeze_on_boot: bool = True
    freeze_duration: float = 300.0  # 5 minutes
    background_interval: float = 60.0
    generation_thresholds: tuple = (700, 10, 10)  # gen0, gen1, gen2
    max_latency_ms: float = 50.0
    emergency_threshold: float = 0.85  # Memory pressure threshold


@dataclass
class RetryPolicy:
    """Advanced retry policy configuration."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: str = "FULL_JITTER"  # FIXED, LINEAR, EXPONENTIAL, FULL_JITTER, DECORRELATED_JITTER
    retryable_statuses: set[int] = None
    non_retryable_statuses: set[int] = None
    timeout_multiplier: float = 2.0
    jitter_factor: float = 0.1

    def __post_init__(self):
        if self.retryable_statuses is None:
            self.retryable_statuses = {408, 429, 500, 502, 503, 504}
        if self.non_retryable_statuses is None:
            self.non_retryable_statuses = {400, 401, 403, 404, 405, 422}


@dataclass
class TracingConfig:
    """OpenTelemetry tracing configuration."""

    service_name: str = "comfyui-engine"
    service_version: str = "5.0.0"
    environment: str = "production"
    otlp_endpoint: str | None = None
    sampler_ratio: float = 0.1
    enable_debug: bool = False


@dataclass
class GPUOptimizationConfig:
    """GPU optimization configuration."""

    memory_fraction: float = 0.9
    enable_memory_pool: bool = True
    enable_stream_prioritization: bool = True
    stream_priority_high: int = 1
    stream_priority_low: int = 0
    enable_tensor_core: bool = True
    enable_cuda_graphs: bool = False
    max_batch_size: int = 32
