"""Cloud provider GPU integration for AWS, GCP, and Azure.

Provides unified interface for provisioning GPU instances across
multiple cloud providers with automatic selection, cost optimization,
and lifecycle management.
"""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


class CloudProvider(Enum):
    """Supported cloud providers."""

    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    PAPERSPACE = "paperspace"
    LAMBDA_LABS = "lambda_labs"
    COREWEAVE = "coreweave"


class GPUType(Enum):
    """GPU types available across providers."""

    NVIDIA_T4 = "nvidia-tesla-t4"
    NVIDIA_A10 = "nvidia-a10"
    NVIDIA_A100 = "nvidia-a100"
    NVIDIA_A100_80GB = "nvidia-a100-80gb"
    NVIDIA_H100 = "nvidia-h100"
    NVIDIA_L4 = "nvidia-l4"
    NVIDIA_L40 = "nvidia-l40"
    NVIDIA_RTX_4090 = "nvidia-rtx-4090"
    NVIDIA_RTX_A6000 = "nvidia-rtx-a6000"
    NVIDIA_V100 = "nvidia-v100"


@dataclass
class GPUInstanceSpec:
    """Specification for a GPU instance."""

    provider: CloudProvider
    instance_type: str
    gpu_type: GPUType
    gpu_count: int
    vcpu_count: int
    memory_gb: int
    storage_gb: int
    region: str
    spot: bool = False

    # Pricing (USD per hour)
    on_demand_price: float = 0.0
    spot_price: float | None = None

    @property
    def effective_price(self) -> float:
        """Get effective price based on spot preference."""
        if self.spot and self.spot_price:
            return self.spot_price
        return self.on_demand_price


@dataclass
class ProvisionedInstance:
    """A provisioned GPU instance."""

    instance_id: str
    spec: GPUInstanceSpec
    public_ip: str | None = None
    private_ip: str | None = None
    status: str = "pending"  # pending, running, stopping, stopped, terminated
    ssh_key: str | None = None
    ssh_user: str = "ubuntu"
    launch_time: float = field(default_factory=time.time)
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def uptime_hours(self) -> float:
        if self.status == "running":
            return (time.time() - self.launch_time) / 3600
        return 0.0

    @property
    def current_cost(self) -> float:
        return self.uptime_hours * self.spec.effective_price


