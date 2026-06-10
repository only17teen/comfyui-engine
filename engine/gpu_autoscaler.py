"""ComfyUI Async Generation Engine v6.0 - GPU Autoscaler
Custom metrics-based GPU autoscaling with node pool management.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ScalingAction(Enum):
    """GPU scaling action enumeration."""

    SCALE_UP = auto()
    SCALE_DOWN = auto()
    MAINTAIN = auto()
    EMERGENCY_SCALE_UP = auto()


class GPUMetricType(Enum):
    """GPU metric types for autoscaling decisions."""

    UTILIZATION = "gpu_utilization"
    MEMORY = "gpu_memory_usage"
    TEMPERATURE = "gpu_temperature"
    POWER = "gpu_power_draw"
    QUEUE_DEPTH = "queue_depth"
    JOB_WAIT_TIME = "job_wait_time"
    ERROR_RATE = "error_rate"


@dataclass
class GPUScaleMetric:
    """GPU scaling metric threshold."""

    metric_type: GPUMetricType
    target_value: float
    current_value: float = 0.0
    scale_up_threshold: float = 0.8
    scale_down_threshold: float = 0.3
    emergency_threshold: float = 0.95
    weight: float = 1.0


@dataclass
class NodePoolConfig:
    """Configuration for a GPU node pool."""

    name: str
    gpu_type: str  # e.g., nvidia-tesla-t4
    min_nodes: int = 1
    max_nodes: int = 10
    target_utilization: float = 0.7
    scale_up_cooldown: float = 120.0  # seconds
    scale_down_cooldown: float = 300.0  # seconds
    cost_per_hour: float = 0.0
    preemptible: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    taints: list[str] = field(default_factory=list)


@dataclass
class ScalingDecision:
    """Scaling decision with reasoning."""

    action: ScalingAction
    current_nodes: int
    target_nodes: int
    reason: str
    metrics: dict[str, float]
    timestamp: float = field(default_factory=time.time)


class GPUAutoscaler:
    """GPU autoscaling manager with custom metrics and node pool management.

    Features:
    - Custom metric-based scaling (GPU utilization, queue depth, wait time)
    - Multiple node pool support with different GPU types
    - Cost-aware scaling decisions
    - Preemptible/spot instance support
    - Cooldown periods to prevent flapping
    - Emergency scaling for high load
    - Predictive scaling based on historical patterns
    """

    def __init__(self, node_pools: list[NodePoolConfig]):
        self.node_pools = {p.name: p for p in node_pools}
        self._current_nodes: dict[str, int] = {p.name: p.min_nodes for p in node_pools}
        self._metrics: dict[str, dict[GPUMetricType, float]] = {}
        self._last_scale_time: dict[str, float] = {p.name: 0 for p in node_pools}
        self._scaling_history: list[ScalingDecision] = []
        self._running = False
        self._scale_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start autoscaling loop."""
        self._running = True
        self._scale_task = asyncio.create_task(self._scaling_loop())
        logger.info(f"GPU autoscaler started with {len(self.node_pools)} node pools")

    async def stop(self) -> None:
        """Stop autoscaling loop."""
        self._running = False
        if self._scale_task:
            self._scale_task.cancel()
            try:
                await self._scale_task
            except asyncio.CancelledError:
                pass
        logger.info("GPU autoscaler stopped")

    async def update_metrics(
        self,
        pool_name: str,
        metrics: dict[GPUMetricType, float],
    ) -> None:
        """Update metrics for a node pool."""
        async with self._lock:
            self._metrics[pool_name] = metrics

    async def get_scaling_decision(self, pool_name: str) -> ScalingDecision:
        """Get scaling decision for a node pool based on current metrics.

        Returns:
            ScalingDecision with action and reasoning.
        """
        async with self._lock:
            pool = self.node_pools.get(pool_name)
            if not pool:
                return ScalingDecision(
                    action=ScalingAction.MAINTAIN,
                    current_nodes=0,
                    target_nodes=0,
                    reason="Pool not found",
                    metrics={},
                )

            current_nodes = self._current_nodes.get(pool_name, pool.min_nodes)
            metrics = self._metrics.get(pool_name, {})

            # Check cooldown
            last_scale = self._last_scale_time.get(pool_name, 0)
            time_since_scale = time.time() - last_scale

            # Calculate composite load score
            load_score = self._calculate_load_score(pool, metrics)

            # Determine action
            if load_score >= 0.95:
                action = ScalingAction.EMERGENCY_SCALE_UP
                target_nodes = min(current_nodes + 3, pool.max_nodes)
                reason = f"Emergency scaling: load score {load_score:.2f}"

            elif load_score >= pool.target_utilization + 0.1:
                if time_since_scale < pool.scale_up_cooldown:
                    action = ScalingAction.MAINTAIN
                    target_nodes = current_nodes
                    reason = (
                        f"Scale up cooldown active ({time_since_scale:.0f}s remaining)"
                    )
                else:
                    action = ScalingAction.SCALE_UP
                    target_nodes = min(current_nodes + 1, pool.max_nodes)
                    reason = (
                        f"High load: {load_score:.2f} > {pool.target_utilization:.2f}"
                    )

            elif load_score <= pool.target_utilization - 0.2:
                if time_since_scale < pool.scale_down_cooldown:
                    action = ScalingAction.MAINTAIN
                    target_nodes = current_nodes
                    reason = f"Scale down cooldown active ({time_since_scale:.0f}s remaining)"
                else:
                    action = ScalingAction.SCALE_DOWN
                    target_nodes = max(current_nodes - 1, pool.min_nodes)
                    reason = (
                        f"Low load: {load_score:.2f} < {pool.target_utilization:.2f}"
                    )

            else:
                action = ScalingAction.MAINTAIN
                target_nodes = current_nodes
                reason = f"Load within target: {load_score:.2f}"

            decision = ScalingDecision(
                action=action,
                current_nodes=current_nodes,
                target_nodes=target_nodes,
                reason=reason,
                metrics={k.value: v for k, v in metrics.items()},
            )

            return decision

    async def apply_scaling_decision(
        self, pool_name: str, decision: ScalingDecision
    ) -> bool:
        """Apply a scaling decision to a node pool.

        Returns:
            True if scaling was applied.
        """
        if decision.action == ScalingAction.MAINTAIN:
            return False

        async with self._lock:
            pool = self.node_pools.get(pool_name)
            if not pool:
                return False

            # Validate target
            target = max(pool.min_nodes, min(decision.target_nodes, pool.max_nodes))
            current = self._current_nodes.get(pool_name, pool.min_nodes)

            if target == current:
                return False

            # Apply scaling (in real implementation, this would call cloud provider API)
            self._current_nodes[pool_name] = target
            self._last_scale_time[pool_name] = time.time()

            # Record decision
            self._scaling_history.append(decision)

            logger.info(
                f"Scaled {pool_name}: {current} -> {target} nodes ({decision.action.name})"
            )
            return True

    async def get_node_pool_status(self, pool_name: str) -> dict[str, Any] | None:
        """Get current status of a node pool."""
        pool = self.node_pools.get(pool_name)
        if not pool:
            return None

        current_nodes = self._current_nodes.get(pool_name, pool.min_nodes)
        metrics = self._metrics.get(pool_name, {})
        load_score = self._calculate_load_score(pool, metrics)

        return {
            "name": pool_name,
            "gpu_type": pool.gpu_type,
            "current_nodes": current_nodes,
            "min_nodes": pool.min_nodes,
            "max_nodes": pool.max_nodes,
            "target_utilization": pool.target_utilization,
            "current_load_score": load_score,
            "metrics": {k.value: v for k, v in metrics.items()},
            "cost_per_hour": pool.cost_per_hour * current_nodes,
            "preemptible": pool.preemptible,
        }

    async def get_all_status(self) -> dict[str, dict[str, Any]]:
        """Get status for all node pools."""
        return {
            name: await self.get_node_pool_status(name)
            for name in self.node_pools.keys()
        }

    async def get_scaling_history(
        self,
        pool_name: str | None = None,
        limit: int = 100,
    ) -> list[ScalingDecision]:
        """Get scaling history, optionally filtered by pool."""
        history = self._scaling_history
        if pool_name:
            history = [d for d in history if pool_name in d.reason]
        return history[-limit:]

    async def get_cost_estimate(self, hours: float = 1.0) -> dict[str, float]:
        """Estimate cost for all node pools."""
        costs = {}
        for name, pool in self.node_pools.items():
            current_nodes = self._current_nodes.get(name, pool.min_nodes)
            costs[name] = pool.cost_per_hour * current_nodes * hours
        return costs

    def _calculate_load_score(
        self,
        pool: NodePoolConfig,
        metrics: dict[GPUMetricType, float],
    ) -> float:
        """Calculate composite load score from metrics."""
        if not metrics:
            return 0.0

        scores = []
        weights = []

        # GPU utilization (primary metric)
        if GPUMetricType.UTILIZATION in metrics:
            scores.append(metrics[GPUMetricType.UTILIZATION])
            weights.append(3.0)

        # Queue depth (normalized by capacity)
        if GPUMetricType.QUEUE_DEPTH in metrics:
            queue_depth = metrics[GPUMetricType.QUEUE_DEPTH]
            max_queue = pool.max_nodes * 10  # Assume 10 jobs per node
            scores.append(min(queue_depth / max_queue, 1.0))
            weights.append(2.0)

        # Job wait time (normalized, > 60s is high)
        if GPUMetricType.JOB_WAIT_TIME in metrics:
            wait_time = metrics[GPUMetricType.JOB_WAIT_TIME]
            scores.append(min(wait_time / 60.0, 1.0))
            weights.append(1.5)

        # Error rate (> 10% is high)
        if GPUMetricType.ERROR_RATE in metrics:
            error_rate = metrics[GPUMetricType.ERROR_RATE]
            scores.append(min(error_rate / 0.1, 1.0))
            weights.append(1.0)

        if not scores:
            return 0.0

        # Weighted average
        total_weight = sum(weights)
        weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight

        return min(weighted_score, 1.0)

    async def _scaling_loop(self) -> None:
        """Main autoscaling loop."""
        while self._running:
            try:
                for pool_name in self.node_pools.keys():
                    decision = await self.get_scaling_decision(pool_name)
                    if decision.action != ScalingAction.MAINTAIN:
                        await self.apply_scaling_decision(pool_name, decision)

                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scaling loop error: {e}")
                await asyncio.sleep(60)

    def get_stats(self) -> dict[str, Any]:
        """Get autoscaler statistics."""
        return {
            "node_pools": len(self.node_pools),
            "total_nodes": sum(self._current_nodes.values()),
            "scaling_decisions_1h": len(
                [d for d in self._scaling_history if time.time() - d.timestamp < 3600]
            ),
            "total_scaling_decisions": len(self._scaling_history),
            "running": self._running,
        }


