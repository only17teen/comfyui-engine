"""ComfyUI Async Generation Engine v6.0 - Multi-Region Deployment Manager
Cross-region replication, failover, and global load balancing.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

logger = logging.getLogger(__name__)


class RegionStatus(Enum):
    """Region health status enumeration."""

    HEALTHY = auto()
    DEGRADED = auto()
    UNAVAILABLE = auto()
    MAINTENANCE = auto()


class ReplicationStrategy(Enum):
    """Data replication strategy enumeration."""

    SYNC = auto()  # Synchronous replication
    ASYNC = auto()  # Asynchronous replication
    EVENTUAL = auto()  # Eventual consistency


@dataclass
class RegionConfig:
    """Configuration for a deployment region."""

    name: str  # Region identifier (e.g., us-east-1)
    provider: str  # Cloud provider (aws, gcp, azure)
    endpoint: str  # API endpoint URL
    weight: float = 1.0  # Load balancing weight
    priority: int = 1  # Failover priority (lower = primary)
    health_check_interval: float = 30.0
    health_check_timeout: float = 10.0
    replication_strategy: ReplicationStrategy = ReplicationStrategy.ASYNC
    is_active: bool = True
    max_capacity: int = 100  # Max concurrent jobs
    gpu_types: list[str] = field(default_factory=lambda: ["nvidia-tesla-t4"])
    cost_per_hour: float = 0.0  # Cost per GPU hour


@dataclass
class RegionHealth:
    """Health status for a region."""

    region_name: str
    status: RegionStatus
    last_check: float
    latency_ms: float = 0.0
    success_rate: float = 1.0
    active_jobs: int = 0
    queue_depth: int = 0
    gpu_utilization: float = 0.0
    error_rate: float = 0.0
    consecutive_failures: int = 0


@dataclass
class ReplicationRule:
    """Data replication rule between regions."""

    source_region: str
    target_region: str
    data_types: list[str]  # jobs, models, sessions, configs
    strategy: ReplicationStrategy
    sync_interval_seconds: float = 60.0
    conflict_resolution: str = "last_write_wins"  # last_write_wins, source_wins, merge


class MultiRegionManager:
    """Manages multi-region deployment with cross-region replication and failover.

    Features:
    - Health monitoring across regions
    - Automatic failover with priority-based routing
    - Cross-region data replication (sync/async/eventual)
    - Global load balancing with weighted distribution
    - Capacity-aware job routing
    - Cost optimization across regions
    """

    def __init__(self, regions: list[RegionConfig]):
        self.regions = {r.name: r for r in regions}
        self._health: dict[str, RegionHealth] = {}
        self._replication_rules: list[ReplicationRule] = []
        self._running = False
        self._health_check_task: asyncio.Task | None = None
        self._replication_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Start multi-region management."""
        self._running = True
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        self._replication_task = asyncio.create_task(self._replication_loop())
        logger.info(f"Multi-region manager started with {len(self.regions)} regions")

    async def stop(self) -> None:
        """Stop multi-region management."""
        self._running = False

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        if self._replication_task:
            self._replication_task.cancel()
            try:
                await self._replication_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.close()

        logger.info("Multi-region manager stopped")

    def add_replication_rule(self, rule: ReplicationRule) -> None:
        """Add a cross-region replication rule."""
        self._replication_rules.append(rule)
        logger.info(f"Added replication rule: {rule.source_region} -> {rule.target_region} " f"({rule.strategy.name})")

    async def get_best_region(self, job_requirements: dict[str, Any] | None = None) -> str | None:
        """Get the best region for job execution based on health, capacity, and cost.

        Args:
            job_requirements: Optional job requirements (gpu_type, priority, etc.)

        Returns:
            Region name or None if no healthy regions available.
        """
        async with self._lock:
            healthy_regions = []

            for name, config in self.regions.items():
                if not config.is_active:
                    continue

                health = self._health.get(name)
                if not health:
                    continue

                if health.status == RegionStatus.HEALTHY:
                    # Check capacity
                    if health.active_jobs < config.max_capacity:
                        # Calculate score based on latency, cost, and weight
                        score = (
                            config.weight * 100
                            - health.latency_ms * 0.1
                            - config.cost_per_hour * 10
                            - health.queue_depth * 5
                            - health.gpu_utilization * 2
                        )
                        healthy_regions.append((name, score, config.priority))

            if not healthy_regions:
                # Try degraded regions as fallback
                for name, config in self.regions.items():
                    if not config.is_active:
                        continue
                    health = self._health.get(name)
                    if health and health.status == RegionStatus.DEGRADED:
                        return name
                return None

            # Sort by priority first, then by score
            healthy_regions.sort(key=lambda x: (x[2], -x[1]))
            return healthy_regions[0][0]

    async def route_job(self, job_data: dict[str, Any]) -> tuple[str | None, bool]:
        """Route a job to the best available region.

        Returns:
            Tuple of (region_name, success).
        """
        region = await self.get_best_region(job_data)
        if not region:
            logger.error("No healthy regions available for job routing")
            return None, False

        logger.info(f"Routed job to region: {region}")
        return region, True

    async def replicate_data(
        self,
        data_type: str,
        data: dict[str, Any],
        source_region: str,
    ) -> int:
        """Replicate data to target regions based on replication rules.

        Returns:
            Number of successful replications.
        """
        success_count = 0

        for rule in self._replication_rules:
            if rule.source_region != source_region:
                continue
            if data_type not in rule.data_types:
                continue

            try:
                await self._replicate_to_region(rule, data)
                success_count += 1
            except Exception as e:
                logger.error(f"Replication failed {source_region} -> {rule.target_region}: {e}")

        return success_count

    async def get_region_health(self, region_name: str) -> RegionHealth | None:
        """Get health status for a specific region."""
        return self._health.get(region_name)

    async def get_all_health(self) -> dict[str, RegionHealth]:
        """Get health status for all regions."""
        return dict(self._health)

    async def set_region_maintenance(self, region_name: str, maintenance: bool) -> bool:
        """Set maintenance mode for a region."""
        if region_name not in self.regions:
            return False

        config = self.regions[region_name]
        config.is_active = not maintenance

        if maintenance:
            health = self._health.get(region_name)
            if health:
                health.status = RegionStatus.MAINTENANCE

        logger.info(f"Region {region_name} maintenance mode: {maintenance}")
        return True

    async def _health_check_loop(self) -> None:
        """Periodic health check across all regions."""
        while self._running:
            try:
                await self._check_all_regions()
                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
                await asyncio.sleep(10)

    async def _check_all_regions(self) -> None:
        """Check health of all active regions."""
        tasks = []
        for name, config in self.regions.items():
            if config.is_active:
                tasks.append(self._check_region_health(name, config))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_region_health(self, name: str, config: RegionConfig) -> None:
        """Check health of a single region."""
        start_time = time.time()

        try:
            if not self._session:
                return

            async with self._session.get(
                f"{config.endpoint}/health",
                timeout=aiohttp.ClientTimeout(total=config.health_check_timeout),
            ) as response:
                latency = (time.time() - start_time) * 1000

                if response.status == 200:
                    data = await response.json()

                    health = RegionHealth(
                        region_name=name,
                        status=RegionStatus.HEALTHY,
                        last_check=time.time(),
                        latency_ms=latency,
                        success_rate=1.0,
                        active_jobs=data.get("active_jobs", 0),
                        queue_depth=data.get("queue_depth", 0),
                        gpu_utilization=data.get("gpu_utilization", 0.0),
                        error_rate=0.0,
                        consecutive_failures=0,
                    )
                else:
                    health = RegionHealth(
                        region_name=name,
                        status=RegionStatus.DEGRADED,
                        last_check=time.time(),
                        latency_ms=latency,
                        success_rate=0.0,
                        consecutive_failures=1,
                    )

                async with self._lock:
                    self._health[name] = health

        except asyncio.TimeoutError:
            await self._record_failure(name, "timeout")
        except Exception as e:
            await self._record_failure(name, str(e))

    async def _record_failure(self, name: str, reason: str) -> None:
        """Record a health check failure."""
        async with self._lock:
            health = self._health.get(name)
            if health:
                health.consecutive_failures += 1
                health.last_check = time.time()

                if health.consecutive_failures >= 3:
                    health.status = RegionStatus.UNAVAILABLE
                    logger.warning(f"Region {name} marked unavailable: {reason}")
            else:
                self._health[name] = RegionHealth(
                    region_name=name,
                    status=RegionStatus.UNAVAILABLE,
                    last_check=time.time(),
                    consecutive_failures=1,
                )

    async def _replication_loop(self) -> None:
        """Periodic replication across regions."""
        while self._running:
            try:
                await self._run_replication()
                await asyncio.sleep(60)  # Replicate every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Replication loop error: {e}")
                await asyncio.sleep(30)

    async def _run_replication(self) -> None:
        """Execute pending replications."""
        # This would typically check a queue or database for pending changes
        # For now, this is a placeholder for the replication logic
        pass

    async def _replicate_to_region(self, rule: ReplicationRule, data: dict[str, Any]) -> None:
        """Replicate data to a target region."""
        target_config = self.regions.get(rule.target_region)
        if not target_config:
            raise ValueError(f"Target region not found: {rule.target_region}")

        if not self._session:
            raise RuntimeError("HTTP session not initialized")

        # Send replication request
        async with self._session.post(
            f"{target_config.endpoint}/api/v1/replication",
            json={
                "source_region": rule.source_region,
                "data": data,
                "strategy": rule.strategy.name,
                "conflict_resolution": rule.conflict_resolution,
            },
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"Replication failed: {response.status}")

    def get_stats(self) -> dict[str, Any]:
        """Get multi-region manager statistics."""
        return {
            "total_regions": len(self.regions),
            "active_regions": sum(1 for r in self.regions.values() if r.is_active),
            "healthy_regions": sum(1 for h in self._health.values() if h.status == RegionStatus.HEALTHY),
            "degraded_regions": sum(1 for h in self._health.values() if h.status == RegionStatus.DEGRADED),
            "unavailable_regions": sum(1 for h in self._health.values() if h.status == RegionStatus.UNAVAILABLE),
            "replication_rules": len(self._replication_rules),
            "running": self._running,
        }


