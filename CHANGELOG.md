# Changelog

## [5.1.0] - 2026-06-09

### Fixed
- CORS: `allow_origins=["*"]` + `allow_credentials=True` violates CORS spec — now uses explicit origins
- `WebhookManager.shutdown()` was missing — caused AttributeError on graceful shutdown
- API endpoints: replaced `dict[str, Any]` with typed Pydantic request models
- `list_jobs`: `total` now returns real count, not `len(paginated_result)`
- CI: added `docker/login-action` for GHCR (release job was failing without auth)
- CI: replaced deprecated `actions/create-release@v1` with `softprops/action-gh-release@v2`
- CI: added Python 3.10 to test matrix
- Version strings: `core.py`, `main.py`, `api_client.py` said "v2.0" — aligned to v5.1
- `pyproject.toml`: added `black`, `safety`, `bandit` to dev deps
- `.gitignore`: `__pycache__/` was tracked in git

## [5.0.0] - 2026-06-06
- OpenTelemetry, WebSocket streaming, Redis caching, k8s, Helm, Terraform
