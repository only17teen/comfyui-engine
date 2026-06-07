# ComfyUI Async Generation Engine v5.0

Production-grade async pipeline for AI model generation via ComfyUI API.
Built for Arch Linux, Python 3.11+, with enterprise resilience patterns,
cloud-native orchestration, and comprehensive observability.

## Quick Start

```bash
# 1. Clone and install
git clone <repo>
cd comfyui-engine
make install

# 2. Configure prompts
vim config/prompts.yaml

# 3. Export workflow from ComfyUI (Save API format)
cp your_workflow.json workflows/standard.json

# 4. Run
make run

# Or manually:
python -m main --batch 8 --workflow workflows/standard.json --verbose
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ComfyUI Async Generation Engine v5.0                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────┐ │
│  │   Config    │  │   Prompt    │  │      API Client     │  │   Tracing   │ │
│  │  (Pydantic) │  │  (Templates)│  │ (Circuit Breaker +  │  │(OpenTel/Ja│ │
│  │             │  │  (Seeds)    │  │  Retry + WebSocket) │  │  ger)       │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  └─────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────┐ │
│  │   Session   │  │ Checkpoint  │  │   Output Handler    │  │  WebSocket  │ │
│  │  Manager    │  │  / Resume   │  │ (Downloads + Meta)  │  │  Streaming  │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  └─────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────┐ │
│  │   Metrics   │  │  Webhook    │  │   A/B Testing       │  │  Redis Cache│ │
│  │  Server     │  │ Notifications│  │   Framework         │  │  Manager    │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  └─────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  ┌─────────────┐ │
│  │  Kubernetes │  │    Helm     │  │   Terraform         │  │  Load Tests │ │
│  │  Manifests  │  │   Charts    │  │   Modules           │  │  (k6)       │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

## All Features

### Core Engine
- **Asyncio + aiohttp** — Parallel GPU job submission
- **Circuit Breaker** — Prevents cascading failures
- **Exponential Backoff Retry** — With jitter
- **Priority Queue** — Backpressure and rate limiting
- **WebSocket Manager** — Auto-reconnection, heartbeat
- **Metrics Server** — Prometheus-compatible HTTP endpoint
- **Graceful Shutdown** — SIGTERM/SIGINT handling with connection draining

### Configuration
- **Pydantic Validation** — Type-safe, fail-fast
- **Environment Override** — 20+ env vars
- **YAML Merging** — Base + overrides

### Prompt Engineering
- **5 Templates** — standard, portrait, cinematic, fashion, full_body
- **4 Seed Strategies** — random, time_based, sequential, fixed
- **Weighted Random** — Quality tags prioritized
- **Deduplication** — History tracking

### Resilience
- **Session Manager** — Crash recovery
- **Checkpoint/Resume** — Emergency on SIGTERM
- **Distributed Queue** — Redis-backed multi-GPU
- **Workflow Validator** — Auto node mapping
- **Dead Letter Queue** — Failed job handling
- **Profiler** — Performance analysis

### Observability
- **Structured JSON Logging** — Loki/Vector integration
- **Prometheus Metrics** — Counters, gauges, histograms
- **Grafana Dashboard** — Pre-configured panels
- **Session Manifests** — Full reproducibility
- **OpenTelemetry Tracing** — Distributed request tracing with Jaeger
- **WebSocket Streaming** — Real-time job progress updates

### Operations
- **Git Sync** — Atomic commits with status
- **Webhook Notifications** — Discord/Slack
- **A/B Testing** — Statistical comparison
- **Benchmark Suite** — Performance analysis
- **Docker** — Multi-stage build
- **Systemd** — Hardened service
- **Makefile** — All operations

### Cloud-Native Orchestration
- **Kubernetes** — Production manifests with HPA, PDB, NetworkPolicy
- **Helm Charts** — Configurable deployments
- **Terraform** — AWS, GCP, Azure infrastructure modules
- **Load Testing** — k6 smoke, load, stress, spike, soak tests
- **Chaos Engineering** — Resilience testing with failure injection

### Advanced Features
- **Redis Caching** — Model metadata, prompt embeddings, workflow results
- **Rate Limiting** — Token bucket algorithm
- **API Server** — FastAPI-based REST API with auth
- **WebSocket Streaming** — Real-time job progress
- **OpenTelemetry** — Distributed tracing and metrics

## Complete File Structure

```
comfyui_engine/
├── engine/                          # Core modules (20+ files, ~10000 lines)
│   ├── __init__.py                  # Package exports
│   ├── core.py                      # Infrastructure (logging, metrics, CB, retry, queue)
│   ├── config.py                    # Pydantic configuration
│   ├── prompt_manager.py            # Templates, seeds, LoRA
│   ├── api_client.py                # Resilient HTTP/WebSocket client
│   ├── output_handler.py            # Downloads, metadata, manifests
│   ├── git_sync.py                  # Atomic git operations
│   ├── session_manager.py           # Session persistence
│   ├── checkpoint_resume.py         # Checkpoint/resume system
│   ├── websocket_manager.py         # WebSocket with reconnection
│   ├── websocket_stream.py          # WebSocket streaming for real-time updates
│   ├── api_server_ws.py             # WebSocket handler for API server
│   ├── metrics_server.py            # Prometheus HTTP endpoint
│   ├── distributed_queue.py         # Redis-backed queue
│   ├── workflow_validator.py        # JSON validation + node mapping
│   ├── ab_testing.py                # A/B testing framework
│   ├── notifications.py             # Discord/Slack webhooks
│   ├── tracing.py                   # OpenTelemetry distributed tracing
│   ├── redis_cache.py               # Redis-based caching layer
│   ├── dead_letter_queue.py         # Failed job handling
│   ├── profiler.py                  # Performance profiling
│   ├── shutdown_manager.py          # Graceful shutdown handling
│   ├── security.py                  # Security utilities
│   ├── protocols.py                 # Type protocols
│   ├── model_cache.py               # Model caching
│   ├── node_discovery.py            # Node discovery
│   ├── plugin_system.py             # Plugin architecture
│   ├── genetic_optimizer.py         # Genetic algorithm optimization
│   ├── mlflow_tracker.py            # MLflow integration
│   ├── auto_scaler.py               # Auto-scaling logic
│   ├── cloud_providers.py           # Cloud provider integrations
│   └── cluster_manager.py           # Cluster management
├── tests/
│   ├── test_engine.py               # Unit tests
│   ├── test_integration.py          # Integration tests
│   └── test_websocket_stream.py     # WebSocket streaming tests
├── tests/load/
│   ├── k6_load_test.js              # k6 load tests (smoke, load, stress, spike, soak)
│   ├── k6_chaos_test.js             # k6 chaos engineering tests
│   ├── run_load_tests.sh            # Load test runner
│   └── run_chaos_tests.sh           # Chaos test runner
├── k8s/                             # Kubernetes manifests
│   └── base/                        # Base Kustomize layer
│       ├── namespace.yaml
│       ├── configmap.yaml
│       ├── secret.yaml
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── hpa.yaml
│       ├── pdb.yaml
│       ├── network-policy.yaml
│       ├── rbac.yaml
│       ├── ingress.yaml
│       ├── pvc.yaml
│       └── kustomization.yaml
├── helm/                            # Helm charts
│   └── comfyui-engine/
│       ├── Chart.yaml
│       └── values.yaml
├── terraform/                       # Terraform modules
│   ├── modules/
│   │   ├── aws/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   ├── outputs.tf
│   │   │   └── eks.tf
│   │   ├── gcp/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   ├── outputs.tf
│   │   │   └── gke.tf
│   │   └── azure/
│   │       ├── main.tf
│   │       ├── variables.tf
│   │       ├── outputs.tf
│   │       └── aks.tf
│   └── environments/
│       ├── dev/
│       │   └── main.tf
│       ├── staging/
│       │   └── main.tf
│       └── production/
│           └── main.tf
├── monitoring/
│   ├── grafana/
│   │   ├── dashboards/              # Pre-built dashboard
│   │   └── datasources/             # Prometheus + Loki config
│   ├── prometheus.yml               # Scrape config
│   ├── loki.yml                     # Log aggregation config
│   └── promtail.yml                 # Log shipping config
├── systemd/
│   ├── comfyui-engine@.service      # Hardened systemd service
│   └── install.sh                   # Installation script
├── docs/
│   └── architecture_v2.md           # Architecture decisions
├── config/
│   └── prompts.yaml                 # Prompt dictionaries + LoRA
├── api_server.py                    # FastAPI REST API server
├── main.py                          # Unified CLI orchestrator
├── benchmark.py                     # Performance benchmark suite
├── dashboard.py                     # Monitoring dashboard
├── mobile_app.py                    # Mobile app interface
├── setup_wizard.py                  # Setup wizard
├── Dockerfile                       # Multi-stage build
├── docker-compose.yml               # Full stack (engine + monitoring)
├── Makefile                         # All operations
├── pyproject.toml                   # Packaging + tool configs
├── push_to_github.sh               # GitHub push script
└── README.md                        # This file
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `COMFYUI_URL` | http://127.0.0.1:8188 | ComfyUI API endpoint |
| `COMFYUI_MAX_CONCURRENT` | 3 | Parallel GPU jobs |
| `ENGINE_OUTPUT_DIR` | output_models/ | Output directory |
| `ENGINE_TIMEOUT` | 300 | Job timeout (seconds) |
| `ENGINE_POLL_INTERVAL` | 1.0 | Status poll frequency |
| `ENGINE_RETRY_MAX` | 3 | Max retries |
| `ENGINE_RETRY_BASE_DELAY` | 1.0 | Retry base delay |
| `ENGINE_CB_FAILURE_THRESHOLD` | 5 | Circuit breaker threshold |
| `ENGINE_CB_RECOVERY_TIMEOUT` | 30.0 | Recovery timeout |
| `ENGINE_QUEUE_MAX_SIZE` | 100 | Queue capacity |
| `ENGINE_QUEUE_RATE_LIMIT` | None | Jobs/second cap |
| `ENGINE_LOG_LEVEL` | INFO | Logging level |
| `ENGINE_JSON_LOGGING` | true | JSON log format |
| `ENGINE_METRICS_PORT` | 9090 | Metrics server port |
| `ENGINE_METRICS_WINDOW` | 1000 | Histogram window |
| `REDIS_URL` | redis://localhost:6379/0 | Redis for distributed mode |
| `OTLP_ENDPOINT` | None | OpenTelemetry collector endpoint |
| `TRACING_SAMPLER_RATIO` | 0.1 | Tracing sampling ratio |
| `API_KEY` | None | API key for authentication |
| `WEBHOOK_URL` | None | Webhook URL for notifications |

