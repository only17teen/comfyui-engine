"""Distributed multi-node GPU cluster support for ComfyUI Engine.

Manages a cluster of GPU nodes for distributed generation:
- Node discovery and health monitoring
- Load balancing across GPUs
- Job distribution with failover
- Model synchronization between nodes
- Cluster-wide metrics aggregation
"""

import asyncio
import hashlib
import json
import logging
import random
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class NodeStatus(Enum):
    """Status of a cluster node."""

    HEALTHY = "healthy"
    BUSY = "busy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


@dataclass
class NodeCapabilities:
    """Hardware capabilities of a node."""

    gpu_count: int = 0
    gpu_models: list[str] = field(default_factory=list)
    gpu_memory_mb: list[int] = field(default_factory=list)
    cpu_cores: int = 0
    total_memory_mb: int = 0
    cuda_version: str = ""
    supported_dtypes: list[str] = field(default_factory=lambda: ["fp32"])
    max_batch_size: int = 1
    network_bandwidth_mbps: float = 1000.0


@dataclass
class NodeInfo:
    """Information about a cluster node."""

    node_id: str
    host: str
    port: int
    status: NodeStatus = NodeStatus.HEALTHY
    capabilities: NodeCapabilities = field(default_factory=NodeCapabilities)
    current_jobs: int = 0
    max_jobs: int = 4
    last_heartbeat: float = field(default_factory=time.time)
    uptime_seconds: float = 0.0
    total_jobs_completed: int = 0
    total_jobs_failed: int = 0
    avg_generation_time_ms: float = 0.0
    # Model availability on this node
    available_models: set[str] = field(default_factory=set)
    # Tags for scheduling constraints
    tags: set[str] = field(default_factory=set)
    # Priority weight (higher = preferred)
    weight: float = 1.0

    @property
    def is_available(self) -> bool:
        """Check if node can accept new jobs."""
        return (
            self.status in (NodeStatus.HEALTHY, NodeStatus.BUSY)
            and self.current_jobs < self.max_jobs
            and time.time() - self.last_heartbeat < 30.0
        )

    @property
    def load_factor(self) -> float:
        """Current load factor (0.0 to 1.0+)."""
        return self.current_jobs / max(self.max_jobs, 1)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        data["available_models"] = list(self.available_models)
        data["tags"] = list(self.tags)
        return data


@dataclass
class DistributedJob:
    """Job scheduled for distributed execution."""

    job_id: str
    workflow: dict[str, Any]
    priority: int = 50
    required_models: list[str] = field(default_factory=list)
    required_tags: list[str] = field(default_factory=list)
    min_gpu_memory_mb: int = 4096
    preferred_dtype: str = "fp16"
    # Scheduling
    assigned_node: str | None = None
    assigned_gpu: int = 0
    # Timing
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    # Result
    status: str = "pending"  # pending, assigned, running, completed, failed
    result: dict[str, Any] | None = None
    error: str | None = None
    # Retry
    retry_count: int = 0
    max_retries: int = 3