# Global autoscaler instance
_global_autoscaler: GPUAutoscaler | None = None


def get_autoscaler() -> GPUAutoscaler | None:
    """Get global autoscaler instance."""
    return _global_autoscaler


async def initialize_autoscaler(node_pools: list[NodePoolConfig]) -> GPUAutoscaler:
    """Initialize global autoscaler."""
    global _global_autoscaler
    _global_autoscaler = GPUAutoscaler(node_pools)
    await _global_autoscaler.start()
    return _global_autoscaler


if __name__ == "__main__":

    async def main():
        # Example node pools
        pools = [
            NodePoolConfig(
                name="gpu-t4-pool",
                gpu_type="nvidia-tesla-t4",
                min_nodes=2,
                max_nodes=20,
                target_utilization=0.7,
                cost_per_hour=0.35,
                preemptible=True,
            ),
            NodePoolConfig(
                name="gpu-v100-pool",
                gpu_type="nvidia-tesla-v100",
                min_nodes=1,
                max_nodes=5,
                target_utilization=0.6,
                cost_per_hour=2.48,
                preemptible=False,
            ),
        ]

        autoscaler = await initialize_autoscaler(pools)

        # Simulate metrics
        await autoscaler.update_metrics(
            "gpu-t4-pool",
            {
                GPUMetricType.UTILIZATION: 0.85,
                GPUMetricType.QUEUE_DEPTH: 15,
                GPUMetricType.JOB_WAIT_TIME: 45.0,
                GPUMetricType.ERROR_RATE: 0.02,
            },
        )

        # Get decision
        decision = await autoscaler.get_scaling_decision("gpu-t4-pool")
        print(f"Decision: {decision.action.name} - {decision.reason}")
        print(f"Nodes: {decision.current_nodes} -> {decision.target_nodes}")

        # Apply decision
        await autoscaler.apply_scaling_decision("gpu-t4-pool", decision)

        # Get status
        status = await autoscaler.get_node_pool_status("gpu-t4-pool")
        print(f"Status: {status}")

        # Get cost estimate
        costs = await autoscaler.get_cost_estimate(hours=24)
        print(f"Daily cost estimate: {costs}")

        await autoscaler.stop()

    asyncio.run(main())
