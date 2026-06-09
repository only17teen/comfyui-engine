# Changelog

All notable changes to ComfyUI Async Generation Engine are documented here.

## [5.1.0] - 2026-06-09

### Fixed
- **CORS security**: `allow_origins=["*"]` + `allow_credentials=True` is invalid per CORS spec
  and rejected by all browsers. Changed to explicit `cors_origins` parameter.
- **WebhookManager.shutdown()**: method was missing, causing `AttributeError` on graceful shutdown
- **API Pydantic models**: all endpoints now use typed `JobSubmitRequest`, `WebhookRegisterRequest`,
  `APIKeyCreateRequest` instead of untyped `dict[str, Any]`
- **list_jobs pagination**: `"total"` now returns real total count, not `len(page)`
- **CI/CD**: added missing `docker/login-action@v3` for GHCR push in release job
- **CI/CD**: replaced deprecated `actions/create-release@v1` with `softprops/action-gh-release@v2`
- **CI/CD**: added Python 3.10 to test matrix (listed in pyproject.toml but not tested)
- **Version strings**: `core.py`, `main.py`, `api_client.py` all said "v2.0" — aligned to v5.x
- **pyproject.toml**: added `black>=24.0.0`, `safety>=3.0.0`, `bandit>=1.7.0` to dev deps
- **.gitignore**: `__pycache__/` was committed to git; added proper gitignore
- **CHANGELOG.md**: this file was missing

## [5.0.0] - 2026-06-06

### Added
- OpenTelemetry distributed tracing with Jaeger (`engine/tracing.py`)
- WebSocket streaming for real-time job progress (`engine/websocket_stream.py`)
- k6 load testing suite: smoke, load, stress, spike, soak (`tests/load/`)
- Chaos engineering tests (`tests/load/k6_chaos_test.js`)
- Advanced Redis caching layer (`engine/redis_cache.py`)
- Kubernetes manifests: HPA, PDB, NetworkPolicy, RBAC (`k8s/base/`)
- Helm chart (`helm/comfyui-engine/`)
- Terraform modules: AWS EKS, GCP GKE, Azure AKS (`terraform/`)

## [4.0.0] - 2026-06-05

### Added
- Initial production release: async engine, circuit breaker, retry logic,
  distributed Redis queue, Prometheus metrics, MLflow tracking,
  session manager, checkpoint/resume, A/B testing, webhooks, REST API