## CLI Reference

### Basic Commands
```bash
# Basic batch
python -m main --batch 8 --workflow workflows/standard.json

# With template and verbose logging
python -m main --batch 16 --template cinematic --workflow workflows/standard.json --verbose

# With metrics server
python -m main --batch 32 --max-concurrent 8 --metrics-port 9090 --workflow workflows/standard.json

# Resume interrupted batch
python -m main --batch 16 --workflow workflows/standard.json --resume-session session_123456

# Distributed worker mode
python -m main --distributed --redis-url redis://localhost:6379/0 --workflow workflows/standard.json

# Validate workflow only
python -m main --workflow workflows/standard.json --validate-workflow

# Health check only
python -m main --health-check-only
```

### All CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--batch` | 4 | Number of generations |
| `--lora` | 2 | LoRA models per generation |
| `--template` | standard | Prompt template |
| `--seed-strategy` | random | Seed generation strategy |
| `--seed` | None | Fixed seed |
| `--tags` | None | Comma-separated tags |
| `--workflow` | required | Path to ComfyUI workflow JSON |
| `--max-concurrent` | 3 | Parallel GPU jobs |
| `--base-url` | 127.0.0.1:8188 | ComfyUI URL |
| `--output-dir` | output_models/ | Output directory |
| `--config` | config/prompts.yaml | Config YAML |
| `--timeout` | 300 | Job timeout |
| `--poll-interval` | 1.0 | Poll frequency |
| `--resume-session` | None | Resume from session ID |
| `--metrics-port` | None | Start metrics server |
| `--distributed` | false | Distributed worker mode |
| `--redis-url` | redis://localhost:6379/0 | Redis URL |
| `--git-sync` | false | Sync to git after batch |
| `--repo-path` | . | Git repository path |
| `--init-repo` | false | Initialize git repo |
| `--remote` | None | Git remote URL |
| `--commit-msg` | None | Custom commit message |
| `--verbose` / `-v` | false | DEBUG logging |
| `--no-progress` | false | Disable progress bar |
| `--health-check-only` | false | Check health and exit |
| `--validate-workflow` | false | Validate workflow and exit |

