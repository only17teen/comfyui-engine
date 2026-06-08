# ComfyUI Engine Terraform Modules

This directory contains Terraform modules for deploying ComfyUI Engine across multiple cloud providers: AWS, GCP, and Azure.

## Structure

```
terraform/
├── aws/          # AWS EKS deployment
├── gcp/          # GCP GKE deployment
├── azure/        # Azure AKS deployment
└── README.md     # This file
```

## Quick Start

### AWS

```bash
cd aws
terraform init
terraform plan -var="cluster_name=comfyui-engine"
terraform apply
```

### GCP

```bash
cd gcp
terraform init
terraform plan -var="project_id=my-project"
terraform apply
```

### Azure

```bash
cd azure
terraform init
terraform plan -var="resource_group=comfyui-engine"
terraform apply
```

## Common Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `cluster_name` | Kubernetes cluster name | `comfyui-engine` |
| `region` | Cloud region | `us-east-1` (AWS), `us-central1` (GCP), `eastus` (Azure) |
| `node_count` | Number of GPU nodes | `2` |
| `gpu_type` | GPU type | `nvidia-tesla-t4` |
| `gpu_count` | GPUs per node | `1` |
| `enable_monitoring` | Enable Prometheus/Grafana | `true` |
| `enable_backup` | Enable automated backups | `true` |

## Prerequisites

- Terraform 1.5+
- Cloud provider CLI configured
- kubectl configured
- Helm 3.12+

## Outputs

Each module outputs:

- `cluster_endpoint` - Kubernetes API endpoint
- `kubeconfig` - Path to kubeconfig file
- `node_pool_name` - GPU node pool name
- `monitoring_endpoint` - Prometheus/Grafana endpoint (if enabled)

## License

MIT License
