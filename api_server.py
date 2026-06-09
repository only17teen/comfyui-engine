"""REST API for external integrations with ComfyUI Engine v5.1.

Fixes applied:
- FIX #2: CORS: allow_origins=["*"] + allow_credentials=True is invalid (CORS spec).
          Use explicit origins list.
- FIX #4: WebhookManager.shutdown() was missing — caused AttributeError on graceful shutdown.
- FIX #5: All API endpoints now use typed Pydantic models instead of dict[str, Any].
- FIX #6: list_jobs returns true total count, not len(paginated_result).
"""

import asyncio
import hashlib
import hmac as hmac_module
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


# ── Pydantic Request Models (FIX #5) ─────────────────────────────────────

class JobSubmitRequest(BaseModel):
    """Request model for job submission."""

    workflow: dict[str, Any] = Field(..., description="ComfyUI workflow JSON")
    template: str | None = Field(None, description="Prompt template name")
    batch_size: int = Field(1, ge=1, le=100)
    seed: int | None = Field(None)
    tags: list[str] = Field(default_factory=list)
    priority: int = Field(2, ge=0, le=3, description="0=CRITICAL 1=HIGH 2=NORMAL 3=LOW")


class WebhookRegisterRequest(BaseModel):
    """Request model for webhook registration."""

    url: str = Field(..., description="Webhook target URL")
    events: list[str] = Field(..., min_length=1)
    secret: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class APIKeyCreateRequest(BaseModel):
    """Request model for API key creation."""

    name: str = Field(..., min_length=1, max_length=64)
    scopes: list[str] = Field(default=["read"])
    rate_limit: int = Field(100, ge=1, le=10000)
    expires_in_days: int | None = Field(None, ge=1, le=365)


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class APIKey:
    """API key record."""

    key_id: str
    key_hash: str
    name: str
    created_at: float
    expires_at: float | None = None
    scopes: list[str] = field(default_factory=lambda: ["read", "write"])
    rate_limit: int = 100
    is_active: bool = True
    last_used: float | None = None
    usage_count: int = 0


@dataclass
class RateLimitInfo:
    """Per-client rate limit state."""

    requests: list[float] = field(default_factory=list)
    limit: int = 100
    window: float = 60.0

    def _clean(self) -> None:
        cutoff = time.time() - self.window
        self.requests = [r for r in self.requests if r > cutoff]

    def is_allowed(self) -> bool:
        self._clean()
        return len(self.requests) < self.limit

    def add_request(self) -> None:
        self.requests.append(time.time())

    @property
    def remaining(self) -> int:
        self._clean()
        return max(0, self.limit - len(self.requests))


@dataclass
class WebhookConfig:
    """Webhook configuration."""

    url: str
    events: list[str]
    secret: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    is_active: bool = True
    retry_count: int = 3
    timeout: float = 30.0
    created_at: float = field(default_factory=time.time)


# ── Managers ──────────────────────────────────────────────────────────────