## Makefile Commands

```bash
make install              # Install dependencies
make test                 # Run all tests
make test-unit            # Unit tests only
make test-integration     # Integration tests only
make test-coverage        # Tests with coverage report
make test-websocket       # WebSocket streaming tests
make benchmark            # Performance benchmark
make lint                 # Run linter
make format               # Format code
make type-check           # Type checking
make docker-build         # Build Docker image
make docker-run           # Run Docker container
make docker-compose-up    # Start full stack
make docker-compose-down  # Stop full stack
make k8s-deploy           # Deploy to Kubernetes
make k8s-delete           # Delete from Kubernetes
make helm-install         # Install Helm chart
make helm-upgrade         # Upgrade Helm chart
make terraform-init       # Initialize Terraform
make terraform-plan       # Plan Terraform changes
make terraform-apply      # Apply Terraform changes
make load-test            # Run load tests
make chaos-test           # Run chaos engineering tests
make systemd-install      # Install systemd service
make run                  # Basic batch generation
make run-cinematic        # Cinematic template
make run-distributed      # Distributed worker mode
make run-ab-test          # A/B testing
make run-metrics          # With metrics server
make run-resume           # Resume previous session
make clean                # Clean artifacts
make clean-all            # Clean everything including data
make health-check         # Check ComfyUI health
make metrics              # Fetch Prometheus metrics
make validate-workflow    # Validate workflow JSON
make git-sync             # Sync to git
```

