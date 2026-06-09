"""REST API for external integrations with ComfyUI Engine.

Provides a comprehensive HTTP API for third-party integrations:
- Job submission and management
- Model management
- Queue operations
- Metrics and monitoring
- Webhook support
- API key authentication
- Rate limiting
"""

import asyncio
import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from uuid import uuid4

import aiohttp
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# Security
security = HTTPBearer(auto_error=False)


@dataclass
class APIKey:
    """API key for authentication."""

    key_id: str
    key_hash: str
    name: str
    created_at: float
    expires_at: float | None = None
    scopes: list[str] = field(default_factory=lambda: ["read", "write"])
    rate_limit: int = 100  # requests per minute
    is_active: bool = True
    last_used: float | None = None
    usage_count: int = 0


@dataclass
class RateLimitInfo:
    """Rate limit tracking per client."""

    requests: list[float] = field(default_factory=list)
    limit: int = 100
    window: float = 60.0  # seconds

    def is_allowed(self) -> bool:
        """Check if request is within rate limit."""
        now = time.time()
        cutoff = now - self.window
        self.requests = [r for r in self.requests if r > cutoff]
        return len(self.requests) < self.limit

    def add_request(self) -> None:
        """Record a new request."""
        self.requests.append(time.time())

    @property
    def remaining(self) -> int:
        """Remaining requests in current window."""
        now = time.time()
        cutoff = now - self.window
        self.requests = [r for r in self.requests if r > cutoff]
        return max(0, self.limit - len(self.requests))


@dataclass
class WebhookConfig:
    """Webhook configuration for event notifications."""

    url: str
    events: list[str]  # job.completed, job.failed, queue.full, etc.
    secret: str | None = None  # For HMAC signature
    headers: dict[str, str] = field(default_factory=dict)
    is_active: bool = True
    retry_count: int = 3
    timeout: float = 30.0
    created_at: float = field(default_factory=time.time)


class APIKeyManager:
    """Manages API keys for authentication."""

    def __init__(self, keys_file: Path | None = None):
        self.keys_file = keys_file or Path("config/api_keys.json")
        self._keys: dict[str, APIKey] = {}
        self._key_hashes: dict[str, str] = {}  # hash -> key_id
        self._lock = asyncio.Lock()
        self._load_keys()

    def _load_keys(self) -> None:
        """Load API keys from file."""
        if self.keys_file.exists():
            try:
                with open(self.keys_file) as f:
                    data = json.load(f)
                for key_data in data.get("keys", []):
                    key = APIKey(**key_data)
                    self._keys[key.key_id] = key
                    self._key_hashes[key.key_hash] = key.key_id
            except Exception as e:
                logger.warning(f"Failed to load API keys: {e}")

    async def _save_keys(self) -> None:
        """Save API keys to file."""
        async with self._lock:
            data = {
                "keys": [
                    {
                        "key_id": k.key_id,
                        "key_hash": k.key_hash,
                        "name": k.name,
                        "created_at": k.created_at,
                        "expires_at": k.expires_at,
                        "scopes": k.scopes,
                        "rate_limit": k.rate_limit,
                        "is_active": k.is_active,
                        "last_used": k.last_used,
                        "usage_count": k.usage_count,
                    }
                    for k in self._keys.values()
                ]
            }
            self.keys_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.keys_file, "w") as f:
                json.dump(data, f, indent=2)

    async def create_key(
        self,
        name: str,
        scopes: list[str] = None,
        rate_limit: int = 100,
        expires_in_days: int | None = None,
    ) -> tuple[str, str]:
        """Create a new API key.

        Returns:
            Tuple of (key_id, plain_key) - plain_key is shown only once
        """
        key_id = str(uuid4())[:8]
        plain_key = f"ce_{uuid4().hex}"
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()

        expires_at = None
        if expires_in_days:
            expires_at = time.time() + expires_in_days * 86400

        key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            created_at=time.time(),
            expires_at=expires_at,
            scopes=scopes or ["read", "write"],
            rate_limit=rate_limit,
        )

        async with self._lock:
            self._keys[key_id] = key
            self._key_hashes[key_hash] = key_id

        await self._save_keys()

        logger.info(f"Created API key: {name} ({key_id})")
        return key_id, plain_key

    async def validate_key(self, plain_key: str) -> APIKey | None:
        """Validate an API key and return its info."""
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()

        key_id = self._key_hashes.get(key_hash)
        if not key_id:
            return None

        key = self._keys.get(key_id)
        if not key or not key.is_active:
            return None

        # Check expiration
        if key.expires_at and time.time() > key.expires_at:
            return None

        # Update usage
        key.last_used = time.time()
        key.usage_count += 1

        return key

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        async with self._lock:
            key = self._keys.get(key_id)
            if not key:
                return False

            key.is_active = False
            del self._key_hashes[key.key_hash]

        await self._save_keys()
        logger.info(f"Revoked API key: {key_id}")
        return True

    async def list_keys(self) -> list[dict[str, Any]]:
        """List all API keys (without hashes)."""
        return [
            {
                "key_id": k.key_id,
                "name": k.name,
                "created_at": k.created_at,
                "expires_at": k.expires_at,
                "scopes": k.scopes,
                "rate_limit": k.rate_limit,
                "is_active": k.is_active,
                "last_used": k.last_used,
                "usage_count": k.usage_count,
            }
            for k in self._keys.values()
        ]


    async def shutdown(self) -> None:
        """FIX #4: Gracefully stop webhook delivery. Was missing before."""
        self._active = False

