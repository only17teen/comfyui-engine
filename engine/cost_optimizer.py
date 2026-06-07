"""
ComfyUI Async Generation Engine v6.0 - Cost Optimization and Resource Scheduling
Cloud cost optimization with intelligent resource scheduling and spot instance management.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class InstanceType(Enum):
    ON_DEMAND = auto()
    SPOT = auto()
    PREEMPTIBLE = auto()
    RESERVED = auto()


class SchedulePriority(Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass
class ResourceSpec:
    """Resource specification for a job."""
    gpu_type: str
    gpu_count: int = 1
    memory_gb: float = 16.0
    cpu_cores: int = 4
    disk_gb: int = 100
    max_duration_minutes: float = 60.0
    preemptible: bool = True


@dataclass
class InstancePricing:
    """Pricing information for an instance type."""
    instance_type: str
    provider: str
    region: str
    on_demand_price: float
    spot_price: float
    preemptible_price: float
    reserved_price: float
    spot_discount: float = 0.7  # 70% discount for spot
    preemptible_discount: float = 0.8  # 80% discount for preemptible


@dataclass
class ScheduledJob:
    """A job scheduled for execution."""
    job_id: str
    resource_spec: ResourceSpec
    priority: SchedulePriority
    estimated_duration_minutes: float
    deadline: Optional[float] = None  # Unix timestamp
    created_at: float = field(default_factory=time.time)
    scheduled_at: Optional[float] = None
    instance_id: Optional[str] = None
    cost_estimate: float = 0.0


class CostOptimizer:
    """
    Cloud cost optimization with intelligent resource scheduling.

    Features:
    - Spot instance management with fallback to on-demand
    - Cost-aware job scheduling across regions and instance types
    - Reserved instance utilization optimization
    - Budget tracking and alerts
    - Cost allocation and chargeback
    - Right-sizing recommendations
    - Idle resource detection and termination
    """

    def __init__(self, budget_usd_per_hour: float = 100.0):
        self.budget_usd_per_hour = budget_usd_per_hour
        self._pricing: Dict[str, InstancePricing] = {}
        self._scheduled_jobs: Dict[str, ScheduledJob] = {}
        self._running_instances: Dict[str, Dict[str, Any]] = {}
        self._cost_history: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._running = False
        self._optimization_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start cost optimization loop."""
        self._running = True
        self._optimization_task = asyncio.create_task(self._optimization_loop())
        logger.info(f"Cost optimizer started (budget: ${self.budget_usd_per_hour}/hour)")

    async def stop(self) -> None:
        """Stop cost optimization loop."""
        self._running = False
        if self._optimization_task:
            self._optimization_task.cancel()
            try:
                await self._optimization_task
            except asyncio.CancelledError:
                pass
        logger.info("Cost optimizer stopped")

    def add_pricing(self, pricing: InstancePricing) -> None:
        """Add instance pricing information."""
        key = f"{pricing.provider}:{pricing.region}:{pricing.instance_type}"
        self._pricing[key] = pricing
        logger.info(f"Added pricing: {key} (on-demand: ${pricing.on_demand_price}/hr)")

    async def schedule_job(self, job: ScheduledJob) -> Tuple[bool, Optional[str], float]:
        """
        Schedule a job with cost optimization.

        Returns:
            Tuple of (success, instance_id, estimated_cost).
        """
        async with self._lock:
            # Find best instance type based on cost and availability
            best_instance = await self._find_best_instance(job)
            
            if not best_instance:
                logger.warning(f"No suitable instance found for job {job.job_id}")
                return False, None, 0.0

            instance_id, cost_per_hour = best_instance
            estimated_cost = cost_per_hour * (job.estimated_duration_minutes / 60)

            # Check budget
            current_hourly_cost = await self._get_current_hourly_cost()
            if current_hourly_cost + cost_per_hour > self.budget_usd_per_hour:
                logger.warning(
                    f"Job {job.job_id} would exceed budget: "
                    f"${current_hourly_cost + cost_per_hour:.2f} > ${self.budget_usd_per_hour:.2f}"
                )
                # Try to find cheaper alternative
                cheaper_instance = await self._find_cheaper_instance(job, cost_per_hour)
                if cheaper_instance:
                    instance_id, cost_per_hour = cheaper_instance
                    estimated_cost = cost_per_hour * (job.estimated_duration_minutes / 60)
                else:
                    return False, None, 0.0

            # Schedule job
            job.scheduled_at = time.time()
            job.instance_id = instance_id
            job.cost_estimate = estimated_cost
            self._scheduled_jobs[job.job_id] = job

            # Track instance usage
            if instance_id not in self._running_instances:
                self._running_instances[instance_id] = {
                    "jobs": [],
                    "start_time": time.time(),
                    "cost_per_hour": cost_per_hour,
                    "instance_type": job.resource_spec.gpu_type,
                }
            self._running_instances[instance_id]["jobs"].append(job.job_id)

            logger.info(
                f"Scheduled job {job.job_id} on {instance_id} "
                f"(est. cost: ${estimated_cost:.2f})"
            )
            return True, instance_id, estimated_cost

    async def complete_job(self, job_id: str, actual_duration_minutes: float) -> float:
        """
        Mark a job as complete and record actual cost.

        Returns:
            Actual cost of the job.
        """
        async with self._lock:
            job = self._scheduled_jobs.get(job_id)
            if not job:
                logger.warning(f"Job not found: {job_id}")
                return 0.0

            # Calculate actual cost
            instance = self._running_instances.get(job.instance_id)
            if instance:
                cost_per_hour = instance["cost_per_hour"]
                actual_cost = cost_per_hour * (actual_duration_minutes / 60)

                # Remove job from instance
                instance["jobs"].remove(job_id)
                
                # Check if instance is idle
                if not instance["jobs"]:
                    await self._terminate_idle_instance(job.instance_id)

                # Record cost
                self._cost_history.append({
                    "job_id": job_id,
                    "instance_id": job.instance_id,
                    "estimated_cost": job.cost_estimate,
                    "actual_cost": actual_cost,
                    "duration_minutes": actual_duration_minutes,
                    "timestamp": time.time(),
                })

                logger.info(
                    f"Job {job_id} completed. Actual cost: ${actual_cost:.2f} "
                    f"(estimated: ${job.cost_estimate:.2f})"
                )
                return actual_cost

            return 0.0

    async def get_cost_report(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Generate cost report for a time period.

        Returns:
            Dictionary with cost breakdown and statistics.
        """
        if start_time is None:
            start_time = time.time() - 86400  # Last 24 hours
        if end_time is None:
            end_time = time.time()

        relevant_costs = [
            c for c in self._cost_history
            if start_time <= c["timestamp"] <= end_time
        ]

        total_estimated = sum(c["estimated_cost"] for c in relevant_costs)
        total_actual = sum(c["actual_cost"] for c in relevant_costs)
        total_jobs = len(relevant_costs)

        # Cost by instance type
        by_instance = {}
        for cost in relevant_costs:
            instance_type = self._running_instances.get(
                cost["instance_id"], {}
            ).get("instance_type", "unknown")
            
            if instance_type not in by_instance:
                by_instance[instance_type] = {"cost": 0.0, "jobs": 0}
            by_instance[instance_type]["cost"] += cost["actual_cost"]
            by_instance[instance_type]["jobs"] += 1

        # Cost efficiency
        efficiency = total_estimated / total_actual if total_actual > 0 else 1.0

        return {
            "period_hours": (end_time - start_time) / 3600,
            "total_jobs": total_jobs,
            "total_estimated_cost": total_estimated,
            "total_actual_cost": total_actual,
            "cost_efficiency": efficiency,
            "average_cost_per_job": total_actual / total_jobs if total_jobs > 0 else 0.0,
            "cost_by_instance_type": by_instance,
            "budget_utilization": total_actual / (self.budget_usd_per_hour * (end_time - start_time) / 3600),
        }

    async def get_right_sizing_recommendations(self) -> List[Dict[str, Any]]:
        """
        Get right-sizing recommendations for running instances.

        Returns:
            List of recommendations with potential savings.
        """
        recommendations = []
        
        for instance_id, instance in self._running_instances.items():
            if not instance["jobs"]:
                continue

            # Analyze utilization
            avg_duration = np.mean([
                self._scheduled_jobs[job_id].estimated_duration_minutes
                for job_id in instance["jobs"]
                if job_id in self._scheduled_jobs
            ]) if instance["jobs"] else 0

            # Check if instance is underutilized
            if avg_duration < 30:  # Less than 30 minutes average
                recommendations.append({
                    "instance_id": instance_id,
                    "current_type": instance["instance_type"],
                    "recommendation": "downsize",
                    "reason": f"Low average job duration ({avg_duration:.1f} min)",
                    "potential_savings_percent": 30,
                })

            # Check if spot instance would be suitable
            if instance["cost_per_hour"] > 0 and not instance.get("is_spot", False):
                spot_savings = instance["cost_per_hour"] * 0.7
                recommendations.append({
                    "instance_id": instance_id,
                    "current_type": instance["instance_type"],
                    "recommendation": "use_spot",
                    "reason": "Jobs are fault-tolerant, spot instances would reduce cost",
                    "potential_savings_percent": 70,
                    "potential_savings_hourly": spot_savings,
                })

        return recommendations

    async def _find_best_instance(
        self,
        job: ScheduledJob,
    ) -> Optional[Tuple[str, float]]:
        """Find the best instance for a job based on cost and requirements."""
        suitable_instances = []

        for key, pricing in self._pricing.items():
            if pricing.instance_type == job.resource_spec.gpu_type:
                # Use spot if job allows preemptible
                if job.resource_spec.preemptible:
                    cost = pricing.spot_price
                    instance_id = f"spot-{pricing.region}-{pricing.instance_type}-{int(time.time())}"
                else:
                    cost = pricing.on_demand_price
                    instance_id = f"ondemand-{pricing.region}-{pricing.instance_type}-{int(time.time())}"

                suitable_instances.append((instance_id, cost, pricing.region))

        if not suitable_instances:
            return None

        # Sort by cost (lowest first)
        suitable_instances.sort(key=lambda x: x[1])
        best = suitable_instances[0]
        return best[0], best[1]

    async def _find_cheaper_instance(
        self,
        job: ScheduledJob,
        max_cost: float,
    ) -> Optional[Tuple[str, float]]:
        """Find a cheaper instance that meets requirements."""
        suitable_instances = []

        for key, pricing in self._pricing.items():
            if pricing.instance_type == job.resource_spec.gpu_type:
                # Try preemptible first
                if job.resource_spec.preemptible and pricing.preemptible_price < max_cost:
                    instance_id = f"preemptible-{pricing.region}-{pricing.instance_type}-{int(time.time())}"
                    suitable_instances.append((instance_id, pricing.preemptible_price))
                # Then spot
                elif pricing.spot_price < max_cost:
                    instance_id = f"spot-{pricing.region}-{pricing.instance_type}-{int(time.time())}"
                    suitable_instances.append((instance_id, pricing.spot_price))

        if not suitable_instances:
            return None

        suitable_instances.sort(key=lambda x: x[1])
        return suitable_instances[0]

    async def _get_current_hourly_cost(self) -> float:
        """Calculate current hourly cost of all running instances."""
        return sum(
            instance["cost_per_hour"]
            for instance in self._running_instances.values()
        )

    async def _terminate_idle_instance(self, instance_id: str) -> None:
        """Terminate an idle instance to save costs."""
        if instance_id in self._running_instances:
            instance = self._running_instances[instance_id]
            runtime_hours = (time.time() - instance["start_time"]) / 3600
            total_cost = instance["cost_per_hour"] * runtime_hours

            logger.info(
                f"Terminating idle instance {instance_id} "
                f"(runtime: {runtime_hours:.1f}h, cost: ${total_cost:.2f})"
            )
            del self._running_instances[instance_id]

    async def _optimization_loop(self) -> None:
        """Main optimization loop."""
        while self._running:
            try:
                await self._optimize_resources()
                await asyncio.sleep(300)  # Run every 5 minutes
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Optimization loop error: {e}")
                await asyncio.sleep(60)

    async def _optimize_resources(self) -> None:
        """Optimize resource allocation."""
        async with self._lock:
            # Check for idle instances
            idle_instances = [
                instance_id for instance_id, instance in self._running_instances.items()
                if not instance["jobs"] and (time.time() - instance["start_time"]) > 600
            ]

            for instance_id in idle_instances:
                await self._terminate_idle_instance(instance_id)

            # Check budget utilization
            current_cost = await self._get_current_hourly_cost()
            utilization = current_cost / self.budget_usd_per_hour

            if utilization > 0.9:
                logger.warning(
                    f"Budget utilization high: {utilization*100:.1f}% "
                    f"(${current_cost:.2f}/${self.budget_usd_per_hour:.2f})"
                )

    def get_stats(self) -> Dict[str, Any]:
        """Get cost optimizer statistics."""
        return {
            "budget_usd_per_hour": self.budget_usd_per_hour,
            "running_instances": len(self._running_instances),
            "scheduled_jobs": len(self._scheduled_jobs),
            "pricing_entries": len(self._pricing),
            "total_cost_history_entries": len(self._cost_history),
            "running": self._running,
        }


# Global cost optimizer instance
_global_cost_optimizer: Optional[CostOptimizer] = None


def get_cost_optimizer() -> Optional[CostOptimizer]:
    """Get global cost optimizer instance."""
    return _global_cost_optimizer


async def initialize_cost_optimizer(budget_usd_per_hour: float = 100.0) -> CostOptimizer:
    """Initialize global cost optimizer."""
    global _global_cost_optimizer
    _global_cost_optimizer = CostOptimizer(budget_usd_per_hour)
    await _global_cost_optimizer.start()
    return _global_cost_optimizer


if __name__ == "__main__":
    async def main():
        optimizer = await initialize_cost_optimizer(budget_usd_per_hour=50.0)

        # Add pricing
        optimizer.add_pricing(InstancePricing(
            instance_type="nvidia-tesla-t4",
            provider="aws",
            region="us-east-1",
            on_demand_price=0.35,
            spot_price=0.10,
            preemptible_price=0.07,
            reserved_price=0.20,
        ))

        optimizer.add_pricing(InstancePricing(
            instance_type="nvidia-tesla-v100",
            provider="aws",
            region="us-east-1",
            on_demand_price=2.48,
            spot_price=0.74,
            preemptible_price=0.50,
            reserved_price=1.50,
        ))

        # Schedule a job
        job = ScheduledJob(
            job_id="job_001",
            resource_spec=ResourceSpec(
                gpu_type="nvidia-tesla-t4",
                preemptible=True,
            ),
            priority=SchedulePriority.NORMAL,
            estimated_duration_minutes=45.0,
        )

        success, instance_id, cost = await optimizer.schedule_job(job)
        print(f"Scheduled job: {success}, instance: {instance_id}, cost: ${cost:.2f}")

        # Get stats
        stats = optimizer.get_stats()
        print(f"Stats: {stats}")

        # Get cost report
        report = await optimizer.get_cost_report()
        print(f"Cost report: {report}")

        # Get recommendations
        recommendations = await optimizer.get_right_sizing_recommendations()
        print(f"Recommendations: {recommendations}")

        await optimizer.stop()

    asyncio.run(main())