## Docker Deployment

```bash
# Build and run basic
docker build -t comfyui-engine .
docker run -p 9090:9090 -v $(pwd)/workflows:/app/workflows:ro comfyui-engine

# Full stack with monitoring
docker-compose --profile full up -d
# Access: Grafana http://localhost:3000 (admin/admin)
#         Prometheus http://localhost:9091
#         Metrics http://localhost:9090/metrics

# Distributed mode with Redis
docker-compose --profile distributed up -d
```

## Kubernetes Deployment

```bash
# Deploy to Kubernetes
make k8s-deploy

# Or manually:
kubectl apply -k k8s/base/

# Check deployment status
kubectl get pods -n comfyui-engine
kubectl get svc -n comfyui-engine
kubectl get hpa -n comfyui-engine

# View logs
kubectl logs -f deployment/comfyui-engine -n comfyui-engine

# Scale deployment
kubectl scale deployment/comfyui-engine --replicas=5 -n comfyui-engine
```

## Helm Deployment

```bash
# Install Helm chart
make helm-install

# Or manually:
helm install comfyui-engine ./helm/comfyui-engine

# Upgrade
helm upgrade comfyui-engine ./helm/comfyui-engine

# Uninstall
helm uninstall comfyui-engine
```

## Terraform Deployment

```bash
# Initialize Terraform
make terraform-init

# Plan changes
make terraform-plan

# Apply changes
make terraform-apply

# Or manually:
cd terraform/environments/production
terraform init
terraform plan
terraform apply
```

## Systemd Service (Arch Linux)

```bash
# Install
sudo make systemd-install

# Start
sudo systemctl start comfyui-engine@$USER

# Enable auto-start
sudo systemctl enable comfyui-engine@$USER

# View logs
sudo journalctl -u comfyui-engine@$USER -f

# Or use install script
sudo ./systemd/install.sh
```

## Monitoring Stack

```bash
# Start Prometheus + Grafana + Loki
docker-compose --profile monitoring up -d

# Access dashboards
open http://localhost:3000    # Grafana (admin/admin)
open http://localhost:9091    # Prometheus
open http://localhost:9090/metrics  # Engine metrics

# Query logs in Grafana
# Data source: Loki
# Query: {job="comfyui-engine"}
```

## OpenTelemetry Tracing

```python
from engine.tracing import initialize_tracing, trace_span

# Initialize tracing
tracing = initialize_tracing(
    service_name="comfyui-engine",
    service_version="5.0.0",
    environment="production",
    otlp_endpoint="http://localhost:4317",
    sampler_ratio=0.1,
)

# Trace a function
@trace_span("generate_image")
async def generate_image(workflow):
    # Your code here
    pass
```

