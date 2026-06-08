# Azure AKS Deployment for ComfyUI Engine

Deploys ComfyUI Engine on Azure AKS with GPU node pools, managed addons, and Helm integration.

## Usage

```hcl
module "comfyui_engine_azure" {
  source = "./azure"

  resource_group  = "comfyui-engine"
  cluster_name    = "comfyui-engine"
  location        = "eastus"
  node_count      = 2
  gpu_type        = "nvidia-tesla-t4"
  gpu_count       = 1
  
  enable_monitoring = true
  enable_backup   = true
  
  tags = {
    environment = "production"
    project     = "comfyui-engine"
  }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| resource_group | Azure resource group | string | - | yes |
| cluster_name | AKS cluster name | string | comfyui-engine | no |
| location | Azure region | string | eastus | no |
| node_count | Number of GPU nodes | number | 2 | no |
| gpu_type | GPU type | string | nvidia-tesla-t4 | no |
| gpu_count | GPUs per node | number | 1 | no |
| vm_size | Azure VM size | string | Standard_NC4as_T4_v3 | no |
| enable_monitoring | Enable Prometheus/Grafana | bool | true | no |
| enable_backup | Enable automated backups | bool | true | no |
| tags | Resource tags | map(string) | {} | no |

## Outputs

| Name | Description |
|------|-------------|
| cluster_endpoint | AKS cluster endpoint |
| cluster_name | AKS cluster name |
| kubeconfig | Path to kubeconfig |
| node_pool_name | GPU node pool name |
| monitoring_endpoint | Prometheus/Grafana endpoint |

## GPU VM Sizes

- `Standard_NC4as_T4_v3` - NVIDIA T4 (1 GPU)
- `Standard_NC8as_T4_v3` - NVIDIA T4 (1 GPU)
- `Standard_NC24ads_A100_v4` - NVIDIA A100 (1 GPU)
- `Standard_NC48ads_A100_v4` - NVIDIA A100 (2 GPUs)

## License

MIT License