class APIKeyManager:
    """Manages API key lifecycle."""

    def __init__(self, keys_file: Path | None = None):
        self.keys_file = keys_file or Path("config/api_keys.json")
        self._keys: dict[str, APIKey] = {}
        self._key_hashes: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._load_keys()

    def _load_keys(self) -> None:
        if self.keys_file.exists():
            try:
                with open(self.keys_file) as f:
                    data = json.load(f)
                for kd in data.get("keys", []):
                    k = APIKey(**kd)
                    self._keys[k.key_id] = k
                    self._key_hashes[k.key_hash] = k.key_id
            except Exception as e:
                logger.warning(f"Failed to load API keys: {e}")

    async def _save_keys(self) -> None:
        async with self._lock:
            data = {"keys": [
                {"key_id": k.key_id, "key_hash": k.key_hash, "name": k.name,
                 "created_at": k.created_at, "expires_at": k.expires_at,
                 "scopes": k.scopes, "rate_limit": k.rate_limit,
                 "is_active": k.is_active, "last_used": k.last_used,
                 "usage_count": k.usage_count}
                for k in self._keys.values()
            ]}
            self.keys_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.keys_file, "w") as f:
                json.dump(data, f, indent=2)

    async def create_key(self, name: str, scopes: list[str] | None = None,
                         rate_limit: int = 100, expires_in_days: int | None = None,
                         ) -> tuple[str, str]:
        """Create new API key. Returns (key_id, plain_key)."""
        key_id = str(uuid4())[:8]
        plain_key = f"ce_{uuid4().hex}"
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        expires_at = time.time() + expires_in_days * 86400 if expires_in_days else None
        key = APIKey(key_id=key_id, key_hash=key_hash, name=name,
                     created_at=time.time(), expires_at=expires_at,
                     scopes=scopes or ["read", "write"], rate_limit=rate_limit)
        async with self._lock:
            self._keys[key_id] = key
            self._key_hashes[key_hash] = key_id
        await self._save_keys()
        logger.info(f"Created API key: {name} ({key_id})")
        return key_id, plain_key

    async def validate_key(self, plain_key: str) -> APIKey | None:
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        key_id = self._key_hashes.get(key_hash)
        if not key_id:
            return None
        key = self._keys.get(key_id)
        if not key or not key.is_active:
            return None
        if key.expires_at and time.time() > key.expires_at:
            return None
        key.last_used = time.time()
        key.usage_count += 1
        return key

    async def revoke_key(self, key_id: str) -> bool:
        async with self._lock:
            key = self._keys.get(key_id)
            if not key:
                return False
            key.is_active = False
            self._key_hashes.pop(key.key_hash, None)
        await self._save_keys()
        logger.info(f"Revoked API key: {key_id}")
        return True

    async def list_keys(self) -> list[dict[str, Any]]:
        return [{"key_id": k.key_id, "name": k.name, "created_at": k.created_at,
                 "expires_at": k.expires_at, "scopes": k.scopes,
                 "rate_limit": k.rate_limit, "is_active": k.is_active,
                 "last_used": k.last_used, "usage_count": k.usage_count}
                for k in self._keys.values()]


class RateLimiter:
    """Token-window rate limiter."""

    def __init__(self):
        self._limits: dict[str, RateLimitInfo] = {}
        self._lock = asyncio.Lock()

    async def check_rate_limit(self, key_id: str, limit: int = 100,
                               ) -> tuple[bool, int, int]:
        async with self._lock:
            if key_id not in self._limits:
                self._limits[key_id] = RateLimitInfo(limit=limit)
            info = self._limits[key_id]
            info.limit = limit
            allowed = info.is_allowed()
            remaining = info.remaining
            if allowed:
                info.add_request()
            reset_after = int(info.window - (time.time() - min(info.requests))) if info.requests else 0
            return allowed, remaining, max(0, reset_after)


