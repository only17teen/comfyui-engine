# ComfyUI Async Generation Engine v2.0

Production-grade async pipeline for AI character generation via ComfyUI API.
Built for Arch Linux, Python 3.11+, with enterprise resilience patterns.

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
┌─────────────────────────────────────────────────────────────┐
│                    UnifiedGenerationEngine                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Config    │  │   Prompt    │  │      API Client     │  │
│  │  (Pydantic) │  │  (Templates)│  │ (Circuit Breaker +  │  │
│  │             │  │  (Seeds)    │  │  Retry + WebSocket) │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Session   │  │ Checkpoint  │  │   Output Handler    │  │
│  │  Manager    │  │  / Resume   │  │ (Downloads + Meta)  │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Metrics   │  │  Webhook    │  │   A/B Testing       │  │
│  │  Server     │  │ Notifications│  │   Framework         │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## All Features

### Core Engine
- **Asyncio + aiohttp** — Parallel GPU job submission
- **Circuit Breaker** — Prevents cascading failures
- **Exponential Backoff Retry** — With jitter
- **Priority Queue** — Backpressure and rate limiting
- **WebSocket Manager** — Auto-reconnection, heartbeat
- **Metrics Server** — Prometheus-compatible HTTP endpoint

### Configuration
- **Pydantic Validation** — Type-safe, fail-fast
- **Environment Override** — 15+ env vars
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

### Observability
- **Structured JSON Logging** — Loki/Vector integration
- **Prometheus Metrics** — Counters, gauges, histograms
- **Grafana Dashboard** — Pre-configured panels
- **Session Manifests** — Full reproducibility

### Operations
- **Git Sync** — Atomic commits with status
- **Webhook Notifications** — Discord/Slack
- **A/B Testing** — Statistical comparison
- **Benchmark Suite** — Performance analysis
- **Docker** — Multi-stage build
- **Systemd** — Hardened service
- **Makefile** — All operations

## Complete File Structure

```
comfyui_engine/
├── engine/                          # Core modules (15 files, ~7000 lines)
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
│   ├── metrics_server.py            # Prometheus HTTP endpoint
│   ├── distributed_queue.py         # Redis-backed queue
│   ├── workflow_validator.py        # JSON validation + node mapping
│   ├── ab_testing.py                # A/B testing framework
│   └── notifications.py             # Discord/Slack webhooks
├── tests/
│   ├── test_engine.py               # Unit tests (458 lines)
│   └── test_integration.py          # Integration tests (2600 lines)
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
├── main.py                          # Unified CLI orchestrator
├── benchmark.py                     # Performance benchmark suite
├── Dockerfile                       # Multi-stage build
├── docker-compose.yml               # Full stack (engine + monitoring)
├── Makefile                         # All operations
├── pyproject.toml                   # Packaging + tool configs
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
make benchmark            # Performance benchmark
make lint                 # Run linter
make format               # Format code
make type-check           # Type checking
make docker-build         # Build Docker image
make docker-run           # Run Docker container
make docker-compose-up    # Start full stack
make docker-compose-down  # Stop full stack
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

## License

MIT
