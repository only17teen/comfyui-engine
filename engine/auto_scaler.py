"""Auto-scaling controller for GPU cluster.

Monitors cluster load and automatically scales GPU nodes up or down
based on queue depth, job wait times, and cost optimization.
Supports multiple cloud providers with pluggable backends.
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


class ScalingAction(Enum):
    """Possible scaling actions."""

    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    MAINTAIN = "maintain"
    EMERGENCY_SCALE_UP = "emergency_scale_up"


@dataclass
class ScalingPolicy:
    """Policy for auto-scaling decisions."""

    # Scale up triggers
    scale_up_queue_depth: int = 10
    scale_up_wait_time_sec: float = 60.0
    scale_up_cpu_threshold: float = 80.0

    # Scale down triggers
    scale_down_idle_time_sec: float = 300.0
    scale_down_utilization_threshold: float = 20.0
    min_nodes: int = 1
    max_nodes: int = 10

    # Cooldown periods
    scale_up_cooldown_sec: float = 120.0
    scale_down_cooldown_sec: float = 600.0

    # Cost optimization
    max_cost_per_hour: float = 100.0  # USD
    prefer_spot_instances: bool = True
    spot_instance_discount: float = 0.7  # 70% of on-demand price


@dataclass
class NodeInstance:
    """Represents a cloud GPU instance."""

    instance_id: str
    provider: str  # aws, gcp, azure, local
    instance_type: str
    gpu_type: str
    gpu_count: int
    cost_per_hour: float
    is_spot: bool = False
    status: str = "running"  # running, pending, terminating, terminated
    launched_at: float = field(default_factory=time.time)
    last_job_at: float | None = None
    total_jobs: int = 0
    zone: str = ""
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class ScalingDecision:
    """Decision made by the auto-scaler."""

    action: ScalingAction
    reason: str
    target_nodes: int
    current_nodes: int
    queue_depth: int
    avg_wait_time: float
    estimated_cost: float
    timestamp: float = field(default_factory=time.time)


class CloudProviderBase:
    """Base class for cloud provider integrations."""

    async def launch_instance(
        self,
        instance_type: str,
        gpu_count: int,
        is_spot: bool = False,
        zone: str = "",
        tags: dict[str, str] = None,
    ) -> NodeInstance | None:
        """Launch a new GPU instance."""
        raise NotImplementedError

    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate an instance."""
        raise NotImplementedError

    async def get_instance_status(self, instance_id: str) -> dict[str, Any] | None:
        """Get instance status."""
        raise NotImplementedError

    async def list_instances(self, tags: dict[str, str] = None) -> list[NodeInstance]:
        """List all managed instances."""
        raise NotImplementedError

    async def get_pricing(self, instance_type: str, is_spot: bool = False) -> float:
        """Get hourly cost for instance type."""
        raise NotImplementedError