class CloudProviderClient(ABC):
    """Abstract base class for cloud provider clients."""

    def __init__(self, credentials: dict[str, str]):
        self.credentials = credentials
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session

    @abstractmethod
    async def list_available_instances(
        self,
        gpu_type: GPUType | None = None,
        gpu_count: int = 1,
        region: str | None = None,
        spot: bool = False,
    ) -> list[GPUInstanceSpec]:
        """List available GPU instance types."""
        pass

    @abstractmethod
    async def provision_instance(
        self,
        spec: GPUInstanceSpec,
        name: str,
        ssh_key: str | None = None,
        startup_script: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> ProvisionedInstance:
        """Provision a new GPU instance."""
        pass

    @abstractmethod
    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate an instance."""
        pass

    @abstractmethod
    async def get_instance_status(self, instance_id: str) -> ProvisionedInstance | None:
        """Get instance status."""
        pass

    @abstractmethod
    async def list_instances(
        self,
        tags: dict[str, str] | None = None,
    ) -> list[ProvisionedInstance]:
        """List all instances."""
        pass

    async def shutdown(self) -> None:
        """Cleanup resources."""
        if self._session and not self._session.closed:
            await self._session.close()


class AWSClient(CloudProviderClient):
    """AWS EC2 GPU instance client."""

    GPU_INSTANCE_TYPES = {
        GPUType.NVIDIA_T4: [
            ("g4dn.xlarge", 1, 4, 16, 125, 0.526),
            ("g4dn.2xlarge", 1, 8, 32, 225, 0.752),
            ("g4dn.4xlarge", 1, 16, 64, 225, 1.204),
            ("g4dn.8xlarge", 1, 32, 128, 900, 2.176),
            ("g4dn.16xlarge", 1, 64, 256, 900, 4.352),
        ],
        GPUType.NVIDIA_A10: [
            ("g5.xlarge", 1, 4, 16, 250, 1.006),
            ("g5.2xlarge", 1, 8, 32, 450, 1.212),
            ("g5.4xlarge", 1, 16, 64, 600, 2.028),
            ("g5.8xlarge", 1, 32, 128, 900, 3.672),
            ("g5.12xlarge", 4, 48, 192, 1024, 5.672),
            ("g5.16xlarge", 1, 64, 256, 1900, 7.344),
            ("g5.24xlarge", 4, 96, 384, 1800, 8.144),
            ("g5.48xlarge", 8, 192, 768, 3800, 16.288),
        ],
        GPUType.NVIDIA_A100: [
            ("p4d.24xlarge", 8, 96, 1152, 8000, 32.77),
        ],
        GPUType.NVIDIA_V100: [
            ("p3.2xlarge", 1, 8, 61, 900, 3.06),
            ("p3.8xlarge", 4, 32, 244, 7200, 12.24),
            ("p3.16xlarge", 8, 64, 488, 14400, 24.48),
        ],
    }

    REGIONS = [
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "eu-west-1",
        "eu-west-2",
        "eu-central-1",
        "ap-southeast-1",
        "ap-northeast-1",
    ]

    def __init__(self, credentials: dict[str, str]):
        super().__init__(credentials)
        self.region = credentials.get("region", "us-east-1")
        self._ec2_client = None

    def _get_ec2_client(self):
        """Get boto3 EC2 client."""
        import boto3

        if self._ec2_client is None:
            self._ec2_client = boto3.client(
                "ec2",
                region_name=self.region,
                aws_access_key_id=self.credentials.get("access_key_id"),
                aws_secret_access_key=self.credentials.get("secret_access_key"),
            )
        return self._ec2_client

    async def list_available_instances(
        self,
        gpu_type: GPUType | None = None,
        gpu_count: int = 1,
        region: str | None = None,
        spot: bool = False,
    ) -> list[GPUInstanceSpec]:
        """List available AWS GPU instances."""
        specs = []

        target_region = region or self.region

        for gtype, instances in self.GPU_INSTANCE_TYPES.items():
            if gpu_type and gtype != gpu_type:
                continue

            for instance_type, gcount, vcpu, memory, storage, price in instances:
                if gcount < gpu_count:
                    continue

                spot_price = price * 0.3 if spot else None

                specs.append(
                    GPUInstanceSpec(
                        provider=CloudProvider.AWS,
                        instance_type=instance_type,
                        gpu_type=gtype,
                        gpu_count=gcount,
                        vcpu_count=vcpu,
                        memory_gb=memory,
                        storage_gb=storage,
                        region=target_region,
                        spot=spot,
                        on_demand_price=price,
                        spot_price=spot_price,
                    )
                )

        return sorted(specs, key=lambda x: x.effective_price)

    async def provision_instance(
        self,
        spec: GPUInstanceSpec,
        name: str,
        ssh_key: str | None = None,
        startup_script: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> ProvisionedInstance:
        """Provision AWS EC2 GPU instance."""
        try:
            import boto3

            ec2 = self._get_ec2_client()

            # Build launch specification
            launch_spec = {
                "ImageId": "ami-0c55b159cbfafe1f0",  # Deep Learning AMI
                "InstanceType": spec.instance_type,
                "MinCount": 1,
                "MaxCount": 1,
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": name},
                            {"Key": "ManagedBy", "Value": "comfyui-engine"},
                            {"Key": "GPUType", "Value": spec.gpu_type.value},
                        ]
                        + [{"Key": k, "Value": v} for k, v in (tags or {}).items()],
                    }
                ],
            }

            if ssh_key:
                launch_spec["KeyName"] = ssh_key

            if startup_script:
                launch_spec["UserData"] = startup_script

            if spec.spot:
                # Request spot instance
                response = ec2.request_spot_instances(
                    InstanceCount=1,
                    LaunchSpecification=launch_spec,
                    SpotPrice=str(spec.on_demand_price * 1.5),
                )

                spot_request_id = response["SpotInstanceRequests"][0][
                    "SpotInstanceRequestId"
                ]

                # Wait for instance to be created
                instance_id = await self._wait_for_spot_instance(spot_request_id)
            else:
                response = ec2.run_instances(**launch_spec)
                instance_id = response["Instances"][0]["InstanceId"]

            # Get instance details
            instance_info = await self.get_instance_status(instance_id)

            if instance_info:
                return instance_info

            return ProvisionedInstance(
                instance_id=instance_id,
                spec=spec,
                status="pending",
                tags=tags or {},
            )

        except ImportError:
            logger.error("boto3 not installed. AWS client unavailable.")
            raise
        except Exception as e:
            logger.error(f"Failed to provision AWS instance: {e}")
            raise

    async def _wait_for_spot_instance(
        self, spot_request_id: str, timeout: int = 300
    ) -> str:
        """Wait for spot instance to be fulfilled."""
        import boto3

        ec2 = self._get_ec2_client()

        start_time = time.time()
        while time.time() - start_time < timeout:
            response = ec2.describe_spot_instance_requests(
                SpotInstanceRequestIds=[spot_request_id]
            )

            request = response["SpotInstanceRequests"][0]
            status = request["Status"]["Code"]

            if status == "fulfilled":
                return request["InstanceId"]
            elif status in [
                "capacity-not-available",
                "capacity-oversubscribed",
                "price-too-low",
            ]:
                raise Exception(f"Spot request failed: {status}")

            await asyncio.sleep(5)

        raise Exception("Timeout waiting for spot instance")

    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate AWS instance."""
        try:
            import boto3

            ec2 = self._get_ec2_client()
            ec2.terminate_instances(InstanceIds=[instance_id])
            return True
        except Exception as e:
            logger.error(f"Failed to terminate instance {instance_id}: {e}")
            return False

    async def get_instance_status(self, instance_id: str) -> ProvisionedInstance | None:
        """Get AWS instance status."""
        try:
            import boto3

            ec2 = self._get_ec2_client()

            response = ec2.describe_instances(InstanceIds=[instance_id])

            if not response["Reservations"]:
                return None

            instance = response["Reservations"][0]["Instances"][0]
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}

            gpu_type_str = tags.get("GPUType", "")
            gpu_type = GPUType(gpu_type_str) if gpu_type_str else GPUType.NVIDIA_T4

            spec = GPUInstanceSpec(
                provider=CloudProvider.AWS,
                instance_type=instance["InstanceType"],
                gpu_type=gpu_type,
                gpu_count=1,  # Would need to look up from instance type
                vcpu_count=0,
                memory_gb=0,
                storage_gb=0,
                region=self.region,
            )

            return ProvisionedInstance(
                instance_id=instance_id,
                spec=spec,
                public_ip=instance.get("PublicIpAddress"),
                private_ip=instance.get("PrivateIpAddress"),
                status=instance["State"]["Name"],
                ssh_user="ubuntu",
                launch_time=instance["LaunchTime"].timestamp(),
                tags=tags,
            )

        except Exception as e:
            logger.error(f"Failed to get instance status: {e}")
            return None

    async def list_instances(
        self,
        tags: dict[str, str] | None = None,
    ) -> list[ProvisionedInstance]:
        """List AWS instances managed by ComfyUI Engine."""
        try:
            import boto3

            ec2 = self._get_ec2_client()

            filters = [
                {"Name": "tag:ManagedBy", "Values": ["comfyui-engine"]},
            ]

            if tags:
                for k, v in tags.items():
                    filters.append({"Name": f"tag:{k}", "Values": [v]})

            response = ec2.describe_instances(Filters=filters)

            instances = []
            for reservation in response["Reservations"]:
                for inst in reservation["Instances"]:
                    instance_info = await self.get_instance_status(inst["InstanceId"])
                    if instance_info:
                        instances.append(instance_info)

            return instances

        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            return []