class RateLimiter:
    """Rate limiter for API requests."""

    def __init__(self):
        self._limits: dict[str, RateLimitInfo] = {}
        self._lock = asyncio.Lock()

    async def check_rate_limit(self, key_id: str, limit: int = 100) -> tuple[bool, int, int]:
        """Check if request is within rate limit.

        Returns:
            Tuple of (allowed, remaining, reset_after_seconds)
        """
        async with self._lock:
            if key_id not in self._limits:
                self._limits[key_id] = RateLimitInfo(limit=limit)

            info = self._limits[key_id]
            info.limit = limit  # Update if changed

            allowed = info.is_allowed()
            remaining = info.remaining

            if allowed:
                info.add_request()

            # Calculate reset time
            if info.requests:
                reset_after = int(info.window - (time.time() - min(info.requests)))
            else:
                reset_after = 0

            return allowed, remaining, max(0, reset_after)


class WebhookManager:
    """Manages webhooks for event notifications."""

    def __init__(self, webhooks_file: Path | None = None):
        self.webhooks_file = webhooks_file or Path("config/webhooks.json")
        self._webhooks: dict[str, WebhookConfig] = {}
        self._active = True
        self._load_webhooks()

    def _load_webhooks(self) -> None:
        """Load webhooks from file."""
        if self.webhooks_file.exists():
            try:
                with open(self.webhooks_file) as f:
                    data = json.load(f)
                for wh_data in data.get("webhooks", []):
                    wh = WebhookConfig(**wh_data)
                    self._webhooks[wh.url] = wh
            except Exception as e:
                logger.warning(f"Failed to load webhooks: {e}")

    async def _save_webhooks(self) -> None:
        """Save webhooks to file."""
        data = {
            "webhooks": [
                {
                    "url": w.url,
                    "events": w.events,
                    "secret": w.secret,
                    "headers": w.headers,
                    "is_active": w.is_active,
                    "retry_count": w.retry_count,
                    "timeout": w.timeout,
                    "created_at": w.created_at,
                }
                for w in self._webhooks.values()
            ]
        }
        self.webhooks_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.webhooks_file, "w") as f:
            json.dump(data, f, indent=2)

    async def register(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Register a new webhook."""
        webhook_id = hashlib.sha256(url.encode()).hexdigest()[:12]

        self._webhooks[webhook_id] = WebhookConfig(
            url=url,
            events=events,
            secret=secret,
            headers=headers or {},
        )

        await self._save_webhooks()
        logger.info(f"Registered webhook: {url} for events: {events}")

        return webhook_id

    async def unregister(self, webhook_id: str) -> bool:
        """Unregister a webhook."""
        if webhook_id not in self._webhooks:
            return False

        del self._webhooks[webhook_id]
        await self._save_webhooks()
        logger.info(f"Unregistered webhook: {webhook_id}")
        return True

    async def fire_event(self, event: str, payload: dict[str, Any]) -> None:
        """Fire an event to all registered webhooks."""
        tasks = []

        for webhook in self._webhooks.values():
            if not webhook.is_active or event not in webhook.events:
                continue

            tasks.append(self._send_webhook(webhook, event, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(
        self,
        webhook: WebhookConfig,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """Send webhook notification with retry."""
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event,
            "X-Webhook-Timestamp": str(int(time.time())),
            **webhook.headers,
        }

        # Add HMAC signature if secret is configured
        if webhook.secret:
            signature = self._sign_payload(payload, webhook.secret)
            headers["X-Webhook-Signature"] = signature

        body = json.dumps(payload)

        for attempt in range(webhook.retry_count):
            try:
                async with (
                    aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=webhook.timeout)) as session,
                    session.post(
                        webhook.url,
                        data=body,
                        headers=headers,
                    ) as response,
                ):
                    if response.status < 400:
                        logger.debug(f"Webhook delivered: {webhook.url}")
                        return
                    else:
                        logger.warning(f"Webhook failed: {webhook.url} ({response.status})")

            except Exception as e:
                logger.warning(f"Webhook error: {webhook.url} - {e}")

            if attempt < webhook.retry_count - 1:
                await asyncio.sleep(2**attempt)  # Exponential backoff

        logger.error(f"Webhook permanently failed: {webhook.url}")

    @staticmethod
    def _sign_payload(payload: dict[str, Any], secret: str) -> str:
        """Sign payload with HMAC for webhook verification."""
        import hmac

        body = json.dumps(payload, sort_keys=True)
        signature = hmac.new(
            secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()

        return f"sha256={signature}"


class RESTAPIServer:
    """FastAPI-based REST API server for ComfyUI Engine."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        engine: Any | None = None,
        enable_auth: bool = True,
        enable_cors: bool = True,
    ):
        self.host = host
        self.port = port
        self.engine = engine
        self.enable_auth = enable_auth
        self.enable_cors = enable_cors

        self.app = FastAPI(
            title="ComfyUI Engine API",
            description="REST API for ComfyUI Engine external integrations",
            version="5.1.0",
            docs_url="/docs",
            redoc_url="/redoc",
        )

        self.key_manager = APIKeyManager()
        self.rate_limiter = RateLimiter()
        self.webhook_manager = WebhookManager()

        self._setup_middleware()
        self._setup_routes()

    def _setup_middleware(self) -> None:
        """Configure API middleware."""
        if self.enable_cors:
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=self._cors_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        self.app.add_middleware(GZipMiddleware, minimum_size=1000)

    def _setup_routes(self) -> None:
        """Configure API routes."""

        async def get_api_key(
            credentials: HTTPAuthorizationCredentials = Depends(security),
        ) -> APIKey | None:
            """Dependency to validate API key."""
            if not self.enable_auth:
                return None

            if not credentials:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key required",
                )

            key = await self.key_manager.validate_key(credentials.credentials)
            if not key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                )

            # Check rate limit
            allowed, remaining, reset_after = await self.rate_limiter.check_rate_limit(
                key.key_id,
                key.rate_limit,
            )

            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                    headers={
                        "X-RateLimit-Limit": str(key.rate_limit),
                        "X-RateLimit-Remaining": str(remaining),
                        "X-RateLimit-Reset": str(reset_after),
                    },
                )

            return key

        # Health check (liveness - no auth required)
        @self.app.get("/health", tags=["System"])
        async def health_check():
            return {
                "status": "healthy",
                "version": "4.0.0",
                "timestamp": time.time(),
            }

        # Readiness check (readiness - no auth required)
        @self.app.get("/ready", tags=["System"])
        async def readiness_check():
            checks = {
                "api": True,
                "engine_connected": self.engine is not None,
            }

            # Check engine health if connected
            if self.engine and hasattr(self.engine, "health_check"):
                try:
                    checks["engine_healthy"] = await self.engine.health_check()
                except Exception:
                    checks["engine_healthy"] = False

            all_ready = all(checks.values())
            status_code = 200 if all_ready else 503

            return JSONResponse(
                status_code=status_code,
                content={
                    "status": "ready" if all_ready else "not_ready",
                    "checks": checks,
                    "timestamp": time.time(),
                },
            )

        # Liveness check (no auth required)
        @self.app.get("/live", tags=["System"])
        async def liveness_check():
            return {
                "status": "alive",
                "timestamp": time.time(),
            }

        # Metrics endpoint (no auth required for Prometheus scraping)
        @self.app.get("/metrics", tags=["System"])
        async def metrics_check():
            """Prometheus-compatible metrics endpoint."""
            metrics_lines = []

            # Basic info
            metrics_lines.append(f"# HELP comfyui_engine_info Engine version info")
            metrics_lines.append(f"# TYPE comfyui_engine_info gauge")
            metrics_lines.append(f'comfyui_engine_info{{version="5.1.0"}} 1')

            # Uptime
            metrics_lines.append(f"# HELP comfyui_engine_uptime_seconds Engine uptime")
            metrics_lines.append(f"# TYPE comfyui_engine_uptime_seconds counter")
            metrics_lines.append(
                f'comfyui_engine_uptime_seconds {time.time() - getattr(self, "_start_time", time.time())}'
            )

            # Engine metrics if available
            if self.engine and hasattr(self.engine, "get_metrics"):
                try:
                    engine_metrics = await self.engine.get_metrics()
                    for key, value in engine_metrics.items():
                        if isinstance(value, int | float):
                            metric_name = f"comfyui_engine_{key}"
                            metrics_lines.append(f"# HELP {metric_name} {key}")
                            metrics_lines.append(f"# TYPE {metric_name} gauge")
                            metrics_lines.append(f"{metric_name} {value}")
                except Exception:
                    pass

            return StreamingResponse(
                content=iter("\n".join(metrics_lines) + "\n"),
                media_type="text/plain",
            )

        # Graceful shutdown endpoint (admin only)
        @self.app.post("/shutdown", tags=["System"])
        async def graceful_shutdown(key: APIKey = Depends(get_api_key)):
            """Initiate graceful shutdown."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            # Signal shutdown
            asyncio.create_task(self._shutdown())

            return {
                "status": "shutting_down",
                "message": "Server will shutdown after active requests complete",
                "timestamp": time.time(),
            }

        # Job management
        @self.app.post("/api/v1/jobs", tags=["Jobs"], status_code=status.HTTP_202_ACCEPTED)
        async def submit_job(
            request: Request,
            job_data: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Submit a new generation job."""
            job_id = str(uuid4())[:12]

            # Store job
            if self.engine and hasattr(self.engine, "submit_job"):
                job_id = await self.engine.submit_job(job_data)

            # Fire webhook
            await self.webhook_manager.fire_event(
                "job.created",
                {"job_id": job_id, "status": "pending"},
            )

            return {
                "job_id": job_id,
                "status": "pending",
                "estimated_wait": 0,  # Could calculate from queue
            }

        @self.app.get("/api/v1/jobs/{job_id}", tags=["Jobs"])
        async def get_job_status(
            job_id: str,
            key: APIKey = Depends(get_api_key),
        ):
            """Get job status and results."""
            if self.engine and hasattr(self.engine, "get_job_status"):
                status = await self.engine.get_job_status(job_id)
                if status:
                    return status

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        @self.app.get("/api/v1/jobs", tags=["Jobs"])
        async def list_jobs(
            status: str | None = None,
            limit: int = 100,
            offset: int = 0,
            key: APIKey = Depends(get_api_key),
        ):
            """List jobs with optional filtering."""
            jobs = []

            if self.engine and hasattr(self.engine, "list_jobs"):
                jobs = await self.engine.list_jobs(status, limit, offset)

            return {
                "jobs": jobs,
                "total": len(jobs),
                "limit": limit,
                "offset": offset,
            }

        @self.app.delete("/api/v1/jobs/{job_id}", tags=["Jobs"])
        async def cancel_job(
            job_id: str,
            key: APIKey = Depends(get_api_key),
        ):
            """Cancel a pending or running job."""
            if self.engine and hasattr(self.engine, "cancel_job"):
                success = await self.engine.cancel_job(job_id)
                if success:
                    await self.webhook_manager.fire_event(
                        "job.cancelled",
                        {"job_id": job_id},
                    )
                    return {"status": "cancelled"}

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found or cannot be cancelled",
            )

        # Queue management
        @self.app.get("/api/v1/queue", tags=["Queue"])
        async def get_queue_status(key: APIKey = Depends(get_api_key)):
            """Get current queue status."""
            if self.engine and hasattr(self.engine, "get_queue_status"):
                return await self.engine.get_queue_status()

            return {"length": 0, "pending": 0, "running": 0}

        @self.app.post("/api/v1/queue/pause", tags=["Queue"])
        async def pause_queue(key: APIKey = Depends(get_api_key)):
            """Pause job processing."""
            if self.engine and hasattr(self.engine, "pause_queue"):
                await self.engine.pause_queue()
            return {"status": "paused"}

        @self.app.post("/api/v1/queue/resume", tags=["Queue"])
        async def resume_queue(key: APIKey = Depends(get_api_key)):
            """Resume job processing."""
            if self.engine and hasattr(self.engine, "resume_queue"):
                await self.engine.resume_queue()
            return {"status": "resumed"}

        # Model management
        @self.app.get("/api/v1/models", tags=["Models"])
        async def list_models(key: APIKey = Depends(get_api_key)):
            """List available models."""
            if self.engine and hasattr(self.engine, "list_models"):
                return await self.engine.list_models()
            return {"models": []}

        @self.app.post("/api/v1/models/{model_name}/preload", tags=["Models"])
        async def preload_model(
            model_name: str,
            key: APIKey = Depends(get_api_key),
        ):
            """Preload a model into memory."""
            if self.engine and hasattr(self.engine, "preload_model"):
                success = await self.engine.preload_model(model_name)
                return {"model": model_name, "loaded": success}

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Model preloading not available",
            )

        # Metrics
        @self.app.get("/api/v1/metrics", tags=["Metrics"])
        async def get_metrics(key: APIKey = Depends(get_api_key)):
            """Get engine metrics."""
            if self.engine and hasattr(self.engine, "get_metrics"):
                return await self.engine.get_metrics()

            return {
                "jobs_completed": 0,
                "jobs_failed": 0,
                "avg_generation_time": 0,
                "queue_length": 0,
            }

        @self.app.get("/api/v1/metrics/prometheus", tags=["Metrics"])
        async def get_prometheus_metrics():
            """Get metrics in Prometheus format (no auth required)."""
            # This endpoint is typically scraped by Prometheus
            # and doesn't require authentication
            metrics = []

            if self.engine and hasattr(self.engine, "get_prometheus_metrics"):
                metrics = await self.engine.get_prometheus_metrics()

            return StreamingResponse(
                content=iter(metrics),
                media_type="text/plain",
            )

        # Webhook management
        @self.app.post("/api/v1/webhooks", tags=["Webhooks"])
        async def register_webhook(
            config: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Register a new webhook."""
            webhook_id = await self.webhook_manager.register(
                url=config["url"],
                events=config["events"],
                secret=config.get("secret"),
                headers=config.get("headers"),
            )
            return {"webhook_id": webhook_id, "status": "registered"}

        @self.app.delete("/api/v1/webhooks/{webhook_id}", tags=["Webhooks"])
        async def unregister_webhook(
            webhook_id: str,
            key: APIKey = Depends(get_api_key),
        ):
            """Unregister a webhook."""
            success = await self.webhook_manager.unregister(webhook_id)
            if success:
                return {"status": "unregistered"}

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Webhook {webhook_id} not found",
            )

        @self.app.get("/api/v1/webhooks", tags=["Webhooks"])
        async def list_webhooks(key: APIKey = Depends(get_api_key)):
            """List registered webhooks."""
            return {
                "webhooks": [
                    {
                        "url": w.url,
                        "events": w.events,
                        "is_active": w.is_active,
                    }
                    for w in self.webhook_manager._webhooks.values()
                ]
            }

        # API Key management (admin only)
        @self.app.post("/api/v1/keys", tags=["Admin"])
        async def create_api_key(
            config: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Create a new API key."""
            # Check if current key has admin scope
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            key_id, plain_key = await self.key_manager.create_key(
                name=config["name"],
                scopes=config.get("scopes", ["read"]),
                rate_limit=config.get("rate_limit", 100),
                expires_in_days=config.get("expires_in_days"),
            )

            return {
                "key_id": key_id,
                "key": plain_key,  # Shown only once!
                "name": config["name"],
            }

        @self.app.delete("/api/v1/keys/{key_id}", tags=["Admin"])
        async def revoke_api_key(
            key_id: str,
            key: APIKey = Depends(get_api_key),
        ):
            """Revoke an API key."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            success = await self.key_manager.revoke_key(key_id)
            if success:
                return {"status": "revoked"}

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Key {key_id} not found",
            )

        @self.app.get("/api/v1/keys", tags=["Admin"])
        async def list_api_keys(key: APIKey = Depends(get_api_key)):
            """List all API keys."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            keys = await self.key_manager.list_keys()
            return {"keys": keys}

        # Kiro Protocol v3.0 Enhanced Engine Management
        @self.app.post("/api/v1/engine/gc-tuner", tags=["Engine"])
        async def configure_gc_tuner(
            config: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Configure garbage collection tuning."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            if self.engine and hasattr(self.engine, "configure_gc_tuner"):
                await self.engine.configure_gc_tuner(config)
                return {"status": "configured"}

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="GC tuner configuration not available",
            )

        @self.app.get("/api/v1/engine/gc-stats", tags=["Engine"])
        async def get_gc_stats(key: APIKey = Depends(get_api_key)):
            """Get garbage collection statistics."""
            if self.engine and hasattr(self.engine, "get_gc_stats"):
                stats = await self.engine.get_gc_stats()
                return stats

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="GC stats not available",
            )

        @self.app.post("/api/v1/engine/retry-policy", tags=["Engine"])
        async def configure_retry_policy(
            policy: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Configure advanced retry policies."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            if self.engine and hasattr(self.engine, "configure_retry_policy"):
                await self.engine.configure_retry_policy(policy)
                return {"status": "configured"}

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Retry policy configuration not available",
            )

        @self.app.post("/api/v1/engine/tracing", tags=["Engine"])
        async def initialize_tracing(
            config: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Initialize OpenTelemetry tracing."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            if self.engine and hasattr(self.engine, "initialize_tracing"):
                await self.engine.initialize_tracing(config)
                return {"status": "initialized"}

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Tracing initialization not available",
            )

        @self.app.get("/api/v1/engine/trace-context", tags=["Engine"])
        async def get_trace_context(key: APIKey = Depends(get_api_key)):
            """Get current trace context."""
            if self.engine and hasattr(self.engine, "get_trace_context"):
                context = await self.engine.get_trace_context()
                return context

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Trace context not available",
            )

        @self.app.post("/api/v1/engine/gpu-optimization", tags=["Engine"])
        async def configure_gpu_optimization(
            config: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Configure GPU-specific optimizations."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            if self.engine and hasattr(self.engine, "configure_gpu_optimization"):
                await self.engine.configure_gpu_optimization(config)
                return {"status": "configured"}

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="GPU optimization configuration not available",
            )

        @self.app.get("/api/v1/engine/gpu-stats", tags=["Engine"])
        async def get_gpu_stats(key: APIKey = Depends(get_api_key)):
            """Get GPU utilization and memory statistics."""
            if self.engine and hasattr(self.engine, "get_gpu_stats"):
                stats = await self.engine.get_gpu_stats()
                return stats

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="GPU stats not available",
            )

        @self.app.post("/api/v1/engine/batching", tags=["Engine"])
        async def enable_advanced_batching(
            config: dict[str, Any],
            key: APIKey = Depends(get_api_key),
        ):
            """Enable or disable advanced batching optimizations."""
            if "admin" not in key.scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin scope required",
                )

            enabled = config.get("enabled", True)
            if self.engine and hasattr(self.engine, "enable_advanced_batching"):
                await self.engine.enable_advanced_batching(enabled)
                return {"status": "updated", "enabled": enabled}

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Advanced batching configuration not available",
            )

        @self.app.get("/api/v1/engine/batch-stats", tags=["Engine"])
        async def get_batch_stats(key: APIKey = Depends(get_api_key)):
            """Get batching statistics."""
            if self.engine and hasattr(self.engine, "get_batch_stats"):
                stats = await self.engine.get_batch_stats()
                return stats

            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Batch stats not available",
            )

    async def _shutdown(self) -> None:
        """Graceful shutdown with connection draining."""
        logger.info("Initiating graceful shutdown...")

        # Signal shutdown to engine
        if self.engine and hasattr(self.engine, "shutdown"):
            await self.engine.shutdown()

        # Cancel pending webhooks
        await self.webhook_manager.shutdown()

        logger.info("Graceful shutdown complete")

    async def start(self) -> None:
        """Start the API server with graceful shutdown support."""
        import uvicorn

        self._start_time = time.time()

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        logger.info(f"REST API server starting on http://{self.host}:{self.port}")
        await server.serve()

    async def fire_webhook(self, event: str, payload: dict[str, Any]) -> None:
        """Fire a webhook event."""
        await self.webhook_manager.fire_event(event, payload)


# Example usage
async def create_api_server(
    engine: Any | None = None,
    host: str = "0.0.0.0",
    port: int = 8000,
) -> RESTAPIServer:
    """Factory function to create and start API server."""
    server = RESTAPIServer(engine=engine, host=host, port=port)
    return server


if __name__ == "__main__":
    server = RESTAPIServer()
    asyncio.run(server.start())
