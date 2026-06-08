# ComfyUI Engine - Kiro Protocol Optimized

A high-performance, distributed inference engine for ComfyUI with Kiro Protocol v3.0 optimizations.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Nginx (Load Balancer)                 │
│                        Port 80/443                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
│  ComfyUI     │ │ ComfyUI  │ │  ComfyUI   │
│  Engine      │ │ Engine   │ │  Engine    │
│  (Worker 1)  │ │ (Worker 2)│ │ (Worker 3) │
│  Port 8080   │ │ Port 8080│ │  Port 8080 │
└───────┬──────┘ └────┬─────┘ └─────┬──────┘
        │              │              │
        └──────────────┼──────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
│   Redis      │ │ PostgreSQL│ │   Jaeger   │
│   (Queue)    │ │ (State)  │ │ (Tracing)  │
│   Port 6379  │ │ Port 5432│ │  Port 4317 │
└──────────────┘ └──────────┘ └────────────┘
        │
┌───────▼──────┐
│  Prometheus  │
│  (Metrics)   │
│  Port 9090   │
└───────┬──────┘
        │
┌───────▼──────┐
│   Grafana    │
│ (Dashboards) │
│  Port 3000   │
└──────────────┘
```

## Quick Start

### Prerequisites

- Python 3.10+
- Docker & Docker Compose
- NVIDIA GPU (optional, for GPU acceleration)

### Installation

```bash
# Clone the repository
git clone https://github.com/only17teen/comfyui-engine.git
cd comfyui-engine

# Install dependencies
make install-dev

# Start local development stack
make docker-up
```

### Development Commands

```bash
# Run all tests
make test

# Run fast unit tests only
make test-fast

# Run linting
make lint

# Format code
make format

# Run type checking
make type-check

# Build Docker image
make build

# Run benchmarks
make benchmark

# Full CI pipeline
make ci
```

## Kiro Protocol Optimizations

This engine implements the Kiro Protocol v3.0 optimization rules:

### Rule 1: Relentless Optimization
- Batch metrics collection with asyncio.Queue
- Lock-free counters for high-throughput operations
- Pre-computed retry delay tables
- Object pooling for reduced allocations

### Rule 3: Scale by Default
- Auto-scaling from 1-50 workers based on queue depth and GPU utilization
- Hysteresis-based scaling decisions (60s up, 300s down cooldown)
- Emergency scaling for queue depth > 100

### Rule 4: Reliability as Feature
- Composite health checker with parallel checks
- Circuit breaker with fast-path optimization
- SLI/SLO monitoring with automatic alerting
- Multi-region active-active failover

### Rule 6: Memory First
- `__slots__` in dataclasses for memory reduction
- Generic ObjectPool[T] for reusable objects
- SQLite WAL mode for concurrent reads/writes
- TTL-based cache eviction

### Rule 7: Async Correctness
- Proper async polling with adaptive intervals
- No blocking sleeps in async paths
- Structured concurrency with task groups

### Rule 10: API & Security
- JWT secret rotation every 24 hours
- JSON schema validation for all requests
- Device fingerprint token binding
- Sliding window rate limiting with burst support

### Rule 11: Observability
- OpenTelemetry tracing with W3C context propagation
- Prometheus metrics with SLI/SLO gauges
- Structured JSON logging
- Grafana dashboards for all components

## Components

| Component | Description | File |
|-----------|-------------|------|
| Core Engine | Main inference engine with optimizations | `engine/core.py` |
| API Client | ComfyUI client with pooling and adaptive polling | `engine/api_client.py` |
| Auto Scaler | Dynamic worker scaling with hysteresis | `engine/auto_scaler.py` |
| Metrics Server | SLI/SLO monitoring and alerting | `engine/metrics_server.py` |
| Security Manager | JWT, validation, rate limiting | `engine/security.py` |
| Distributed Queue | Redis-backed job queue | `engine/distributed_queue.py` |
| Session Manager | SQLite WAL checkpoint management | `engine/session_manager.py` |
| WebSocket Manager | Real-time streaming | `engine/websocket_manager.py` |

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_comprehensive.py -v

# Run with coverage
pytest tests/ --cov=engine --cov-report=html

# Run load tests
make test-load
```

## Deployment

### Docker Compose (Local)

```bash
make docker-up
```

### Kubernetes

```bash
make k8s-deploy
```

### Helm

```bash
make helm-install
```

### Terraform (AWS)

```bash
make tf-plan
make tf-apply
```

## Monitoring

- **Prometheus**: http://localhost:9091
- **Grafana**: http://localhost:3000 (admin/admin)
- **Jaeger**: http://localhost:16686

## Environment Configuration

Copy the appropriate environment file:

```bash
cp config/development.env .env   # Development
cp config/staging.env .env     # Staging
cp config/production.env .env    # Production
```

## Contributing

1. Install pre-commit hooks: `pre-commit install`
2. Run tests before committing: `make test-fast`
3. Follow the Kiro Protocol optimization rules
4. Update documentation for new features

## License

MIT License - see LICENSE file for details.