class WebhookManager:
    """Manages webhook registrations and delivery."""

    def __init__(self, webhooks_file: Path | None = None):
        self.webhooks_file = webhooks_file or Path("config/webhooks.json")
        self._webhooks: dict[str, WebhookConfig] = {}
        self._active = True
        self._load_webhooks()

    def _load_webhooks(self) -> None:
        if self.webhooks_file.exists():
            try:
                with open(self.webhooks_file) as f:
                    data = json.load(f)
                for wd in data.get("webhooks", []):
                    wh = WebhookConfig(**wd)
                    self._webhooks[wh.url] = wh
            except Exception as e:
                logger.warning(f"Failed to load webhooks: {e}")

    async def _save_webhooks(self) -> None:
        data = {"webhooks": [
            {"url": w.url, "events": w.events, "secret": w.secret,
             "headers": w.headers, "is_active": w.is_active,
             "retry_count": w.retry_count, "timeout": w.timeout,
             "created_at": w.created_at}
            for w in self._webhooks.values()
        ]}
        self.webhooks_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.webhooks_file, "w") as f:
            json.dump(data, f, indent=2)

    async def register(self, url: str, events: list[str],
                       secret: str | None = None,
                       headers: dict[str, str] | None = None) -> str:
        webhook_id = hashlib.sha256(url.encode()).hexdigest()[:12]
        self._webhooks[webhook_id] = WebhookConfig(
            url=url, events=events, secret=secret, headers=headers or {})
        await self._save_webhooks()
        logger.info(f"Registered webhook: {url} for events: {events}")
        return webhook_id

    async def unregister(self, webhook_id: str) -> bool:
        if webhook_id not in self._webhooks:
            return False
        del self._webhooks[webhook_id]
        await self._save_webhooks()
        return True

    async def fire_event(self, event: str, payload: dict[str, Any]) -> None:
        if not self._active:
            return
        tasks = [self._send_webhook(wh, event, payload)
                 for wh in self._webhooks.values()
                 if wh.is_active and event in wh.events]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(self, webhook: WebhookConfig, event: str,
                            payload: dict[str, Any]) -> None:
        headers = {"Content-Type": "application/json",
                   "X-Webhook-Event": event,
                   "X-Webhook-Timestamp": str(int(time.time())),
                   **webhook.headers}
        if webhook.secret:
            body_str = json.dumps(payload, sort_keys=True)
            sig = hmac_module.new(webhook.secret.encode(),
                                  body_str.encode(), hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"
        body = json.dumps(payload)
        for attempt in range(webhook.retry_count):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=webhook.timeout)
                ) as session:
                    async with session.post(webhook.url, data=body, headers=headers) as r:
                        if r.status < 400:
                            return
            except Exception as e:
                logger.warning(f"Webhook error: {webhook.url} - {e}")
            if attempt < webhook.retry_count - 1:
                await asyncio.sleep(2 ** attempt)
        logger.error(f"Webhook permanently failed: {webhook.url}")

    async def shutdown(self) -> None:
        """FIX #4: Gracefully stop webhook processing."""
        self._active = False
        logger.info("WebhookManager shutdown complete")


# ── REST API Server ───────────────────────────────────────────────────────