## WebSocket Streaming

```python
from engine.websocket_stream import (
    WebSocketStreamManager,
    StreamEvent,
    StreamEventType,
    initialize_stream_manager,
)

# Initialize stream manager
stream_manager = await initialize_stream_manager()

# Create a progress event
event = WebSocketStreamManager.create_progress_event(
    job_id="job_123",
    progress=75.0,
    stage="sampling",
    extra_data={"step": 10},
)

# Broadcast to all subscribers
await stream_manager.broadcast_event(event)
```

## Redis Caching

```python
from engine.redis_cache import CacheManager, CacheConfig

# Initialize cache manager
cache = CacheManager(CacheConfig(
    host="localhost",
    port=6379,
    default_ttl=3600,
))
await cache.connect()

# Cache model info
await cache.models.set_model_info("model_v1", {"name": "Model V1"})

# Cache prompt embedding
await cache.prompts.set_embedding("prompt text", [0.1, 0.2, 0.3])

# Cache workflow result
await cache.results.set_result(workflow, seed, {"output": "image.png"})
```

## Load Testing

```bash
# Run all load tests
make load-test

# Run specific test scenarios
COMFYUI_ENGINE_URL=http://localhost:8000 ./tests/load/run_load_tests.sh

# Run chaos engineering tests
make chaos-test

# Run specific chaos scenario
COMFYUI_ENGINE_URL=http://localhost:8000 CHAOS_TYPE=memory_pressure ./tests/load/run_chaos_tests.sh
```

## A/B Testing

```python
from engine.config import ConfigLoader
from engine.ab_testing import ABTestRunner
from engine.main import UnifiedGenerationEngine

config = ConfigLoader.load()
engine = UnifiedGenerationEngine(config)
runner = ABTestRunner(engine)

# Compare templates
result = await runner.run_test(
    test_type="templates",
    generations_per_variant=50,
    workflow=workflow,
)

# Print report
from engine.ab_testing import ABTestFramework
framework = ABTestFramework(config)
framework.print_report(result)
```

## Webhook Notifications

```python
from engine.notifications import WebhookNotifier, NotificationConfig, WebhookType

# Discord
config = NotificationConfig(
    webhook_url="https://discord.com/api/webhooks/...",
    webhook_type=WebhookType.DISCORD,
    notify_on_success=True,
    notify_on_failure=True,
    mention_on_failure="@everyone",
)
notifier = WebhookNotifier(config)
await notifier.notify_batch_complete(
    session_id="session_123",
    total_jobs=10,
    completed=9,
    failed=1,
    duration_seconds=150.0,
)

# Slack
config = NotificationConfig(
    webhook_url="https://hooks.slack.com/services/...",
    webhook_type=WebhookType.SLACK,
)
```

## Distributed Multi-GPU

```bash
# Terminal 1: Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Terminal 2: Start producer
python -m main --batch 100 --workflow workflows/standard.json

# Terminal 3: Start worker 1 (GPU 0)
CUDA_VISIBLE_DEVICES=0 python -m main --distributed --redis-url redis://localhost:6379/0

# Terminal 4: Start worker 2 (GPU 1)
CUDA_VISIBLE_DEVICES=1 python -m main --distributed --redis-url redis://localhost:6379/0
```

## Testing

```bash
# All tests
make test

# With coverage
make test-coverage

# Specific test class
pytest tests/test_integration.py::TestEndToEndPipeline -v

# WebSocket tests
make test-websocket

# Benchmark
make benchmark
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| ComfyUI not responding | Check `make health-check`, verify URL |
| Circuit breaker open | Wait 30s for auto-recovery, check GPU load |
| Queue full | Increase `ENGINE_QUEUE_MAX_SIZE` or reduce batch size |
| WebSocket disconnects | Check network, increase heartbeat interval |
| Git sync fails | Check `.gitignore`, verify repo permissions |
| Redis connection fails | Start Redis: `docker run -d -p 6379:6379 redis` |
| Tracing not working | Verify OTLP endpoint, check sampler ratio |
| Load test failures | Check server resources, review error logs |

## License

MIT