class AWSProvider(CloudProviderBase):
    """AWS EC2 GPU instance provider."""

    INSTANCE_TYPES = {
        "g4dn.xlarge": {"gpu": 1, "cost": 0.526},
        "g4dn.2xlarge": {"gpu": 1, "cost": 0.752},
        "g4dn.4xlarge": {"gpu": 1, "cost": 1.204},
        "g4dn.8xlarge": {"gpu": 1, "cost": 2.176},
        "g4dn.16xlarge": {"gpu": 1, "cost": 4.352},
        "g5.xlarge": {"gpu": 1, "cost": 1.006},
        "g5.2xlarge": {"gpu": 1, "cost": 1.212},
        "g5.4xlarge": {"gpu": 1, "cost": 2.028},
        "g5.8xlarge": {"gpu": 1, "cost": 3.672},
        "p3.2xlarge": {"gpu": 1, "cost": 3.06},
        "p3.8xlarge": {"gpu": 4, "cost": 12.24},
        "p3.16xlarge": {"gpu": 8, "cost": 24.48},
        "p4d.24xlarge": {"gpu": 8, "cost": 32.77},
    }

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self._session: Any | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if not hasattr(self, "_http_session") or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._http_session

    async def launch_instance(
        self,
        instance_type: str,
        gpu_count: int,
        is_spot: bool = False,
        zone: str = "",
        tags: dict[str, str] = None,
    ) -> NodeInstance | None:
        """Launch EC2 GPU instance."""
        try:
            import boto3

            ec2 = boto3.client("ec2", region_name=self.region)

            # Build launch specification
            launch_spec = {
                "ImageId": "ami-0c55b159cbfafe1f0",  # Deep Learning AMI
                "InstanceType": instance_type,
                "MinCount": 1,
                "MaxCount": 1,
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": k, "Value": v} for k, v in (tags or {}).items()]
                        + [
                            {"Key": "Name", "Value": "comfyui-engine-worker"},
                            {"Key": "ManagedBy", "Value": "comfyui-engine"},
                        ],
                    }
                ],
            }

            if is_spot:
                # Request spot instance
                response = ec2.request_spot_instances(
                    InstanceCount=1,
                    LaunchSpecification=launch_spec,
                    SpotPrice=str(self.INSTANCE_TYPES.get(instance_type, {}).get("cost", 1.0) * 1.5),
                )
                instance_id = response["SpotInstanceRequests"][0]["InstanceId"]
            else:
                response = ec2.run_instances(**launch_spec)
                instance_id = response["Instances"][0]["InstanceId"]

            info = self.INSTANCE_TYPES.get(instance_type, {})

            return NodeInstance(
                instance_id=instance_id,
                provider="aws",
                instance_type=instance_type,
                gpu_type="nvidia",
                gpu_count=info.get("gpu", 1),
                cost_per_hour=info.get("cost", 1.0) * (0.7 if is_spot else 1.0),
                is_spot=is_spot,
                status="pending",
                zone=zone or self.region,
                tags=tags or {},
            )

        except ImportError:
            logger.error("boto3 not installed. AWS provider unavailable.")
            return None
        except Exception as e:
            logger.error(f"Failed to launch AWS instance: {e}")
            return None

    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate EC2 instance."""
        try:
            import boto3

            ec2 = boto3.client("ec2", region_name=self.region)
            ec2.terminate_instances(InstanceIds=[instance_id])
            return True
        except Exception as e:
            logger.error(f"Failed to terminate instance {instance_id}: {e}")
            return False

    async def get_instance_status(self, instance_id: str) -> dict[str, Any] | None:
        """Get EC2 instance status."""
        try:
            import boto3

            ec2 = boto3.client("ec2", region_name=self.region)
            response = ec2.describe_instances(InstanceIds=[instance_id])

            instance = response["Reservations"][0]["Instances"][0]
            return {
                "status": instance["State"]["Name"],
                "public_ip": instance.get("PublicIpAddress"),
                "private_ip": instance.get("PrivateIpAddress"),
                "launch_time": instance["LaunchTime"].isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to get instance status: {e}")
            return None

    async def list_instances(self, tags: dict[str, str] = None) -> list[NodeInstance]:
        """List managed EC2 instances."""
        try:
            import boto3

            ec2 = boto3.client("ec2", region_name=self.region)

            filters = [
                {"Name": "tag:ManagedBy", "Values": ["comfyui-engine"]},
                {"Name": "instance-state-name", "Values": ["running", "pending"]},
            ]

            response = ec2.describe_instances(Filters=filters)

            instances = []
            for reservation in response["Reservations"]:
                for inst in reservation["Instances"]:
                    tags_dict = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}

                    instance_type = inst["InstanceType"]
                    info = self.INSTANCE_TYPES.get(instance_type, {})

                    instances.append(
                        NodeInstance(
                            instance_id=inst["InstanceId"],
                            provider="aws",
                            instance_type=instance_type,
                            gpu_type="nvidia",
                            gpu_count=info.get("gpu", 1),
                            cost_per_hour=info.get("cost", 1.0),
                            is_spot="spot" in tags_dict.get("Lifecycle", ""),
                            status=inst["State"]["Name"],
                            launched_at=inst["LaunchTime"].timestamp(),
                            zone=inst["Placement"]["AvailabilityZone"],
                            tags=tags_dict,
                        )
                    )

            return instances

        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            return []

    async def get_pricing(self, instance_type: str, is_spot: bool = False) -> float:
        """Get hourly cost for instance type."""
        base_cost = self.INSTANCE_TYPES.get(instance_type, {}).get("cost", 1.0)
        return base_cost * (0.7 if is_spot else 1.0)


class GCPProvider(CloudProviderBase):
    """Google Cloud Platform GPU provider."""

    INSTANCE_TYPES = {
        "n1-standard-4": {"gpu": 1, "cost": 0.95},
        "n1-standard-8": {"gpu": 1, "cost": 1.52},
        "n1-standard-16": {"gpu": 2, "cost": 3.04},
        "n1-standard-32": {"gpu": 4, "cost": 6.08},
        "a2-highgpu-1g": {"gpu": 1, "cost": 3.67},
        "a2-highgpu-2g": {"gpu": 2, "cost": 7.34},
        "a2-highgpu-4g": {"gpu": 4, "cost": 14.68},
        "a2-highgpu-8g": {"gpu": 8, "cost": 29.36},
        "a2-megagpu-16g": {"gpu": 16, "cost": 58.72},
    }

    def __init__(self, project: str = "", zone: str = "us-central1-a"):
        self.project = project
        self.zone = zone

    async def launch_instance(
        self,
        instance_type: str,
        gpu_count: int,
        is_spot: bool = False,
        zone: str = "",
        tags: dict[str, str] = None,
    ) -> NodeInstance | None:
        """Launch GCP GPU instance."""
        try:
            from google.cloud import compute_v1

            instances_client = compute_v1.InstancesClient()

            instance = compute_v1.Instance()
            instance.name = f"comfyui-engine-{int(time.time())}"
            instance.machine_type = f"zones/{zone or self.zone}/machineTypes/{instance_type}"

            # Add GPU
            guest_accelerator = compute_v1.AcceleratorConfig()
            guest_accelerator.accelerator_count = gpu_count
            guest_accelerator.accelerator_type = f"zones/{zone or self.zone}/acceleratorTypes/nvidia-tesla-t4"
            instance.guest_accelerators = [guest_accelerator]

            # Spot/preemptible
            if is_spot:
                instance.scheduling = compute_v1.Scheduling()
                instance.scheduling.preemptible = True

            # Labels
            labels = {"managed-by": "comfyui-engine"}
            labels.update(
                {k.lower().replace("-", "_"): v.lower().replace("-", "_")[:63] for k, v in (tags or {}).items()}
            )
            instance.labels = labels

            operation = instances_client.insert(
                project=self.project,
                zone=zone or self.zone,
                instance_resource=instance,
            )

            info = self.INSTANCE_TYPES.get(instance_type, {})

            return NodeInstance(
                instance_id=instance.name,
                provider="gcp",
                instance_type=instance_type,
                gpu_type="nvidia-tesla-t4",
                gpu_count=gpu_count,
                cost_per_hour=info.get("cost", 1.0) * (0.7 if is_spot else 1.0),
                is_spot=is_spot,
                status="pending",
                zone=zone or self.zone,
                tags=tags or {},
            )

        except ImportError:
            logger.error("google-cloud-compute not installed. GCP provider unavailable.")
            return None
        except Exception as e:
            logger.error(f"Failed to launch GCP instance: {e}")
            return None

    async def terminate_instance(self, instance_id: str) -> bool:
        """Delete GCP instance."""
        try:
            from google.cloud import compute_v1

            instances_client = compute_v1.InstancesClient()
            operation = instances_client.delete(
                project=self.project,
                zone=self.zone,
                instance=instance_id,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete instance {instance_id}: {e}")
            return False

    async def get_pricing(self, instance_type: str, is_spot: bool = False) -> float:
        """Get hourly cost for instance type."""
        base_cost = self.INSTANCE_TYPES.get(instance_type, {}).get("cost", 1.0)
        return base_cost * (0.7 if is_spot else 1.0)


class AutoScaler:
    """Auto-scaling controller for GPU cluster.

    Monitors cluster metrics and automatically scales nodes
    based on configurable policies and cost optimization.
    """

    def __init__(
        self,
        provider: CloudProviderBase,
        policy: ScalingPolicy | None = None,
        cluster_coordinator: Any | None = None,
        check_interval: float = 30.0,
    ):
        self.provider = provider
        self.policy = policy or ScalingPolicy()
        self.cluster_coordinator = cluster_coordinator
        self.check_interval = check_interval

        self._instances: dict[str, NodeInstance] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._monitor_task: asyncio.Task | None = None
        self._last_scale_up: float = 0
        self._last_scale_down: float = 0
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start auto-scaling monitor."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Auto-scaler started")

    async def _monitor_loop(self) -> None:
        """Main monitoring and scaling loop."""
        while self._running and not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.check_interval,
                )
                break
            except asyncio.TimeoutError:
                await self._evaluate_scaling()

    async def _evaluate_scaling(self) -> None:
        """Evaluate cluster state and make scaling decisions."""
        # Get current cluster metrics
        metrics = await self._get_cluster_metrics()

        if not metrics:
            return

        queue_depth = metrics.get("queue_depth", 0)
        avg_wait_time = metrics.get("avg_wait_time", 0)
        current_nodes = metrics.get("active_nodes", 0)
        total_jobs = metrics.get("total_jobs", 0)

        # Calculate current cost
        current_cost = await self._calculate_current_cost()

        # Make decision
        decision = self._make_decision(
            queue_depth=queue_depth,
            avg_wait_time=avg_wait_time,
            current_nodes=current_nodes,
            current_cost=current_cost,
        )

        logger.info(
            f"Scaling decision: {decision.action.value} - {decision.reason} "
            f"(nodes: {current_nodes} -> {decision.target_nodes})"
        )

        # Execute decision
        if decision.action == ScalingAction.SCALE_UP:
            await self._scale_up(decision)
        elif decision.action == ScalingAction.EMERGENCY_SCALE_UP:
            await self._emergency_scale_up(decision)
        elif decision.action == ScalingAction.SCALE_DOWN:
            await self._scale_down(decision)

    async def _get_cluster_metrics(self) -> dict[str, Any] | None:
        """Get metrics from cluster coordinator."""
        if self.cluster_coordinator and hasattr(self.cluster_coordinator, "get_cluster_status"):
            try:
                status = await self.cluster_coordinator.get_cluster_status()
                return {
                    "queue_depth": status.get("queue_length", 0),
                    "avg_wait_time": status.get("avg_wait_time", 0),
                    "active_nodes": status.get("nodes_total", 0),
                    "total_jobs": status.get("total_jobs", 0),
                    "utilization": status.get("utilization", 0),
                }
            except Exception as e:
                logger.warning(f"Failed to get cluster metrics: {e}")

        return None

    async def _calculate_current_cost(self) -> float:
        """Calculate current hourly cost of running instances."""
        async with self._lock:
            return sum(inst.cost_per_hour for inst in self._instances.values() if inst.status == "running")

    def _make_decision(
        self,
        queue_depth: int,
        avg_wait_time: float,
        current_nodes: int,
        current_cost: float,
    ) -> ScalingDecision:
        """Make scaling decision based on metrics and policy."""
        now = time.time()

        # Check cooldowns
        scale_up_cooled = (now - self._last_scale_up) > self.policy.scale_up_cooldown_sec
        scale_down_cooled = (now - self._last_scale_down) > self.policy.scale_down_cooldown_sec

        # Emergency scale up
        if queue_depth > self.policy.scale_up_queue_depth * 3:
            if scale_up_cooled and current_nodes < self.policy.max_nodes:
                return ScalingDecision(
                    action=ScalingAction.EMERGENCY_SCALE_UP,
                    reason=f"Emergency: queue depth {queue_depth} exceeds 3x threshold",
                    target_nodes=min(current_nodes + 3, self.policy.max_nodes),
                    current_nodes=current_nodes,
                    queue_depth=queue_depth,
                    avg_wait_time=avg_wait_time,
                    estimated_cost=current_cost * 1.5,
                )

        # Normal scale up
        if queue_depth >= self.policy.scale_up_queue_depth:
            if scale_up_cooled and current_nodes < self.policy.max_nodes:
                return ScalingDecision(
                    action=ScalingAction.SCALE_UP,
                    reason=f"Queue depth {queue_depth} exceeds threshold {self.policy.scale_up_queue_depth}",
                    target_nodes=min(current_nodes + 1, self.policy.max_nodes),
                    current_nodes=current_nodes,
                    queue_depth=queue_depth,
                    avg_wait_time=avg_wait_time,
                    estimated_cost=current_cost * 1.2,
                )

        if avg_wait_time >= self.policy.scale_up_wait_time_sec:
            if scale_up_cooled and current_nodes < self.policy.max_nodes:
                return ScalingDecision(
                    action=ScalingAction.SCALE_UP,
                    reason=f"Wait time {avg_wait_time:.1f}s exceeds threshold {self.policy.scale_up_wait_time_sec}s",
                    target_nodes=min(current_nodes + 1, self.policy.max_nodes),
                    current_nodes=current_nodes,
                    queue_depth=queue_depth,
                    avg_wait_time=avg_wait_time,
                    estimated_cost=current_cost * 1.2,
                )

        # Scale down
        if queue_depth == 0 and current_nodes > self.policy.min_nodes:
            if scale_down_cooled:
                # Check if nodes have been idle
                idle_nodes = self._count_idle_nodes()
                if idle_nodes > 0:
                    return ScalingDecision(
                        action=ScalingAction.SCALE_DOWN,
                        reason=f"Queue empty, {idle_nodes} nodes idle",
                        target_nodes=max(current_nodes - 1, self.policy.min_nodes),
                        current_nodes=current_nodes,
                        queue_depth=queue_depth,
                        avg_wait_time=avg_wait_time,
                        estimated_cost=current_cost * 0.8,
                    )

        # Maintain
        return ScalingDecision(
            action=ScalingAction.MAINTAIN,
            reason="No scaling needed",
            target_nodes=current_nodes,
            current_nodes=current_nodes,
            queue_depth=queue_depth,
            avg_wait_time=avg_wait_time,
            estimated_cost=current_cost,
        )

    def _count_idle_nodes(self) -> int:
        """Count nodes that have been idle for longer than threshold."""
        now = time.time()
        idle_count = 0

        for inst in self._instances.values():
            if inst.status == "running":
                last_job = inst.last_job_at or inst.launched_at
                if (now - last_job) > self.policy.scale_down_idle_time_sec:
                    idle_count += 1

        return idle_count

    async def _scale_up(self, decision: ScalingDecision) -> None:
        """Scale up by launching new instances."""
        nodes_to_add = decision.target_nodes - decision.current_nodes

        for _ in range(nodes_to_add):
            # Select instance type based on policy
            instance_type = self._select_instance_type()
            is_spot = self.policy.prefer_spot_instances

            instance = await self.provider.launch_instance(
                instance_type=instance_type,
                gpu_count=1,
                is_spot=is_spot,
                tags={"purpose": "comfyui-engine-worker"},
            )

            if instance:
                async with self._lock:
                    self._instances[instance.instance_id] = instance

                logger.info(f"Launched instance: {instance.instance_id} ({instance.instance_type})")

        self._last_scale_up = time.time()

    async def _emergency_scale_up(self, decision: ScalingDecision) -> None:
        """Emergency scale up with multiple instances."""
        logger.warning("Emergency scale up triggered!")

        # Launch multiple instances at once
        nodes_to_add = decision.target_nodes - decision.current_nodes

        tasks = []
        for _ in range(nodes_to_add):
            instance_type = self._select_instance_type()
            tasks.append(
                self.provider.launch_instance(
                    instance_type=instance_type,
                    gpu_count=1,
                    is_spot=False,  # On-demand for emergency
                    tags={"purpose": "comfyui-engine-worker", "emergency": "true"},
                )
            )

        instances = await asyncio.gather(*tasks, return_exceptions=True)

        for instance in instances:
            if isinstance(instance, NodeInstance):
                async with self._lock:
                    self._instances[instance.instance_id] = instance
                logger.info(f"Emergency launch: {instance.instance_id}")

        self._last_scale_up = time.time()

    async def _scale_down(self, decision: ScalingDecision) -> None:
        """Scale down by terminating idle instances."""
        nodes_to_remove = decision.current_nodes - decision.target_nodes

        # Find oldest idle instances
        idle_instances = sorted(
            [inst for inst in self._instances.values() if inst.status == "running"],
            key=lambda x: x.last_job_at or x.launched_at,
        )

        for inst in idle_instances[:nodes_to_remove]:
            success = await self.provider.terminate_instance(inst.instance_id)

            if success:
                async with self._lock:
                    inst.status = "terminating"

                logger.info(f"Terminating instance: {inst.instance_id}")

        self._last_scale_down = time.time()

    def _select_instance_type(self) -> str:
        """Select best instance type based on cost and availability."""
        if isinstance(self.provider, AWSProvider):
            # Prefer cost-effective GPU instances
            candidates = [
                ("g4dn.xlarge", 0.526),
                ("g5.xlarge", 1.006),
                ("g4dn.2xlarge", 0.752),
            ]
        elif isinstance(self.provider, GCPProvider):
            candidates = [
                ("n1-standard-4", 0.95),
                ("n1-standard-8", 1.52),
            ]
        else:
            candidates = [("default", 1.0)]

        # Sort by cost
        candidates.sort(key=lambda x: x[1])

        return candidates[0][0]

    async def get_status(self) -> dict[str, Any]:
        """Get auto-scaler status."""
        async with self._lock:
            instances = list(self._instances.values())

        running = sum(1 for i in instances if i.status == "running")
        pending = sum(1 for i in instances if i.status == "pending")
        terminating = sum(1 for i in instances if i.status == "terminating")

        total_cost = sum(i.cost_per_hour for i in instances if i.status == "running")

        return {
            "running_instances": running,
            "pending_instances": pending,
            "terminating_instances": terminating,
            "total_instances": len(instances),
            "total_cost_per_hour": total_cost,
            "last_scale_up": self._last_scale_up,
            "last_scale_down": self._last_scale_down,
            "policy": asdict(self.policy),
            "instances": [asdict(i) for i in instances],
        }

    async def shutdown(self) -> None:
        """Shutdown auto-scaler and cleanup instances."""
        self._running = False
        self._shutdown_event.set()

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Terminate all managed instances
        async with self._lock:
            instances = list(self._instances.values())

        for inst in instances:
            if inst.status in ("running", "pending"):
                logger.info(f"Terminating instance on shutdown: {inst.instance_id}")
                await self.provider.terminate_instance(inst.instance_id)

        logger.info("Auto-scaler shutdown complete")


# Convenience factory functions
async def create_aws_autoscaler(
    region: str = "us-east-1",
    policy: ScalingPolicy | None = None,
    cluster_coordinator: Any | None = None,
) -> AutoScaler:
    """Create AWS auto-scaler."""
    provider = AWSProvider(region=region)
    return AutoScaler(provider=provider, policy=policy, cluster_coordinator=cluster_coordinator)


async def create_gcp_autoscaler(
    project: str = "",
    zone: str = "us-central1-a",
    policy: ScalingPolicy | None = None,
    cluster_coordinator: Any | None = None,
) -> AutoScaler:
    """Create GCP auto-scaler."""
    provider = GCPProvider(project=project, zone=zone)
    return AutoScaler(provider=provider, policy=policy, cluster_coordinator=cluster_coordinator)


if __name__ == "__main__":

    async def main():
        # Example: Create and start auto-scaler
        scaler = await create_aws_autoscaler()
        await scaler.start()

        # Print status
        status = await scaler.get_status()
        print(f"Auto-scaler status: {json.dumps(status, indent=2)}")

        # Keep running
        await asyncio.sleep(60)

        await scaler.shutdown()

    asyncio.run(main())