class RESTAPIServer:
    """FastAPI-based REST API server for ComfyUI Engine v5.1."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        engine: Any | None = None,
        enable_auth: bool = True,
        # FIX #2: explicit origins list — never use ["*"] with allow_credentials=True
        cors_origins: list[str] | None = None,
    ):
        self.host = host
        self.port = port
        self.engine = engine
        self.enable_auth = enable_auth
        self._cors_origins = cors_origins or []

        self.app = FastAPI(
            title="ComfyUI Engine API",
            description="REST API for ComfyUI Engine external integrations",
            version="5.1.0",   # FIX #1
            docs_url="/docs",
            redoc_url="/redoc",
        )

        self.key_manager = APIKeyManager()
        self.rate_limiter = RateLimiter()
        self.webhook_manager = WebhookManager()
        self._start_time = time.time()

        self._setup_middleware()
        self._setup_routes()

    def _setup_middleware(self) -> None:
        if self._cors_origins:
            # FIX #2: never allow_origins=["*"] with credentials
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=self._cors_origins,
                allow_credentials=True,
                allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
                allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            )
        self.app.add_middleware(GZipMiddleware, minimum_size=1000)

    def _setup_routes(self) -> None:
        async def get_api_key(
            credentials: HTTPAuthorizationCredentials = Depends(security),
        ) -> APIKey | None:
            if not self.enable_auth:
                return None
            if not credentials:
                raise HTTPException(status_code=401, detail="API key required")
            key = await self.key_manager.validate_key(credentials.credentials)
            if not key:
                raise HTTPException(status_code=401, detail="Invalid API key")
            allowed, remaining, reset_after = await self.rate_limiter.check_rate_limit(
                key.key_id, key.rate_limit)
            if not allowed:
                raise HTTPException(
                    status_code=429, detail="Rate limit exceeded",
                    headers={"X-RateLimit-Limit": str(key.rate_limit),
                             "X-RateLimit-Remaining": str(remaining),
                             "X-RateLimit-Reset": str(reset_after)})
            return key

        # System
        @self.app.get("/health", tags=["System"])
        async def health():
            return {"status": "healthy", "version": "5.1.0", "timestamp": time.time()}

        @self.app.get("/ready", tags=["System"])
        async def ready():
            checks = {"api": True, "engine_connected": self.engine is not None}
            if self.engine and hasattr(self.engine, "health_check"):
                try:
                    checks["engine_healthy"] = await self.engine.health_check()
                except Exception:
                    checks["engine_healthy"] = False
            ok = all(checks.values())
            return JSONResponse(status_code=200 if ok else 503,
                                content={"status": "ready" if ok else "not_ready",
                                         "checks": checks, "timestamp": time.time()})

        @self.app.get("/live", tags=["System"])
        async def live():
            return {"status": "alive", "timestamp": time.time()}

        @self.app.get("/metrics", tags=["System"])
        async def metrics_endpoint():
            lines = [
                '# HELP comfyui_engine_info Version',
                '# TYPE comfyui_engine_info gauge',
                'comfyui_engine_info{version="5.1.0"} 1',
                '# HELP comfyui_engine_uptime_seconds Uptime',
                '# TYPE comfyui_engine_uptime_seconds counter',
                f'comfyui_engine_uptime_seconds {time.time() - self._start_time}',
            ]
            if self.engine and hasattr(self.engine, "get_metrics"):
                try:
                    em = await self.engine.get_metrics()
                    for k, v in em.items():
                        if isinstance(v, int | float):
                            lines += [f'# TYPE comfyui_engine_{k} gauge',
                                      f'comfyui_engine_{k} {v}']
                except Exception:
                    pass
            return StreamingResponse(iter("\n".join(lines) + "\n"), media_type="text/plain")

        @self.app.post("/shutdown", tags=["System"])
        async def shutdown(key: APIKey = Depends(get_api_key)):
            if "admin" not in key.scopes:
                raise HTTPException(status_code=403, detail="Admin scope required")
            asyncio.create_task(self._shutdown())
            return {"status": "shutting_down", "timestamp": time.time()}

        # Jobs
        @self.app.post("/api/v1/jobs", tags=["Jobs"], status_code=202)
        async def submit_job(job_data: JobSubmitRequest,  # FIX #5
                             key: APIKey = Depends(get_api_key)):
            job_id = str(uuid4())[:12]
            if self.engine and hasattr(self.engine, "submit_job"):
                job_id = await self.engine.submit_job(job_data.model_dump())
            await self.webhook_manager.fire_event("job.created", {"job_id": job_id, "status": "pending"})
            return {"job_id": job_id, "status": "pending"}

        @self.app.get("/api/v1/jobs/{job_id}", tags=["Jobs"])
        async def get_job(job_id: str, key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "get_job_status"):
                s = await self.engine.get_job_status(job_id)
                if s:
                    return s
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        @self.app.get("/api/v1/jobs", tags=["Jobs"])
        async def list_jobs(job_status: str | None = None,
                            limit: int = 100, offset: int = 0,
                            key: APIKey = Depends(get_api_key)):
            jobs, total_count = [], 0
            if self.engine and hasattr(self.engine, "list_jobs"):
                # FIX #6: get ALL jobs first, then paginate
                all_jobs = await self.engine.list_jobs(job_status, limit=None, offset=0)
                total_count = len(all_jobs)
                jobs = all_jobs[offset: offset + limit]
            return {"jobs": jobs, "total": total_count, "limit": limit, "offset": offset}

        @self.app.delete("/api/v1/jobs/{job_id}", tags=["Jobs"])
        async def cancel_job(job_id: str, key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "cancel_job"):
                if await self.engine.cancel_job(job_id):
                    await self.webhook_manager.fire_event("job.cancelled", {"job_id": job_id})
                    return {"status": "cancelled"}
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        # Queue
        @self.app.get("/api/v1/queue", tags=["Queue"])
        async def queue_status(key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "get_queue_status"):
                return await self.engine.get_queue_status()
            return {"length": 0, "pending": 0, "running": 0}

        @self.app.post("/api/v1/queue/pause", tags=["Queue"])
        async def pause_queue(key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "pause_queue"):
                await self.engine.pause_queue()
            return {"status": "paused"}

        @self.app.post("/api/v1/queue/resume", tags=["Queue"])
        async def resume_queue(key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "resume_queue"):
                await self.engine.resume_queue()
            return {"status": "resumed"}

        # Models
        @self.app.get("/api/v1/models", tags=["Models"])
        async def list_models(key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "list_models"):
                return await self.engine.list_models()
            return {"models": []}

        @self.app.post("/api/v1/models/{model_name}/preload", tags=["Models"])
        async def preload_model(model_name: str, key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "preload_model"):
                ok = await self.engine.preload_model(model_name)
                return {"model": model_name, "loaded": ok}
            raise HTTPException(status_code=501, detail="Model preloading not available")

        # Metrics
        @self.app.get("/api/v1/metrics", tags=["Metrics"])
        async def get_metrics(key: APIKey = Depends(get_api_key)):
            if self.engine and hasattr(self.engine, "get_metrics"):
                return await self.engine.get_metrics()
            return {"jobs_completed": 0, "jobs_failed": 0, "avg_generation_time": 0, "queue_length": 0}

        # Webhooks
        @self.app.post("/api/v1/webhooks", tags=["Webhooks"])
        async def register_webhook(config: WebhookRegisterRequest,  # FIX #5
                                   key: APIKey = Depends(get_api_key)):
            wid = await self.webhook_manager.register(
                url=config.url, events=config.events,
                secret=config.secret, headers=config.headers)
            return {"webhook_id": wid, "status": "registered"}

        @self.app.delete("/api/v1/webhooks/{webhook_id}", tags=["Webhooks"])
        async def unregister_webhook(webhook_id: str, key: APIKey = Depends(get_api_key)):
            if await self.webhook_manager.unregister(webhook_id):
                return {"status": "unregistered"}
            raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

        @self.app.get("/api/v1/webhooks", tags=["Webhooks"])
        async def list_webhooks(key: APIKey = Depends(get_api_key)):
            return {"webhooks": [{"url": w.url, "events": w.events, "is_active": w.is_active}
                                 for w in self.webhook_manager._webhooks.values()]}

        # Admin: API keys
        @self.app.post("/api/v1/keys", tags=["Admin"])
        async def create_key(config: APIKeyCreateRequest,  # FIX #5
                             key: APIKey = Depends(get_api_key)):
            if "admin" not in key.scopes:
                raise HTTPException(status_code=403, detail="Admin scope required")
            kid, plain = await self.key_manager.create_key(
                name=config.name, scopes=config.scopes,
                rate_limit=config.rate_limit, expires_in_days=config.expires_in_days)
            return {"key_id": kid, "key": plain, "name": config.name}

        @self.app.delete("/api/v1/keys/{key_id}", tags=["Admin"])
        async def revoke_key(key_id: str, key: APIKey = Depends(get_api_key)):
            if "admin" not in key.scopes:
                raise HTTPException(status_code=403, detail="Admin scope required")
            if await self.key_manager.revoke_key(key_id):
                return {"status": "revoked"}
            raise HTTPException(status_code=404, detail=f"Key {key_id} not found")

        @self.app.get("/api/v1/keys", tags=["Admin"])
        async def list_keys(key: APIKey = Depends(get_api_key)):
            if "admin" not in key.scopes:
                raise HTTPException(status_code=403, detail="Admin scope required")
            return {"keys": await self.key_manager.list_keys()}

    async def _shutdown(self) -> None:
        logger.info("Initiating graceful shutdown...")
        if self.engine and hasattr(self.engine, "shutdown"):
            await self.engine.shutdown()
        await self.webhook_manager.shutdown()  # FIX #4
        logger.info("Graceful shutdown complete")

    async def start(self) -> None:
        """Start the API server."""
        import uvicorn
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        await uvicorn.Server(config).serve()


async def create_api_server(engine: Any | None = None, host: str = "0.0.0.0",
                            port: int = 8000, cors_origins: list[str] | None = None,
                            ) -> RESTAPIServer:
    """Factory for RESTAPIServer."""
    return RESTAPIServer(engine=engine, host=host, port=port, cors_origins=cors_origins)


if __name__ == "__main__":
    asyncio.run(RESTAPIServer().start())
