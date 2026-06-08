# AWS EKS Deployment for ComfyUI Engine

Deploys ComfyUI Engine on Amazon EKS with GPU node groups, managed addons, and Helm integration.

## Usage

```hcl
module "comfyui_engine_aws" {
  source = "./aws"

  cluster_name    = "comfyui-engine"
  region          = "us-east-1"
  vpc_cidr        = "10.0.0.0/16"
  node_count      = 2
  gpu_type        = "nvidia-tesla-t4"
  gpu_count       = 1
  
  enable_monitoring = true
  enable_backup   = true
  
  tags = {
    Environment = "production"
    Project     = "comfyui-engine"
  }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| cluster_name | EKS cluster name | string | comfyui-engine | no |
| region | AWS region | string | us-east-1 | no |
| vpc_cidr | VPC CIDR block | string | 10.0.0.0/16 | no |
| availability_zones | AZs for subnets | list(string) | ["a", "b", "c"] | no |
| node_count | Number of GPU nodes | number | 2 | no |
| gpu_type | GPU instance type | string | nvidia-tesla-t4 | no |
| gpu_count | GPUs per node | number | 1 | no |
| instance_type | EC2 instance type | string | g4dn.xlarge | no |
| enable_monitoring | Enable Prometheus/Grafana | bool | true | no |
| enable_backup | Enable automated backups | bool | true | no |
| tags | Resource tags | map(string) | {} | no |

## Outputs

| Name | Description |
|------|-------------|
| cluster_endpoint | EKS cluster endpoint |
| cluster_name | EKS cluster name |
| kubeconfig | Path to kubeconfig |
| node_pool_name | GPU node pool name |
| monitoring_endpoint | Prometheus/Grafana endpoint |

## GPU Instance Types

- `g4dn.xlarge` - NVIDIA T4 (1 GPU)
- `g4dn.2xlarge` - NVIDIA T4 (1 GPU)
- `g5.xlarge` - NVIDIA A10 (1 GPU)
- `p4d.24xlarge` - NVIDIA A100 (8 GPUs)

## License

MIT License
