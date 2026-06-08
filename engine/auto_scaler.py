"""ComfyUI Async Generation Engine v5.1 - Auto-Scaler for Distributed Workers
Kiro Protocol: auto-scaling based on queue depth and GPU utilization.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ScalingDecision(Enum):
    """Auto-scaling decision enumeration."""

    SCALE_UP = auto()
    SCALE_DOWN = auto()
    MAINTAIN = auto()
    EMERGENCY = auto()


@dataclass
class ScalingConfig:
    """Configuration for auto-scaling behavior."""

    min_workers: int = 1
    max_workers: int = 10
    target_queue_depth: int = 5
    scale_up_threshold: float = 2.0  # queue_depth / target
    scale_down_threshold: float = 0.3  # queue_depth / target
    scale_up_cooldown: float = 60.0  # seconds
    scale_down_cooldown: float = 300.0  # seconds
    scale_up_step: int = 1
    scale_down_step: int = 1
    gpu_utilization_threshold: float = 0.8  # 80% GPU utilization
    gpu_memory_threshold: float = 0.9  # 90% GPU memory
    emergency_scale_up: int = 3  # Emergency scale up step
    emergency_queue_depth: int = 50  # Queue depth for emergency scaling


@dataclass
class WorkerMetrics:
    """Metrics for a single worker."""

    worker_id: str
    queue_depth: int = 0
    active_jobs: int = 0
    gpu_utilization: float = 0.0
    gpu_memory_used: float = 0.0
    last_heartbeat: float = field(default_factory=time.time)
    healthy: bool = True


@dataclass
class ScalingEvent:
    """Auto-scaling event record."""

    timestamp: float
    decision: ScalingDecision
    from_workers: int
    to_workers: int
    reason: str
    queue_depth: int
    avg_gpu_utilization: float


class AutoScaler:
    """Auto-scaler for distributed ComfyUI workers.
    
    Kiro Protocol optimizations:
    - Queue depth + GPU utilization based scaling (Rule 1: Optimization)
    - Cooldown periods prevent flapping (Rule 4: Reliability)
    - Emergency scaling for sudden load spikes (Rule 4: Reliability)
    - Metrics-based decisions with hysteresis (Rule 1: Optimization)
    """

    def __init__(
        self,
        config: ScalingConfig | None = None,
        metrics_collector: Any | None = None,
    ):
        self.config = config or ScalingConfig()
        self.metrics = metrics_collector
        
        self._workers: dict[str, WorkerMetrics] = {}
        self._current_workers: int = self.config.min_workers
        self._target_workers: int = self.config.min_workers
        
        self._last_scale_up: float = 0.0
        self._last_scale_down: float = 0.0
        self._events: list[ScalingEvent] = []
        
        self._scaling_callback: Callable[[int], Any] | None = None
        self._shutdown: bool = False
        self._lock = asyncio.Lock()

    def register_scaling_callback(self, callback: Callable[[int], Any]) -> None:
        """Register callback for scaling actions.
        
        Args:
            callback: Function that takes target worker count.
        """
        self._scaling_callback = callback

    async def update_worker_metrics(self, worker_id: str, metrics: WorkerMetrics) -> None:
        """Update metrics for a worker."""
        async with self._lock:
            self._workers[worker_id] = metrics

    async def remove_worker(self, worker_id: str) -> None:
        """Remove a worker from tracking."""
        async with self._lock:
            if worker_id in self._workers:
                del self._workers[worker_id]

    async def get_aggregate_metrics(self) -> dict[str, Any]:
        """Get aggregated metrics across all workers."""
        async with self._lock:
            if not self._workers:
                return {
                    "total_workers": 0,
                    "total_queue_depth": 0,
                    "avg_gpu_utilization": 0.0,
                    "avg_gpu_memory": 0.0,
                    "healthy_workers": 0,
                }

            total_queue = sum(w.queue_depth for w in self._workers.values())
            avg_gpu = sum(w.gpu_utilization for w in self._workers.values()) / len(self._workers)
            avg_mem = sum(w.gpu_memory_used for w in self._workers.values()) / len(self._workers)
            healthy = sum(1 for w in self._workers.values() if w.healthy)

            return {
                "total_workers": len(self._workers),
                "total_queue_depth": total_queue,
                "avg_gpu_utilization": avg_gpu,
                "avg_gpu_memory": avg_mem,
                "healthy_workers": healthy,
            }

    async def evaluate_scaling(self) -> ScalingDecision:
        """Evaluate whether to scale up, down, or maintain.
        
        Kiro Protocol: Hysteresis-based scaling to prevent flapping.
        """
        async with self._lock:
            metrics = await self.get_aggregate_metrics()
            
            total_queue = metrics["total_queue_depth"]
            avg_gpu = metrics["avg_gpu_utilization"]
            avg_mem = metrics["avg_gpu_memory"]
            current = self._current_workers
            
            now = time.time()
            
            # Emergency scaling: very high queue depth
            if total_queue > self.config.emergency_queue_depth:
                if now - self._last_scale_up >= self.config.scale_up_cooldown:
                    self._target_workers = min(
                        current + self.config.emergency_scale_up,
                        self.config.max_workers,
                    )
                    return ScalingDecision.EMERGENCY
                return ScalingDecision.MAINTAIN
            
            # Scale up: queue depth too high or GPU utilization high
            queue_ratio = total_queue / (self.config.target_queue_depth * current) if current > 0 else 0
            
            if (queue_ratio > self.config.scale_up_threshold or 
                avg_gpu > self.config.gpu_utilization_threshold or
                avg_mem > self.config.gpu_memory_threshold):
                if now - self._last_scale_up >= self.config.scale_up_cooldown:
                    self._target_workers = min(
                        current + self.config.scale_up_step,
                        self.config.max_workers,
                    )
                    return ScalingDecision.SCALE_UP
                return ScalingDecision.MAINTAIN
            
            # Scale down: queue depth very low
            if queue_ratio < self.config.scale_down_threshold and current > self.config.min_workers:
                if now - self._last_scale_down >= self.config.scale_down_cooldown:
                    self._target_workers = max(
                        current - self.config.scale_down_step,
                        self.config.min_workers,
                    )
                    return ScalingDecision.SCALE_DOWN
                return ScalingDecision.MAINTAIN
            
            return ScalingDecision.MAINTAIN

    async def execute_scaling(self, decision: ScalingDecision) -> ScalingEvent:
        """Execute scaling decision."""
        async with self._lock:
            from_workers = self._current_workers
            to_workers = self._target_workers
            
            if decision == ScalingDecision.SCALE_UP:
                self._last_scale_up = time.time()
                reason = f"Queue depth high ({self._get_queue_depth()}), scaling up"
            elif decision == ScalingDecision.EMERGENCY:
                self._last_scale_up = time.time()
                reason = f"Emergency: queue depth critical ({self._get_queue_depth()})"
            elif decision == ScalingDecision.SCALE_DOWN:
                self._last_scale_down = time.time()
                reason = f"Queue depth low, scaling down"
            else:
                reason = "Maintaining current capacity"
            
            self._current_workers = to_workers
            
            event = ScalingEvent(
                timestamp=time.time(),
                decision=decision,
                from_workers=from_workers,
                to_workers=to_workers,
                reason=reason,
                queue_depth=self._get_queue_depth(),
                avg_gpu_utilization=self._get_avg_gpu(),
            )
            self._events.append(event)
            
            # Execute scaling callback
            if self._scaling_callback and decision != ScalingDecision.MAINTAIN:
                try:
                    await self._scaling_callback(to_workers)
                except Exception as e:
                    logger.error(f"Scaling callback failed: {e}")
            
            if self.metrics:
                await self.metrics.gauge("target_workers", float(to_workers))
                await self.metrics.gauge("current_workers", float(from_workers))
            
            return event

    def _get_queue_depth(self) -> int:
        """Get total queue depth."""
        return sum(w.queue_depth for w in self._workers.values())

    def _get_avg_gpu(self) -> float:
        """Get average GPU utilization."""
        if not self._workers:
            return 0.0
        return sum(w.gpu_utilization for w in self._workers.values()) / len(self._workers)

    async def start_monitoring(self, interval: float = 30.0) -> None:
        """Start continuous monitoring and scaling loop."""
        while not self._shutdown:
            try:
                decision = await self.evaluate_scaling()
                if decision != ScalingDecision.MAINTAIN:
                    event = await self.execute_scaling(decision)
                    logger.info(
                        f"Scaling: {event.decision.name} from {event.from_workers} to "
                        f"{event.to_workers} workers ({event.reason})"
                    )
                await asyncio.sleep(interval)
            except Exception as e:
                logger.error(f"Auto-scaler monitoring error: {e}")
                await asyncio.sleep(interval)

    async def stop(self) -> None:
        """Stop auto-scaler."""
        self._shutdown = True

    def get_events(self, limit: int = 100) -> list[ScalingEvent]:
        """Get recent scaling events."""
        return self._events[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """Get auto-scaler statistics."""
        return {
            "current_workers": self._current_workers,
            "target_workers": self._target_workers,
            "min_workers": self.config.min_workers,
            "max_workers": self.config.max_workers,
            "total_events": len(self._events),
            "scale_up_events": sum(1 for e in self._events if e.decision == ScalingDecision.SCALE_UP),
            "scale_down_events": sum(1 for e in self._events if e.decision == ScalingDecision.SCALE_DOWN),
            "emergency_events": sum(1 for e in self._events if e.decision == ScalingDecision.EMERGENCY),
            "last_scale_up": self._last_scale_up,
            "last_scale_down": self._last_scale_down,
            "workers": {wid: {
                "queue_depth": w.queue_depth,
                "gpu_utilization": w.gpu_utilization,
                "healthy": w.healthy,
            } for wid, w in self._workers.items()},
        }