# Global multi-region manager instance
_global_multi_region_manager: MultiRegionManager | None = None


def get_multi_region_manager() -> MultiRegionManager | None:
    """Get or create global multi-region manager."""
    return _global_multi_region_manager


async def initialize_multi_region_manager(
    regions: list[RegionConfig],
) -> MultiRegionManager:
    """Initialize global multi-region manager."""
    global _global_multi_region_manager
    _global_multi_region_manager = MultiRegionManager(regions)
    await _global_multi_region_manager.start()
    return _global_multi_region_manager


if __name__ == "__main__":

    async def main():
        # Example configuration
        regions = [
            RegionConfig(
                name="us-east-1",
                provider="aws",
                endpoint="http://localhost:8001",
                weight=1.0,
                priority=1,
                gpu_types=["nvidia-tesla-t4", "nvidia-tesla-v100"],
                cost_per_hour=0.35,
            ),
            RegionConfig(
                name="us-west-2",
                provider="aws",
                endpoint="http://localhost:8002",
                weight=0.8,
                priority=2,
                gpu_types=["nvidia-tesla-t4"],
                cost_per_hour=0.30,
            ),
            RegionConfig(
                name="eu-west-1",
                provider="aws",
                endpoint="http://localhost:8003",
                weight=0.6,
                priority=3,
                gpu_types=["nvidia-tesla-t4"],
                cost_per_hour=0.40,
            ),
        ]

        manager = await initialize_multi_region_manager(regions)

        # Add replication rules
        manager.add_replication_rule(
            ReplicationRule(
                source_region="us-east-1",
                target_region="us-west-2",
                data_types=["jobs", "sessions"],
                strategy=ReplicationStrategy.ASYNC,
            )
        )

        # Wait for health checks
        await asyncio.sleep(2)

        # Get stats
        stats = manager.get_stats()
        print(f"Stats: {stats}")

        # Get best region
        region = await manager.get_best_region()
        print(f"Best region: {region}")

        await manager.stop()

    asyncio.run(main())
