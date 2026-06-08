# GCP GKE Deployment for ComfyUI Engine

Deploys ComfyUI Engine on Google GKE with GPU node pools, managed addons, and Helm integration.

## Usage

```hcl
module "comfyui_engine_gcp" {
  source = "./gcp"

  project_id      = "my-project"
  cluster_name    = "comfyui-engine"
  region          = "us-central1"
  node_count      = 2
  gpu_type        = "nvidia-tesla-t4"
  gpu_count       = 1
  
  enable_monitoring = true
  enable_backup   = true
  
  labels = {
    environment = "production"
    project     = "comfyui-engine"
  }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| project_id | GCP project ID | string | - | yes |
| cluster_name | GKE cluster name | string | comfyui-engine | no |
| region | GCP region | string | us-central1 | no |
| zone | GCP zone | string | us-central1-a | no |
| node_count | Number of GPU nodes | number | 2 | no |
| gpu_type | GPU type | string | nvidia-tesla-t4 | no |
| gpu_count | GPUs per node | number | 1 | no |
| machine_type | GCE machine type | string | n1-standard-4 | no |
| enable_monitoring | Enable Prometheus/Grafana | bool | true | no |
| enable_backup | Enable automated backups | bool | true | no |
| labels | Resource labels | map(string) | {} | no |

## Outputs

| Name | Description |
|------|-------------|
| cluster_endpoint | GKE cluster endpoint |
| cluster_name | GKE cluster name |
| kubeconfig | Path to kubeconfig |
| node_pool_name | GPU node pool name |
| monitoring_endpoint | Prometheus/Grafana endpoint |

## GPU Types

- `nvidia-tesla-t4` - NVIDIA T4
- `nvidia-tesla-a100` - NVIDIA A100
- `nvidia-l4` - NVIDIA L4

## License

MIT License
