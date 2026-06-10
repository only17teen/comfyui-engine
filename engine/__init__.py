"""ComfyUI Async Generation Engine.

Public API surface - import from here for stable interface.
"""

from __future__ import annotations

__version__ = "5.1.0"
__all__ = [
    "__version__",
    "MetricsCollector",
    "MetricsSnapshot",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpenError",
    "CircuitState",
    "RetryConfig",
    "with_retry",
    "JobQueue",
    "PrioritizedJob",
    "QueueFullError",
    "EngineConfig",
    "ConfigLoader",
    "HealthRegistry",
    "HealthStatus",
    "CheckResult",
    "AggregateHealth",
    "make_http_check",
    "make_redis_check",
    "EventBus",
    "JobEvent",
    "QueueEvent",
    "CircuitBreakerEvent",
    "HealthEvent",
    "default_bus",
]

from engine.metrics import MetricsCollector, MetricsSnapshot
from engine.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
)
from engine.retry import RetryConfig, with_retry
from engine.queue import JobQueue, PrioritizedJob, QueueFullError
from engine.config import EngineConfig, ConfigLoader
from engine.health import (
    HealthRegistry,
    HealthStatus,
    CheckResult,
    AggregateHealth,
    make_http_check,
    make_redis_check,
)
from engine.events import (
    EventBus,
    JobEvent,
    QueueEvent,
    CircuitBreakerEvent,
    HealthEvent,
    default_bus,
)
