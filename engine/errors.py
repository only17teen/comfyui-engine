"""ComfyUI Async Generation Engine — Structured Error Hierarchy.

All engine-specific exceptions live here.  Callers catch specific subtypes;
the root EngineError catches everything engine-related without swallowing
third-party exceptions accidentally.

Design notes
------------
* Every exception carries a ``context`` dict so logging can emit structured
  JSON without string-formatting the message itself.
* Transient errors (network, Redis, GPU overload) inherit TransientError
  and are safe to retry.
* Fatal errors (bad config, validation failure) inherit FatalError
  and must not be retried.
"""

from __future__ import annotations
from typing import Any


class EngineError(Exception):
    """Base for all engine-specific exceptions."""
    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context: dict[str, Any] = context or {}

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base} | context={self.context}" if self.context else base


class TransientError(EngineError):
    """Temporary failure; operation may succeed on a subsequent attempt."""


class APIUnavailableError(TransientError):
    """ComfyUI server is unreachable or returned a 5xx error."""


class RateLimitError(TransientError):
    """Server returned HTTP 429."""
    def __init__(self, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(f"Rate limited (retry_after={retry_after}s)", **kwargs)
        self.retry_after = retry_after


class QueueFullError(TransientError):
    """In-process job queue has reached its capacity limit."""
    def __init__(self, max_size: int) -> None:
        super().__init__(f"Queue full (max_size={max_size})", context={"max_size": max_size})
        self.max_size = max_size


class CircuitBreakerOpenError(TransientError):
    """Circuit breaker is OPEN; request rejected to protect downstream service."""
    def __init__(self, circuit_name: str, last_failure: float | None = None) -> None:
        super().__init__(
            f"Circuit '{circuit_name}' is OPEN",
            context={"circuit": circuit_name, "last_failure": last_failure},
        )
        self.circuit_name = circuit_name
        self.last_failure = last_failure


class RedisUnavailableError(TransientError):
    """Redis connection failed or timed out."""


class WebSocketError(TransientError):
    """WebSocket connection error or unexpected closure."""


class FatalError(EngineError):
    """Non-recoverable failure; retrying would not help."""


class ConfigurationError(FatalError):
    """Invalid or missing configuration value."""


class WorkflowValidationError(FatalError):
    """Submitted workflow failed schema or node validation."""
    def __init__(self, errors: list[str], warnings: list[str] | None = None) -> None:
        super().__init__(
            f"Workflow validation failed: {errors}",
            context={"errors": errors, "warnings": warnings or []},
        )
        self.errors = errors
        self.warnings = warnings or []


class JobNotFoundError(FatalError):
    """Referenced job ID does not exist."""
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Job not found: {job_id}", context={"job_id": job_id})
        self.job_id = job_id


class SessionError(FatalError):
    """Session could not be created, loaded, or resumed."""


class MaxRetriesExceededError(FatalError):
    """Operation exhausted all retry attempts."""
    def __init__(self, operation: str, attempts: int, last_error: Exception) -> None:
        super().__init__(
            f"'{operation}' failed after {attempts} attempts: {last_error}",
            context={"operation": operation, "attempts": attempts},
        )
        self.operation = operation
        self.attempts = attempts
        self.last_error = last_error


class DownloadError(FatalError):
    """Output file download failed after retries."""


__all__ = [
    "EngineError",
    "TransientError", "APIUnavailableError", "RateLimitError",
    "QueueFullError", "CircuitBreakerOpenError",
    "RedisUnavailableError", "WebSocketError",
    "FatalError", "ConfigurationError", "WorkflowValidationError",
    "JobNotFoundError", "SessionError", "MaxRetriesExceededError",
    "DownloadError",
]