class ClusterCoordinator:
    """Coordinates a cluster of GPU nodes for distributed generation.

    Manages node discovery, health monitoring, job distribution,
    and cluster-wide state synchronization via Redis.
    """

    def __init__(
        self,
        node_id: str | None = None,
        redis_url: str = "redis://localhost:6379/0",
        heartbeat_interval: float = 5.0,
        node_timeout: float = 30.0,
    ):
        self.node_id = node_id or self._generate_node_id()
        self.redis_url = redis_url
        self.heartbeat_interval = heartbeat_interval
        self.node_timeout = node_timeout

        # Redis connection
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None

        # Cluster state
        self._nodes: dict[str, NodeInfo] = {}
        self._jobs: dict[str, DistributedJob] = {}
        self._lock = asyncio.Lock()

        # Background tasks
        self._heartbeat_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._scheduler_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

        # Local node info (if this is a worker)
        self._local_node: NodeInfo | None = None
        self._local_capabilities: NodeCapabilities | None = None

    def _generate_node_id(self) -> str:
        """Generate unique node ID from hostname and timestamp."""
        import socket

        hostname = socket.gethostname()
        timestamp = str(time.time())
        return hashlib.sha256(f"{hostname}-{timestamp}".encode()).hexdigest()[:12]

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = await redis.from_url(
                self.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def initialize(
        self,
        host: str = "0.0.0.0",
        port: int = 8188,
        capabilities: NodeCapabilities | None = None,
        is_coordinator: bool = True,
    ) -> None:
        """Initialize cluster coordinator.

        Args:
            host: Hostname/IP for this node
            port: Port for ComfyUI API
            capabilities: Hardware capabilities (auto-detected if None)
            is_coordinator: Whether this node is the coordinator
        """
        # Detect capabilities if not provided
        if capabilities is None:
            capabilities = await self._detect_capabilities()

        self._local_capabilities = capabilities

        # Register local node
        self._local_node = NodeInfo(
            node_id=self.node_id,
            host=host,
            port=port,
            capabilities=capabilities,
            max_jobs=capabilities.gpu_count * 2,  # 2 jobs per GPU
            available_models=set(),  # Will be populated
        )

        # Connect to Redis
        await self._get_redis()

        # Register in cluster
        await self._register_node()

        # Start background tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._monitor_task = asyncio.create_task(self._monitor_nodes())

        if is_coordinator:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info(f"Cluster coordinator initialized: {self.node_id}")

    async def _detect_capabilities(self) -> NodeCapabilities:
        """Auto-detect hardware capabilities."""
        caps = NodeCapabilities()

        try:
            import torch

            caps.gpu_count = torch.cuda.device_count()

            for i in range(caps.gpu_count):
                props = torch.cuda.get_device_properties(i)
                caps.gpu_models.append(props.name)
                caps.gpu_memory_mb.append(props.total_memory // (1024 * 1024))

                # Check supported dtypes
                if props.major >= 7:  # Volta+
                    caps.supported_dtypes.extend(["fp16", "bf16"])
                if props.major >= 8:  # Ampere+
                    caps.supported_dtypes.append("fp8")

            caps.cuda_version = torch.version.cuda or ""

        except ImportError:
            logger.warning("PyTorch not available, GPU detection skipped")

        # CPU info
        import psutil

        caps.cpu_cores = psutil.cpu_count()
        caps.total_memory_mb = psutil.virtual_memory().total // (1024 * 1024)

        return caps

    async def _register_node(self) -> None:
        """Register this node in the cluster."""
        r = await self._get_redis()

        node_data = self._local_node.to_dict()

        # Store node info
        await r.hset(
            "cluster:nodes",
            self.node_id,
            json.dumps(node_data),
        )

        # Add to active set
        await r.sadd("cluster:active", self.node_id)

        # Publish join event
        await r.publish(
            "cluster:events",
            json.dumps(
                {
                    "type": "node_join",
                    "node_id": self.node_id,
                    "timestamp": time.time(),
                }
            ),
        )

        logger.info(f"Registered node {self.node_id} in cluster")

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to maintain node liveness."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.heartbeat_interval,
                )
                break
            except asyncio.TimeoutError:
                await self._send_heartbeat()

    async def _send_heartbeat(self) -> None:
        """Send heartbeat with current node status."""
        try:
            r = await self._get_redis()

            # Update local node stats
            self._local_node.last_heartbeat = time.time()
            self._local_node.current_jobs = len(
                [
                    j
                    for j in self._jobs.values()
                    if j.assigned_node == self.node_id and j.status == "running"
                ]
            )

            # Update in Redis
            await r.hset(
                "cluster:nodes",
                self.node_id,
                json.dumps(self._local_node.to_dict()),
            )

            # Refresh expiration
            await r.expire("cluster:nodes", int(self.node_timeout * 2))

        except Exception as e:
            logger.warning(f"Heartbeat failed: {e}")

    async def _monitor_nodes(self) -> None:
        """Monitor cluster nodes and detect failures."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.node_timeout)

                r = await self._get_redis()

                # Get all nodes
                nodes_data = await r.hgetall("cluster:nodes")
                current_time = time.time()

                for node_id, data in nodes_data.items():
                    try:
                        node_data = json.loads(data)
                        last_heartbeat = node_data.get("last_heartbeat", 0)

                        if current_time - last_heartbeat > self.node_timeout:
                            # Node appears dead
                            logger.warning(f"Node {node_id} timed out")

                            # Mark offline
                            await r.hset(
                                "cluster:nodes",
                                node_id,
                                json.dumps({**node_data, "status": "offline"}),
                            )

                            # Remove from active set
                            await r.srem("cluster:active", node_id)

                            # Reassign jobs
                            await self._reassign_jobs(node_id)

                    except json.JSONDecodeError:
                        logger.warning(f"Invalid node data for {node_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Node monitor error: {e}")

    async def _scheduler_loop(self) -> None:
        """Main scheduling loop for distributing jobs."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(0.5)  # 500ms scheduling interval

                # Get pending jobs from queue
                r = await self._get_redis()

                # Check for new jobs in Redis queue
                job_data = await r.lpop("cluster:job_queue")
                if job_data:
                    job = DistributedJob(**json.loads(job_data))
                    await self._schedule_job(job)

                # Also check local queue
                async with self._lock:
                    pending = [j for j in self._jobs.values() if j.status == "pending"]

                for job in pending:
                    await self._schedule_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")

    async def _schedule_job(self, job: DistributedJob) -> bool:
        """Schedule a job on the best available node.

        Returns:
            True if job was scheduled
        """
        # Get available nodes
        nodes = await self._get_available_nodes()

        if not nodes:
            logger.warning(f"No available nodes for job {job.job_id}")
            return False

        # Filter by requirements
        eligible = self._filter_nodes(nodes, job)

        if not eligible:
            logger.warning(f"No nodes meet requirements for job {job.job_id}")
            return False

        # Select best node using weighted scoring
        best_node = self._select_best_node(eligible, job)

        if best_node is None:
            return False

        # Assign job
        job.assigned_node = best_node.node_id
        job.status = "assigned"
        job.started_at = time.time()

        async with self._lock:
            self._jobs[job.job_id] = job

        # Send to node
        success = await self._dispatch_job(best_node, job)

        if success:
            logger.info(f"Scheduled job {job.job_id} on node {best_node.node_id}")
            return True
        else:
            # Mark for retry
            job.retry_count += 1
            job.status = "pending" if job.retry_count < job.max_retries else "failed"
            job.error = "Dispatch failed"

            if job.status == "failed":
                logger.error(f"Job {job.job_id} failed after {job.retry_count} retries")

            return False

    async def _get_available_nodes(self) -> list[NodeInfo]:
        """Get all currently available nodes."""
        r = await self._get_redis()

        nodes_data = await r.hgetall("cluster:nodes")
        nodes = []

        for _node_id, data in nodes_data.items():
            try:
                node_data = json.loads(data)
                node = NodeInfo(**node_data)

                # Convert string status back to enum
                node.status = NodeStatus(node_data.get("status", "offline"))

                if node.is_available:
                    nodes.append(node)
            except (json.JSONDecodeError, ValueError):
                continue

        return nodes

    def _filter_nodes(
        self,
        nodes: list[NodeInfo],
        job: DistributedJob,
    ) -> list[NodeInfo]:
        """Filter nodes by job requirements."""
        eligible = []

        for node in nodes:
            # Check GPU memory
            if node.capabilities.gpu_memory_mb:
                max_gpu_mem = max(node.capabilities.gpu_memory_mb)
                if max_gpu_mem < job.min_gpu_memory_mb:
                    continue

            # Check required models
            if job.required_models:
                if not all(m in node.available_models for m in job.required_models):
                    continue

            # Check required tags
            if job.required_tags:
                if not all(t in node.tags for t in job.required_tags):
                    continue

            # Check dtype support
            if job.preferred_dtype not in node.capabilities.supported_dtypes:
                # Fall back to fp32 if available
                if "fp32" not in node.capabilities.supported_dtypes:
                    continue

            eligible.append(node)

        return eligible

    def _select_best_node(
        self,
        nodes: list[NodeInfo],
        job: DistributedJob,
    ) -> NodeInfo | None:
        """Select the best node for a job using weighted scoring.

        Factors:
        - Load (lower is better)
        - Model availability (having required models is better)
        - Weight (higher is preferred)
        - Recent performance
        """
        if not nodes:
            return None

        best_score = float("-inf")
        best_node = None

        for node in nodes:
            score = 0.0

            # Load factor (inverse - lower load is better)
            score += (1.0 - node.load_factor) * 100

            # Weight
            score += node.weight * 10

            # Model affinity - bonus for having required models cached
            if job.required_models:
                cached_models = len(set(job.required_models) & node.available_models)
                score += cached_models * 50

            # Performance history
            if node.avg_generation_time_ms > 0:
                # Faster nodes get higher score (inverse of time)
                score += 1000.0 / node.avg_generation_time_ms

            if score > best_score:
                best_score = score
                best_node = node

        return best_node

    async def _dispatch_job(self, node: NodeInfo, job: DistributedJob) -> bool:
        """Dispatch a job to a specific node."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300)
            ) as session:
                url = f"http://{node.host}:{node.port}/api/generate"

                payload = {
                    "job_id": job.job_id,
                    "workflow": job.workflow,
                    "priority": job.priority,
                }

                async with session.post(url, json=payload) as response:
                    if response.status == 202:  # Accepted
                        return True
                    else:
                        logger.warning(
                            f"Node {node.node_id} rejected job {job.job_id}: {response.status}"
                        )
                        return False

        except Exception as e:
            logger.error(f"Failed to dispatch job {job.job_id} to {node.node_id}: {e}")
            return False

    async def _reassign_jobs(self, failed_node_id: str) -> None:
        """Reassign jobs from a failed node."""
        async with self._lock:
            failed_jobs = [
                j
                for j in self._jobs.values()
                if j.assigned_node == failed_node_id
                and j.status in ("assigned", "running")
            ]

        for job in failed_jobs:
            logger.info(
                f"Reassigning job {job.job_id} from failed node {failed_node_id}"
            )

            job.assigned_node = None
            job.status = "pending"
            job.retry_count += 1

            if job.retry_count >= job.max_retries:
                job.status = "failed"
                job.error = f"Node {failed_node_id} failed, max retries exceeded"
                logger.error(f"Job {job.job_id} failed permanently")

    async def submit_job(self, workflow: dict[str, Any], **kwargs) -> str:
        """Submit a job to the cluster.

        Args:
            workflow: ComfyUI workflow JSON
            **kwargs: Additional job parameters

        Returns:
            Job ID
        """
        job_id = hashlib.sha256(
            f"{time.time()}-{json.dumps(workflow, sort_keys=True)}".encode()
        ).hexdigest()[:16]

        job = DistributedJob(
            job_id=job_id,
            workflow=workflow,
            **kwargs,
        )

        # Store locally
        async with self._lock:
            self._jobs[job_id] = job

        # Add to Redis queue for coordinator
        r = await self._get_redis()
        await r.rpush("cluster:job_queue", json.dumps(asdict(job)))

        logger.info(f"Submitted job {job_id}")
        return job_id

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Get status of a job."""
        async with self._lock:
            job = self._jobs.get(job_id)

        if job:
            return asdict(job)

        # Check Redis
        r = await self._get_redis()
        job_data = await r.hget("cluster:jobs", job_id)

        if job_data:
            return json.loads(job_data)

        return None

    async def get_cluster_status(self) -> dict[str, Any]:
        """Get overall cluster status."""
        nodes = await self._get_available_nodes()

        total_gpus = sum(n.capabilities.gpu_count for n in nodes)
        total_jobs = sum(n.current_jobs for n in nodes)
        total_capacity = sum(n.max_jobs for n in nodes)

        return {
            "coordinator_id": self.node_id,
            "nodes_total": len(nodes),
            "nodes_healthy": sum(1 for n in nodes if n.status == NodeStatus.HEALTHY),
            "nodes_busy": sum(1 for n in nodes if n.status == NodeStatus.BUSY),
            "total_gpus": total_gpus,
            "total_jobs": total_jobs,
            "total_capacity": total_capacity,
            "utilization": total_jobs / max(total_capacity, 1) * 100,
            "nodes": [n.to_dict() for n in nodes],
        }

    async def sync_models(self, model_names: list[str]) -> dict[str, list[str]]:
        """Synchronize models across cluster nodes.

        Returns:
            Dict mapping model names to list of nodes that have it
        """
        r = await self._get_redis()

        # Get model distribution
        distribution = {}

        for model in model_names:
            nodes = await r.smembers(f"cluster:models:{model}")
            distribution[model] = list(nodes)

        return distribution

    async def register_model(self, model_name: str) -> None:
        """Register that this node has a model available."""
        r = await self._get_redis()
        await r.sadd(f"cluster:models:{model_name}", self.node_id)

        self._local_node.available_models.add(model_name)

    async def shutdown(self) -> None:
        """Gracefully shutdown cluster coordinator."""
        self._shutdown_event.set()

        # Cancel background tasks
        for task in [self._heartbeat_task, self._monitor_task, self._scheduler_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Deregister from cluster
        try:
            r = await self._get_redis()
            await r.hdel("cluster:nodes", self.node_id)
            await r.srem("cluster:active", self.node_id)

            # Publish leave event
            await r.publish(
                "cluster:events",
                json.dumps(
                    {
                        "type": "node_leave",
                        "node_id": self.node_id,
                        "timestamp": time.time(),
                    }
                ),
            )
        except Exception:
            pass

        # Close Redis
        if self._redis:
            await self._redis.close()

        logger.info(f"Cluster coordinator {self.node_id} shutdown complete")


class ClusterWorker:
    """Worker node that executes jobs assigned by the coordinator."""

    def __init__(
        self,
        coordinator_url: str,
        local_api_url: str = "http://127.0.0.1:8188",
    ):
        self.coordinator_url = coordinator_url
        self.local_api_url = local_api_url
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._worker_task: asyncio.Task | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300),
            )
        return self._session

    async def start(self) -> None:
        """Start worker and register with coordinator."""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Cluster worker started")

    async def _worker_loop(self) -> None:
        """Main worker loop - poll for jobs and execute."""
        while self._running:
            try:
                # Poll for assigned jobs
                session = await self._get_session()

                async with session.get(
                    f"{self.coordinator_url}/api/worker/jobs",
                    params={"node_id": self._get_node_id()},
                ) as response:
                    if response.status == 200:
                        jobs = await response.json()

                        for job_data in jobs:
                            await self._execute_job(job_data)

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(5)

    async def _execute_job(self, job_data: dict[str, Any]) -> None:
        """Execute a job locally via ComfyUI API."""
        job_id = job_data.get("job_id")
        workflow = job_data.get("workflow")

        try:
            session = await self._get_session()

            # Submit to local ComfyUI
            async with session.post(
                f"{self.local_api_url}/prompt",
                json={"prompt": workflow},
            ) as response:
                if response.status == 200:
                    result = await response.json()

                    # Report success
                    await self._report_result(job_id, "completed", result)
                else:
                    error = await response.text()
                    await self._report_result(job_id, "failed", None, error)

        except Exception as e:
            logger.error(f"Job execution failed: {e}")
            await self._report_result(job_id, "failed", None, str(e))

    async def _report_result(
        self,
        job_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        """Report job result back to coordinator."""
        session = await self._get_session()

        payload = {
            "job_id": job_id,
            "status": status,
            "result": result,
            "error": error,
        }

        try:
            async with session.post(
                f"{self.coordinator_url}/api/jobs/result",
                json=payload,
            ):
                pass
        except Exception as e:
            logger.error(f"Failed to report result: {e}")

    def _get_node_id(self) -> str:
        """Get unique node ID."""
        import socket

        return hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]

    async def shutdown(self) -> None:
        """Shutdown worker."""
        self._running = False

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.close()

        logger.info("Cluster worker shutdown complete")


# Convenience functions
async def create_cluster(
    redis_url: str = "redis://localhost:6379/0",
    is_coordinator: bool = True,
) -> ClusterCoordinator:
    """Factory to create and initialize cluster coordinator."""
    coordinator = ClusterCoordinator(redis_url=redis_url)
    await coordinator.initialize(is_coordinator=is_coordinator)
    return coordinator


async def create_worker(
    coordinator_url: str,
    local_api_url: str = "http://127.0.0.1:8188",
) -> ClusterWorker:
    """Factory to create cluster worker."""
    worker = ClusterWorker(coordinator_url, local_api_url)
    await worker.start()
    return worker


if __name__ == "__main__":

    async def main():
        # Example: Start as coordinator
        cluster = await create_cluster(is_coordinator=True)

        # Print cluster status
        status = await cluster.get_cluster_status()
        print(f"Cluster status: {json.dumps(status, indent=2)}")

        # Submit example job
        job_id = await cluster.submit_job(
            workflow={"test": "workflow"},
            priority=100,
        )
        print(f"Submitted job: {job_id}")

        # Keep running
        await asyncio.sleep(60)

        await cluster.shutdown()

    asyncio.run(main())
