"""ComfyUI Async Generation Engine v4.0 - Type Stubs
Type safety improvements and protocol definitions.
"""

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class EngineProtocol(Protocol):
    """Protocol for engine implementations."""

    async def health_check(self) -> bool:
        ...

    async def submit_job(self, job_data: dict[str, Any]) -> str:
        ...

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        ...

    async def list_jobs(self, status: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        ...

    async def cancel_job(self, job_id: str) -> bool:
        ...

    async def get_queue_status(self) -> dict[str, Any]:
        ...

    async def pause_queue(self) -> None:
        ...

    async def resume_queue(self) -> None:
        ...

    async def get_metrics(self) -> dict[str, Any]:
        ...

    async def get_prometheus_metrics(self) -> list[str]:
        ...

    async def shutdown(self) -> None:
        ...


@runtime_checkable
class MetricsProtocol(Protocol):
    """Protocol for metrics collectors."""

    async def inc(self, metric_name: str, value: int = 1) -> None:
        ...

    async def gauge(self, metric_name: str, value: float) -> None:
        ...

    async def observe(self, metric_name: str, value: float) -> None:
        ...

    async def histogram(self, metric_name: str, value: float) -> None:
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
        ...

    async def dequeue(self) -> Any:
        ...

    def qsize(self) -> int:
        ...

    def empty(self) -> bool:
        ...


@runtime_checkable
class StorageProtocol(Protocol):
    """Protocol for storage backends."""

    async def save(self, key: str, data: dict[str, Any]) -> None:
        ...

    async def load(self, key: str) -> dict[str, Any] | None:
        ...

    async def delete(self, key: str) -> bool:
        ...

    async def list_keys(self, prefix: str = "") -> list[str]:
        ...


@runtime_checkable
class NotifierProtocol(Protocol):
    """Protocol for notification services."""

    async def send(self, message: str, level: str = "info", metadata: dict[str, Any] | None = None) -> bool:
        ...

    async def alert(self, message: str, metadata: dict[str, Any] | None = None) -> bool:
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