class GCPClient(CloudProviderClient):
    """Google Cloud Platform GPU client."""

    GPU_INSTANCE_TYPES = {
        GPUType.NVIDIA_T4: [
            ("n1-standard-4", 1, 4, 15, 100, 0.95),
            ("n1-standard-8", 1, 8, 30, 200, 1.52),
            ("n1-standard-16", 2, 16, 60, 400, 3.04),
        ],
        GPUType.NVIDIA_A100: [
            ("a2-highgpu-1g", 1, 12, 85, 1000, 3.67),
            ("a2-highgpu-2g", 2, 24, 170, 2000, 7.34),
            ("a2-highgpu-4g", 4, 48, 340, 4000, 14.68),
            ("a2-highgpu-8g", 8, 96, 680, 8000, 29.36),
            ("a2-megagpu-16g", 16, 192, 1360, 16000, 58.72),
        ],
        GPUType.NVIDIA_L4: [
            ("g2-standard-4", 1, 4, 16, 100, 0.60),
            ("g2-standard-8", 1, 8, 32, 200, 0.78),
            ("g2-standard-16", 1, 16, 64, 400, 1.14),
            ("g2-standard-32", 2, 32, 128, 800, 2.28),
        ],
    }

    REGIONS = [
        "us-central1",
        "us-east1",
        "us-west1",
        "europe-west1",
        "europe-west2",
        "asia-east1",
        "asia-southeast1",
    ]

    def __init__(self, credentials: dict[str, str]):
        super().__init__(credentials)
        self.project = credentials.get("project", "")
        self.zone = credentials.get("zone", "us-central1-a")

    async def list_available_instances(
        self,
        gpu_type: GPUType | None = None,
        gpu_count: int = 1,
        region: str | None = None,
        spot: bool = False,
    ) -> list[GPUInstanceSpec]:
        """List available GCP GPU instances."""
        specs = []

        target_region = region or self.zone.rsplit("-", 1)[0]

        for gtype, instances in self.GPU_INSTANCE_TYPES.items():
            if gpu_type and gtype != gpu_type:
                continue

            for instance_type, gcount, vcpu, memory, storage, price in instances:
                if gcount < gpu_count:
                    continue

                spot_price = price * 0.3 if spot else None

                specs.append(
                    GPUInstanceSpec(
                        provider=CloudProvider.GCP,
                        instance_type=instance_type,
                        gpu_type=gtype,
                        gpu_count=gcount,
                        vcpu_count=vcpu,
                        memory_gb=memory,
                        storage_gb=storage,
                        region=target_region,
                        spot=spot,
                        on_demand_price=price,
                        spot_price=spot_price,
                    )
                )

        return sorted(specs, key=lambda x: x.effective_price)

    async def provision_instance(
        self,
        spec: GPUInstanceSpec,
        name: str,
        ssh_key: str | None = None,
        startup_script: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> ProvisionedInstance:
        """Provision GCP GPU instance."""
        try:
            from google.cloud import compute_v1

            instances_client = compute_v1.InstancesClient()

            instance = compute_v1.Instance()
            instance.name = name
            instance.machine_type = (
                f"zones/{self.zone}/machineTypes/{spec.instance_type}"
            )

            # Add GPU
            accelerator = compute_v1.AcceleratorConfig()
            accelerator.accelerator_count = spec.gpu_count
            accelerator.accelerator_type = (
                f"zones/{self.zone}/acceleratorTypes/{spec.gpu_type.value}"
            )
            instance.guest_accelerators = [accelerator]

            # Boot disk
            disk = compute_v1.AttachedDisk()
            disk.initialize_params = compute_v1.AttachedDiskInitializeParams(
                source_image="projects/deep-learning-platform/global/images/family/common-cu121",
                disk_size_gb=spec.storage_gb,
                disk_type=f"zones/{self.zone}/diskTypes/pd-ssd",
            )
            disk.auto_delete = True
            disk.boot = True
            instance.disks = [disk]

            # Network
            network_interface = compute_v1.NetworkInterface()
            network_interface.network = "global/networks/default"
            network_interface.access_configs = [compute_v1.AccessConfig()]
            instance.network_interfaces = [network_interface]

            # Labels
            labels = {
                "managed-by": "comfyui-engine",
                "gpu-type": spec.gpu_type.value.replace("nvidia-", ""),
            }
            labels.update(
                {
                    k.lower().replace("-", "_")[:63]: v.lower().replace("-", "_")[:63]
                    for k, v in (tags or {}).items()
                }
            )
            instance.labels = labels

            # Spot/preemptible
            if spec.spot:
                instance.scheduling = compute_v1.Scheduling()
                instance.scheduling.provisioning_model = "SPOT"
                instance.scheduling.instance_termination_action = "STOP"

            # Metadata (startup script, SSH keys)
            metadata_items = []
            if startup_script:
                metadata_items.append(
                    compute_v1.Items(key="startup-script", value=startup_script)
                )
            if ssh_key:
                metadata_items.append(
                    compute_v1.Items(key="ssh-keys", value=f"ubuntu:{ssh_key}")
                )

            if metadata_items:
                instance.metadata = compute_v1.Metadata(items=metadata_items)

            operation = instances_client.insert(
                project=self.project,
                zone=self.zone,
                instance_resource=instance,
            )

            return ProvisionedInstance(
                instance_id=name,
                spec=spec,
                status="pending",
                ssh_user="ubuntu",
                tags=tags or {},
            )

        except ImportError:
            logger.error("google-cloud-compute not installed. GCP client unavailable.")
            raise
        except Exception as e:
            logger.error(f"Failed to provision GCP instance: {e}")
            raise

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

    async def get_instance_status(self, instance_id: str) -> ProvisionedInstance | None:
        """Get GCP instance status."""
        try:
            from google.cloud import compute_v1

            instances_client = compute_v1.InstancesClient()
            instance = instances_client.get(
                project=self.project,
                zone=self.zone,
                instance=instance_id,
            )

            gpu_type_str = instance.labels.get("gpu-type", "")
            gpu_type = (
                GPUType(f"nvidia-{gpu_type_str}") if gpu_type_str else GPUType.NVIDIA_T4
            )

            spec = GPUInstanceSpec(
                provider=CloudProvider.GCP,
                instance_type=instance.machine_type.split("/")[-1],
                gpu_type=gpu_type,
                gpu_count=sum(a.accelerator_count for a in instance.guest_accelerators),
                vcpu_count=0,
                memory_gb=0,
                storage_gb=0,
                region=self.zone,
            )

            public_ip = None
            if (
                instance.network_interfaces
                and instance.network_interfaces[0].access_configs
            ):
                public_ip = instance.network_interfaces[0].access_configs[0].nat_i_p

            return ProvisionedInstance(
                instance_id=instance_id,
                spec=spec,
                public_ip=public_ip,
                private_ip=(
                    instance.network_interfaces[0].network_i_p
                    if instance.network_interfaces
                    else None
                ),
                status=instance.status.lower(),
                ssh_user="ubuntu",
                launch_time=(
                    instance.creation_timestamp.timestamp()
                    if hasattr(instance.creation_timestamp, "timestamp")
                    else time.time()
                ),
                tags=dict(instance.labels),
            )

        except Exception as e:
            logger.error(f"Failed to get instance status: {e}")
            return None

    async def list_instances(
        self,
        tags: dict[str, str] | None = None,
    ) -> list[ProvisionedInstance]:
        """List GCP instances managed by ComfyUI Engine."""
        try:
            from google.cloud import compute_v1

            instances_client = compute_v1.InstancesClient()
            response = instances_client.list(project=self.project, zone=self.zone)

            instances = []
            for instance in response:
                if instance.labels.get("managed-by") == "comfyui-engine":
                    instance_info = await self.get_instance_status(instance.name)
                    if instance_info:
                        instances.append(instance_info)

            return instances

        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            return []


class AzureClient(CloudProviderClient):
    """Azure GPU instance client."""

    GPU_INSTANCE_TYPES = {
        GPUType.NVIDIA_T4: [
            ("Standard_NC4as_T4_v3", 1, 4, 28, 180, 0.526),
            ("Standard_NC8as_T4_v3", 1, 8, 56, 360, 0.752),
            ("Standard_NC16as_T4_v3", 1, 16, 110, 720, 1.504),
            ("Standard_NC64as_T4_v3", 4, 64, 440, 2880, 6.016),
        ],
        GPUType.NVIDIA_A100: [
            ("Standard_NC24ads_A100_v4", 1, 24, 220, 900, 3.60),
            ("Standard_NC48ads_A100_v4", 2, 48, 440, 1800, 7.20),
            ("Standard_NC96ads_A100_v4", 4, 96, 880, 3600, 14.40),
        ],
        GPUType.NVIDIA_V100: [
            ("Standard_NC6s_v3", 1, 6, 112, 336, 3.06),
            ("Standard_NC12s_v3", 2, 12, 224, 672, 6.12),
            ("Standard_NC24s_v3", 4, 24, 448, 1344, 12.24),
        ],
    }

    REGIONS = [
        "eastus",
        "westus2",
        "southcentralus",
        "westeurope",
        "northeurope",
        "southeastasia",
        "japaneast",
    ]

    def __init__(self, credentials: dict[str, str]):
        super().__init__(credentials)
        self.subscription_id = credentials.get("subscription_id", "")
        self.resource_group = credentials.get("resource_group", "")
        self.location = credentials.get("location", "eastus")

    async def list_available_instances(
        self,
        gpu_type: GPUType | None = None,
        gpu_count: int = 1,
        region: str | None = None,
        spot: bool = False,
    ) -> list[GPUInstanceSpec]:
        """List available Azure GPU instances."""
        specs = []

        target_region = region or self.location

        for gtype, instances in self.GPU_INSTANCE_TYPES.items():
            if gpu_type and gtype != gpu_type:
                continue

            for instance_type, gcount, vcpu, memory, storage, price in instances:
                if gcount < gpu_count:
                    continue

                spot_price = price * 0.3 if spot else None

                specs.append(
                    GPUInstanceSpec(
                        provider=CloudProvider.AZURE,
                        instance_type=instance_type,
                        gpu_type=gtype,
                        gpu_count=gcount,
                        vcpu_count=vcpu,
                        memory_gb=memory,
                        storage_gb=storage,
                        region=target_region,
                        spot=spot,
                        on_demand_price=price,
                        spot_price=spot_price,
                    )
                )

        return sorted(specs, key=lambda x: x.effective_price)

    async def provision_instance(
        self,
        spec: GPUInstanceSpec,
        name: str,
        ssh_key: str | None = None,
        startup_script: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> ProvisionedInstance:
        """Provision Azure GPU VM."""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient
            from azure.mgmt.network import NetworkManagementClient

            credential = DefaultAzureCredential()
            compute_client = ComputeManagementClient(credential, self.subscription_id)
            network_client = NetworkManagementClient(credential, self.subscription_id)

            # Create or get network resources
            vnet_name = "comfyui-engine-vnet"
            subnet_name = "default"

            # Create VM parameters
            vm_params = {
                "location": spec.region,
                "tags": {
                    "ManagedBy": "comfyui-engine",
                    "GPUType": spec.gpu_type.value,
                    **(tags or {}),
                },
                "hardware_profile": {
                    "vm_size": spec.instance_type,
                },
                "storage_profile": {
                    "image_reference": {
                        "publisher": "microsoft-dsvm",
                        "offer": "ubuntu-2204",
                        "sku": "2204-gen2",
                        "version": "latest",
                    },
                    "os_disk": {
                        "create_option": "FromImage",
                        "disk_size_gb": spec.storage_gb,
                    },
                },
                "os_profile": {
                    "computer_name": name,
                    "admin_username": "ubuntu",
                },
                "network_profile": {
                    "network_interfaces": [],
                },
            }

            if ssh_key:
                vm_params["os_profile"]["linux_configuration"] = {
                    "ssh": {
                        "public_keys": [
                            {
                                "path": "/home/ubuntu/.ssh/authorized_keys",
                                "key_data": ssh_key,
                            }
                        ]
                    },
                    "disable_password_authentication": True,
                }

            if startup_script:
                vm_params["os_profile"]["custom_data"] = startup_script

            # Create VM
            async_vm_creation = compute_client.virtual_machines.begin_create_or_update(
                self.resource_group,
                name,
                vm_params,
            )

            # Wait for completion
            async_vm_creation.result()

            return ProvisionedInstance(
                instance_id=name,
                spec=spec,
                status="pending",
                ssh_user="ubuntu",
                tags=tags or {},
            )

        except ImportError:
            logger.error("azure-mgmt-compute not installed. Azure client unavailable.")
            raise
        except Exception as e:
            logger.error(f"Failed to provision Azure VM: {e}")
            raise

    async def terminate_instance(self, instance_id: str) -> bool:
        """Delete Azure VM."""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient

            credential = DefaultAzureCredential()
            compute_client = ComputeManagementClient(credential, self.subscription_id)

            async_vm_delete = compute_client.virtual_machines.begin_delete(
                self.resource_group,
                instance_id,
            )
            async_vm_delete.result()

            return True
        except Exception as e:
            logger.error(f"Failed to delete VM {instance_id}: {e}")
            return False

    async def get_instance_status(self, instance_id: str) -> ProvisionedInstance | None:
        """Get Azure VM status."""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient

            credential = DefaultAzureCredential()
            compute_client = ComputeManagementClient(credential, self.subscription_id)

            vm = compute_client.virtual_machines.get(self.resource_group, instance_id)

            # Get instance view for status
            instance_view = compute_client.virtual_machines.instance_view(
                self.resource_group,
                instance_id,
            )

            status = "unknown"
            if instance_view.statuses:
                for s in instance_view.statuses:
                    if s.code.startswith("PowerState/"):
                        status = s.code.split("/")[1].lower()

            gpu_type_str = vm.tags.get("GPUType", "")
            gpu_type = GPUType(gpu_type_str) if gpu_type_str else GPUType.NVIDIA_T4

            spec = GPUInstanceSpec(
                provider=CloudProvider.AZURE,
                instance_type=vm.hardware_profile.vm_size,
                gpu_type=gpu_type,
                gpu_count=1,
                vcpu_count=0,
                memory_gb=0,
                storage_gb=0,
                region=vm.location,
            )

            return ProvisionedInstance(
                instance_id=instance_id,
                spec=spec,
                status=status,
                ssh_user="ubuntu",
                tags=dict(vm.tags) if vm.tags else {},
            )

        except Exception as e:
            logger.error(f"Failed to get VM status: {e}")
            return None

    async def list_instances(
        self,
        tags: dict[str, str] | None = None,
    ) -> list[ProvisionedInstance]:
        """List Azure VMs managed by ComfyUI Engine."""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient

            credential = DefaultAzureCredential()
            compute_client = ComputeManagementClient(credential, self.subscription_id)

            vms = compute_client.virtual_machines.list(self.resource_group)

            instances = []
            for vm in vms:
                if vm.tags and vm.tags.get("ManagedBy") == "comfyui-engine":
                    instance_info = await self.get_instance_status(vm.name)
                    if instance_info:
                        instances.append(instance_info)

            return instances

        except Exception as e:
            logger.error(f"Failed to list VMs: {e}")
            return []


class MultiCloudManager:
    """Manages GPU instances across multiple cloud providers.

    Provides unified interface for:
    - Instance provisioning across providers
    - Cost optimization and provider selection
    - Lifecycle management
    - Resource tracking
    """

    def __init__(self):
        self._clients: dict[CloudProvider, CloudProviderClient] = {}
        self._instances: dict[str, ProvisionedInstance] = {}
        self._lock = asyncio.Lock()

    def register_provider(
        self,
        provider: CloudProvider,
        client: CloudProviderClient,
    ) -> None:
        """Register a cloud provider client."""
        self._clients[provider] = client
        logger.info(f"Registered provider: {provider.value}")

    async def find_best_instance(
        self,
        gpu_type: GPUType | None = None,
        gpu_count: int = 1,
        region: str | None = None,
        spot: bool = True,
        max_price: float | None = None,
    ) -> GPUInstanceSpec | None:
        """Find the cheapest available instance across all providers.

        Args:
            gpu_type: Required GPU type
            gpu_count: Minimum GPU count
            region: Preferred region
            spot: Prefer spot instances
            max_price: Maximum price per hour

        Returns:
            Best instance spec or None
        """
        all_specs: list[GPUInstanceSpec] = []

        for provider, client in self._clients.items():
            try:
                specs = await client.list_available_instances(
                    gpu_type=gpu_type,
                    gpu_count=gpu_count,
                    region=region,
                    spot=spot,
                )
                all_specs.extend(specs)
            except Exception as e:
                logger.warning(f"Failed to list instances from {provider.value}: {e}")

        if not all_specs:
            return None

        # Filter by max price
        if max_price:
            all_specs = [s for s in all_specs if s.effective_price <= max_price]

        if not all_specs:
            return None

        # Sort by effective price
        return min(all_specs, key=lambda x: x.effective_price)

    async def provision_best_instance(
        self,
        name: str,
        gpu_type: GPUType | None = None,
        gpu_count: int = 1,
        region: str | None = None,
        spot: bool = True,
        max_price: float | None = None,
        ssh_key: str | None = None,
        startup_script: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> ProvisionedInstance | None:
        """Provision the best available instance across all providers.

        Automatically selects the cheapest option that meets requirements.
        """
        best_spec = await self.find_best_instance(
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            region=region,
            spot=spot,
            max_price=max_price,
        )

        if not best_spec:
            logger.error("No suitable instance found across all providers")
            return None

        provider = best_spec.provider
        client = self._clients.get(provider)

        if not client:
            logger.error(f"Provider {provider.value} not registered")
            return None

        logger.info(
            f"Provisioning {best_spec.instance_type} from {provider.value} "
            f"at ${best_spec.effective_price:.3f}/hour"
        )

        try:
            instance = await client.provision_instance(
                spec=best_spec,
                name=name,
                ssh_key=ssh_key,
                startup_script=startup_script,
                tags=tags,
            )

            async with self._lock:
                self._instances[instance.instance_id] = instance

            return instance

        except Exception as e:
            logger.error(f"Failed to provision instance: {e}")
            return None

    async def terminate_instance(self, instance_id: str) -> bool:
        """Terminate an instance by ID."""
        instance = self._instances.get(instance_id)
        if not instance:
            logger.error(f"Instance {instance_id} not found")
            return False

        provider = instance.spec.provider
        client = self._clients.get(provider)

        if not client:
            logger.error(f"Provider {provider.value} not registered")
            return False

        success = await client.terminate_instance(instance_id)

        if success:
            async with self._lock:
                if instance_id in self._instances:
                    del self._instances[instance_id]

        return success

    async def get_all_instances(self) -> list[ProvisionedInstance]:
        """Get all managed instances across all providers."""
        instances = []

        for provider, client in self._clients.items():
            try:
                provider_instances = await client.list_instances()
                instances.extend(provider_instances)
            except Exception as e:
                logger.warning(f"Failed to list instances from {provider.value}: {e}")

        return instances

    async def get_total_cost(self) -> float:
        """Get total current hourly cost of all running instances."""
        instances = await self.get_all_instances()
        return sum(inst.spec.effective_price for inst in instances if inst.is_running)

    async def shutdown(self) -> None:
        """Shutdown all provider clients."""
        for client in self._clients.values():
            await client.shutdown()


# Convenience factory functions
async def create_aws_client(
    access_key_id: str,
    secret_access_key: str,
    region: str = "us-east-1",
) -> AWSClient:
    """Create AWS client."""
    return AWSClient(
        {
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "region": region,
        }
    )


async def create_gcp_client(
    project: str,
    zone: str = "us-central1-a",
    credentials_path: str | None = None,
) -> GCPClient:
    """Create GCP client."""
    return GCPClient(
        {
            "project": project,
            "zone": zone,
            "credentials_path": credentials_path,
        }
    )


async def create_azure_client(
    subscription_id: str,
    resource_group: str,
    location: str = "eastus",
) -> AzureClient:
    """Create Azure client."""
    return AzureClient(
        {
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "location": location,
        }
    )


async def create_multicloud_manager(
    providers: dict[CloudProvider, dict[str, str]],
) -> MultiCloudManager:
    """Create multi-cloud manager with configured providers.

    Args:
        providers: Dict mapping provider to credentials dict

    Returns:
        Configured MultiCloudManager
    """
    manager = MultiCloudManager()

    for provider, creds in providers.items():
        try:
            if provider == CloudProvider.AWS:
                client = AWSClient(creds)
            elif provider == CloudProvider.GCP:
                client = GCPClient(creds)
            elif provider == CloudProvider.AZURE:
                client = AzureClient(creds)
            else:
                logger.warning(f"Unsupported provider: {provider.value}")
                continue

            manager.register_provider(provider, client)

        except Exception as e:
            logger.error(f"Failed to initialize {provider.value}: {e}")

    return manager


if __name__ == "__main__":

    async def main():
        # Example: Create multi-cloud manager
        manager = await create_multicloud_manager(
            {
                CloudProvider.AWS: {
                    "access_key_id": "your-access-key",
                    "secret_access_key": "your-secret-key",
                    "region": "us-east-1",
                },
            }
        )

        # Find cheapest instance
        best = await manager.find_best_instance(
            gpu_type=GPUType.NVIDIA_T4,
            gpu_count=1,
            spot=True,
        )

        if best:
            print(
                f"Best instance: {best.instance_type} at ${best.effective_price:.3f}/hour"
            )

        await manager.shutdown()

    asyncio.run(main())
