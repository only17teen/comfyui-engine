# ComfyUI Engine Helm Chart

This Helm chart deploys the ComfyUI Engine on Kubernetes with GPU support, auto-scaling, monitoring, and comprehensive configuration options.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.12+
- NVIDIA GPU Operator (for GPU nodes)
- cert-manager (for TLS)
- NGINX Ingress Controller (for ingress)

## Installation

### Quick Start

```bash
# Add the Helm repository
helm repo add comfyui-engine https://only17teen.github.io/comfyui-engine
helm repo update

# Install with default values
helm install comfyui-engine comfyui-engine/comfyui-engine

# Install with custom values
helm install comfyui-engine comfyui-engine/comfyui-engine -f values-production.yaml
```

### GPU Node Configuration

Ensure your GPU nodes have the proper taints and labels:

```bash
kubectl taint nodes gpu-node nvidia.com/gpu=true:NoSchedule
kubectl label nodes gpu-node accelerator=nvidia-gpu
```

## Configuration

### Key Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Image repository | `ghcr.io/only17teen/comfyui-engine` |
| `image.tag` | Image tag | `Chart appVersion` |
| `resources.limits.nvidia.com/gpu` | GPU limit | `1` |
| `autoscaling.enabled` | Enable HPA | `true` |
| `autoscaling.minReplicas` | Minimum replicas | `1` |
| `autoscaling.maxReplicas` | Maximum replicas | `10` |
| `persistence.models.size` | Model storage size | `100Gi` |
| `persistence.outputs.size` | Output storage size | `50Gi` |
| `gpu.enabled` | Enable GPU support | `true` |
| `gpu.count` | GPU count per pod | `1` |

### GPU Sharing

Enable GPU sharing for multiple pods per GPU:

```yaml
gpu:
  enabled: true
  sharing:
    enabled: true
    strategy: time-slicing
    replicas: 2
```

### Auto-scaling Configuration

```yaml
autoscaling:
  enabled: true
  minReplicas: 1
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
  metrics:
    - type: Pods
      pods:
        metric:
          name: nvidia_gpu_utilization
        target:
          type: AverageValue
          averageValue: "70"
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
    scaleUp:
      stabilizationWindowSeconds: 60
```

### Model Cache Configuration

```yaml
config:
  model_cache:
    max_memory_mb: 8192
    max_models: 10
    warmup_on_start: true
    eviction_policy: "lru_memory"
```

### Security Configuration

```yaml
config:
  security:
    jwt_algorithm: "HS256"
    jwt_expiry: 3600
    rate_limit: 100
    rate_limit_window: 60

secrets:
  jwt:
    enabled: true
    existingSecret: "my-jwt-secret"
    secretKey: "jwt-secret"
```

## Subcharts

### Redis

```yaml
redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: false
  master:
    persistence:
      enabled: true
      size: 8Gi
```

### PostgreSQL

```yaml
postgresql:
  enabled: true
  auth:
    username: comfyui
    database: comfyui_engine
    existingSecret: ""
  primary:
    persistence:
      enabled: true
      size: 20Gi
```

### Prometheus

```yaml
prometheus:
  enabled: true
  server:
    persistentVolume:
      enabled: true
      size: 20Gi
```

### Grafana

```yaml
grafana:
  enabled: true
  admin:
    existingSecret: ""
    userKey: admin-user
    passwordKey: admin-password
  persistence:
    enabled: true
    size: 10Gi
```

## Monitoring

### Prometheus Metrics

The following metrics are exposed:

- `comfyui_engine_requests_total` - Total requests
- `comfyui_engine_request_duration_seconds` - Request duration
- `comfyui_engine_errors_total` - Total errors
- `comfyui_engine_queue_depth` - Queue depth
- `comfyui_engine_gpu_utilization` - GPU utilization
- `comfyui_engine_memory_usage_bytes` - Memory usage

### Grafana Dashboard

A pre-configured dashboard is included with panels for:

- Request rate and latency
- GPU utilization and memory
- Queue depth and processing time
- Error rates and cache hit rates

## Backup

### Automated Backups

```yaml
backup:
  enabled: true
  schedule: "0 2 * * *"
  retention: 7
  storage:
    enabled: true
    provider: s3
    bucket: "comfyui-engine-backups"
    region: "us-east-1"
```

## Upgrading

```bash
# Upgrade to latest version
helm upgrade comfyui-engine comfyui-engine/comfyui-engine

# Upgrade with new values
helm upgrade comfyui-engine comfyui-engine/comfyui-engine -f values-production.yaml

# Rollback if needed
helm rollback comfyui-engine 1
```

## Uninstallation

```bash
helm uninstall comfyui-engine
```

## Troubleshooting

### GPU Not Detected

1. Verify NVIDIA GPU Operator is installed:
   ```bash
   kubectl get pods -n gpu-operator
   ```

2. Check node labels:
   ```bash
   kubectl get nodes --show-labels | grep accelerator
   ```

3. Verify GPU resource allocation:
   ```bash
   kubectl describe node gpu-node | grep nvidia.com/gpu
   ```

### Pod Not Starting

1. Check events:
   ```bash
   kubectl describe pod -l app.kubernetes.io/name=comfyui-engine
   ```

2. Check logs:
   ```bash
   kubectl logs -l app.kubernetes.io/name=comfyui-engine
   ```

3. Verify resource limits:
   ```bash
   kubectl get pod -l app.kubernetes.io/name=comfyui-engine -o jsonpath='{.items[0].spec.containers[0].resources}'
   ```

### Performance Issues

1. Check GPU utilization:
   ```bash
   kubectl exec -it <pod-name> -- nvidia-smi
   ```

2. Monitor metrics:
   ```bash
   kubectl port-forward svc/comfyui-engine-prometheus-server 9090:80
   ```

3. Check cache hit rate:
   ```bash
   curl http://comfyui-engine:8080/metrics | grep comfyui_engine_cache_hit_rate
   ```

## Development

### Local Testing

```bash
# Install with local values
helm install comfyui-engine ./helm/comfyui-engine -f values-development.yaml

# Template rendering
helm template comfyui-engine ./helm/comfyui-engine

# Lint chart
helm lint ./helm/comfyui-engine
```

## License

MIT License - See [LICENSE](../../LICENSE) for details.
