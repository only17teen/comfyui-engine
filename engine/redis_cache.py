"""Redis-based caching layer for ComfyUI Engine.

Provides caching for:
- Model metadata and checkpoints
- Prompt templates and embeddings
- Workflow results
- Session data
- Rate limiting counters
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pickle
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, Generic, Optional, TypeVar, Union

import redis.asyncio as redis
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class CacheConfig:
    """Cache configuration."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None
    ssl: bool = False
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
    max_connections: int = 50
    default_ttl: int = 3600
    key_prefix: str = "comfyui:"


class CacheKeyBuilder:
    """Builds cache keys with proper namespacing."""

    def __init__(self, prefix: str = "comfyui:") -> None:
        self.prefix = prefix

    def build(self, namespace: str, *parts: str) -> str:
        """Build a cache key."""
        key = f"{self.prefix}{namespace}"
        for part in parts:
            key = f"{key}:{part}"
        return key

    def hash_key(self, namespace: str, data: str | bytes | dict) -> str:
        """Build a hash-based cache key."""
        if isinstance(data, dict):
            data = json.dumps(data, sort_keys=True)
        if isinstance(data, str):
            data = data.encode()
        hash_val = hashlib.sha256(data).hexdigest()[:16]
        return f"{self.prefix}{namespace}:{hash_val}"


class RedisCache(Generic[T]):
    """Generic Redis cache with type safety."""

    def __init__(
        self,
        redis_client: Redis,
        key_builder: CacheKeyBuilder,
        default_ttl: int = 3600,
    ) -> None:
        self.redis = redis_client
        self.key_builder = key_builder
        self.default_ttl = default_ttl
        self._serializer = pickle

    async def get(self, key: str) -> T | None:
        """Get value from cache."""
        try:
            data = await self.redis.get(key)
            if data is None:
                return None
            return self._serializer.loads(data)
        except Exception as e:
            logger.warning(f"Cache get error for {key}: {e}")
            return None

    async def set(
        self,
        key: str,
        value: T,
        ttl: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool:
        """Set value in cache."""
        try:
            data = self._serializer.dumps(value)
            ttl = ttl or self.default_ttl
            return await self.redis.set(key, data, ex=ttl, nx=nx, xx=xx)
        except Exception as e:
            logger.warning(f"Cache set error for {key}: {e}")
            return False

    async def delete(self, key: str) -> int:
        """Delete value from cache."""
        try:
            return await self.redis.delete(key)
        except Exception as e:
            logger.warning(f"Cache delete error for {key}: {e}")
            return 0

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            return await self.redis.exists(key) > 0
        except Exception as e:
            logger.warning(f"Cache exists error for {key}: {e}")
            return False

    async def ttl(self, key: str) -> int:
        """Get remaining TTL."""
        try:
            return await self.redis.ttl(key)
        except Exception as e:
            logger.warning(f"Cache ttl error for {key}: {e}")
            return -2

    async def expire(self, key: str, seconds: int) -> bool:
        """Set expiration on key."""
        try:
            return await self.redis.expire(key, seconds)
        except Exception as e:
            logger.warning(f"Cache expire error for {key}: {e}")
            return False

    async def get_or_set(
        self,
        key: str,
        factory: callable,
        ttl: int | None = None,
    ) -> T:
        """Get from cache or compute and store."""
        value = await self.get(key)
        if value is not None:
            return value

        value = await factory()
        if value is not None:
            await self.set(key, value, ttl)
        return value

    async def increment(self, key: str, amount: int = 1) -> int:
        """Atomic increment."""
        try:
            return await self.redis.incrby(key, amount)
        except Exception as e:
            logger.warning(f"Cache increment error for {key}: {e}")
            return 0

    async def decrement(self, key: str, amount: int = 1) -> int:
        """Atomic decrement."""
        try:
            return await self.redis.decrby(key, amount)
        except Exception as e:
            logger.warning(f"Cache decrement error for {key}: {e}")
            return 0

    async def flush_namespace(self, namespace: str) -> int:
        """Delete all keys in namespace."""
        try:
            pattern = f"{self.key_builder.prefix}{namespace}:*"
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(key)
            if keys:
                return await self.redis.delete(*keys)
            return 0
        except Exception as e:
            logger.warning(f"Cache flush error for {namespace}: {e}")
            return 0


class ModelCache:
    """Cache for model metadata and checkpoints."""

    def __init__(self, cache: RedisCache[Any]) -> None:
        self.cache = cache
        self.namespace = "models"

    def _key(self, model_name: str) -> str:
        return self.cache.key_builder.build(self.namespace, model_name)

    async def get_model_info(self, model_name: str) -> dict | None:
        """Get cached model info."""
        return await self.cache.get(self._key(model_name))

    async def set_model_info(
        self, model_name: str, info: dict, ttl: int = 86400
    ) -> bool:
        """Cache model info."""
        return await self.cache.set(self._key(model_name), info, ttl)

    async def invalidate_model(self, model_name: str) -> int:
        """Invalidate model cache."""
        return await self.cache.delete(self._key(model_name))

    async def list_cached_models(self) -> list[str]:
        """List all cached model names."""
        pattern = self.cache.key_builder.build(self.namespace, "*")
        keys = []
        async for key in self.cache.redis.scan_iter(match=pattern):
            keys.append(key.decode().split(":")[-1])
        return keys


class PromptCache:
    """Cache for prompt templates and embeddings."""

    def __init__(self, cache: RedisCache[Any]) -> None:
        self.cache = cache
        self.namespace = "prompts"

    def _key(self, prompt_hash: str) -> str:
        return self.cache.key_builder.build(self.namespace, prompt_hash)

    def _hash(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode()).hexdigest()[:16]

    async def get_embedding(self, prompt: str) -> list[float] | None:
        """Get cached embedding for prompt."""
        key = self._key(self._hash(prompt))
        return await self.cache.get(key)

    async def set_embedding(
        self,
        prompt: str,
        embedding: list[float],
        ttl: int = 86400,
    ) -> bool:
        """Cache embedding for prompt."""
        key = self._key(self._hash(prompt))
        return await self.cache.set(key, embedding, ttl)

    async def get_template(self, template_id: str) -> dict | None:
        """Get cached prompt template."""
        key = self.cache.key_builder.build(self.namespace, "template", template_id)
        return await self.cache.get(key)

    async def set_template(
        self, template_id: str, template: dict, ttl: int = 3600
    ) -> bool:
        """Cache prompt template."""
        key = self.cache.key_builder.build(self.namespace, "template", template_id)
        return await self.cache.set(key, template, ttl)


class WorkflowResultCache:
    """Cache for workflow execution results."""

    def __init__(self, cache: RedisCache[Any]) -> None:
        self.cache = cache
        self.namespace = "results"

    def _key(self, workflow_hash: str) -> str:
        return self.cache.key_builder.build(self.namespace, workflow_hash)

    def _hash_workflow(self, workflow: dict, seed: int) -> str:
        """Hash workflow with seed for deterministic caching."""
        data = json.dumps(workflow, sort_keys=True) + str(seed)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    async def get_result(self, workflow: dict, seed: int) -> dict | None:
        """Get cached workflow result."""
        key = self._key(self._hash_workflow(workflow, seed))
        return await self.cache.get(key)

    async def set_result(
        self,
        workflow: dict,
        seed: int,
        result: dict,
        ttl: int = 3600,
    ) -> bool:
        """Cache workflow result."""
        key = self._key(self._hash_workflow(workflow, seed))
        return await self.cache.set(key, result, ttl)

    async def invalidate_result(self, workflow: dict, seed: int) -> int:
        """Invalidate cached result."""
        key = self._key(self._hash_workflow(workflow, seed))
        return await self.cache.delete(key)


class SessionCache:
    """Cache for session data."""

    def __init__(self, cache: RedisCache[Any]) -> None:
        self.cache = cache
        self.namespace = "sessions"

    def _key(self, session_id: str) -> str:
        return self.cache.key_builder.build(self.namespace, session_id)

    async def get_session(self, session_id: str) -> dict | None:
        """Get cached session data."""
        return await self.cache.get(self._key(session_id))

    async def set_session(
        self,
        session_id: str,
        data: dict,
        ttl: int = 3600,
    ) -> bool:
        """Cache session data."""
        return await self.cache.set(self._key(session_id), data, ttl)

    async def delete_session(self, session_id: str) -> int:
        """Delete session from cache."""
        return await self.cache.delete(self._key(session_id))

    async def extend_session(self, session_id: str, ttl: int = 3600) -> bool:
        """Extend session TTL."""
        return await self.cache.expire(self._key(session_id), ttl)


class RateLimiter:
    """Redis-based rate limiter."""

    def __init__(self, cache: RedisCache[Any]) -> None:
        self.cache = cache
        self.namespace = "ratelimit"

    def _key(self, identifier: str, window: str) -> str:
        return self.cache.key_builder.build(self.namespace, identifier, window)

    async def is_allowed(
        self,
        identifier: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """Check if request is allowed.

        Returns:
            Tuple of (allowed, remaining, reset_after)
        """
        key = self._key(identifier, str(window_seconds))
        now = asyncio.get_event_loop().time()
        window_start = int(now // window_seconds) * window_seconds
        window_key = f"{key}:{window_start}"

        try:
            pipe = self.cache.redis.pipeline()
            pipe.incr(window_key)
            pipe.expire(window_key, window_seconds)
            results = await pipe.execute()
            current_count = results[0]

            allowed = current_count <= max_requests
            remaining = max(0, max_requests - current_count)
            reset_after = window_seconds - int(now % window_seconds)

            return allowed, remaining, reset_after
        except Exception as e:
            logger.warning(f"Rate limit check error: {e}")
            return True, max_requests, window_seconds

    async def get_current_count(self, identifier: str, window_seconds: int) -> int:
        """Get current request count in window."""
        key = self._key(identifier, str(window_seconds))
        now = asyncio.get_event_loop().time()
        window_start = int(now // window_seconds) * window_seconds
        window_key = f"{key}:{window_start}"

        try:
            count = await self.cache.redis.get(window_key)
            return int(count) if count else 0
        except Exception as e:
            logger.warning(f"Rate limit count error: {e}")
            return 0


class CacheManager:
    """Central cache manager for all caching needs."""

    def __init__(self, config: CacheConfig | None = None) -> None:
        self.config = config or CacheConfig()
        self._redis: Redis | None = None
        self._cache: RedisCache[Any] | None = None
        self._model_cache: ModelCache | None = None
        self._prompt_cache: PromptCache | None = None
        self._result_cache: WorkflowResultCache | None = None
        self._session_cache: SessionCache | None = None
        self._rate_limiter: RateLimiter | None = None
        self._key_builder = CacheKeyBuilder(self.config.key_prefix)

    async def connect(self) -> None:
        """Connect to Redis."""
        if self._redis is not None:
            return

        self._redis = redis.Redis(
            host=self.config.host,
            port=self.config.port,
            db=self.config.db,
            password=self.config.password,
            ssl=self.config.ssl,
            socket_timeout=self.config.socket_timeout,
            socket_connect_timeout=self.config.socket_connect_timeout,
            max_connections=self.config.max_connections,
            decode_responses=False,
        )

        # Test connection
        await self._redis.ping()
        logger.info(f"Connected to Redis at {self.config.host}:{self.config.port}")

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
            logger.info("Disconnected from Redis")

    @property
    def redis(self) -> Redis:
        """Get Redis client."""
        if self._redis is None:
            raise RuntimeError("Cache not connected. Call connect() first.")
        return self._redis

    @property
    def cache(self) -> RedisCache[Any]:
        """Get generic cache."""
        if self._cache is None:
            self._cache = RedisCache(
                self.redis, self._key_builder, self.config.default_ttl
            )
        return self._cache

    @property
    def models(self) -> ModelCache:
        """Get model cache."""
        if self._model_cache is None:
            self._model_cache = ModelCache(self.cache)
        return self._model_cache

    @property
    def prompts(self) -> PromptCache:
        """Get prompt cache."""
        if self._prompt_cache is None:
            self._prompt_cache = PromptCache(self.cache)
        return self._prompt_cache

    @property
    def results(self) -> WorkflowResultCache:
        """Get workflow result cache."""
        if self._result_cache is None:
            self._result_cache = WorkflowResultCache(self.cache)
        return self._result_cache

    @property
    def sessions(self) -> SessionCache:
        """Get session cache."""
        if self._session_cache is None:
            self._session_cache = SessionCache(self.cache)
        return self._session_cache

    @property
    def rate_limiter(self) -> RateLimiter:
        """Get rate limiter."""
        if self._rate_limiter is None:
            self._rate_limiter = RateLimiter(self.cache)
        return self._rate_limiter

    async def health_check(self) -> dict:
        """Check cache health."""
        try:
            start = asyncio.get_event_loop().time()
            await self.redis.ping()
            latency = (asyncio.get_event_loop().time() - start) * 1000

            info = await self.redis.info()
            return {
                "status": "healthy",
                "latency_ms": round(latency, 2),
                "redis_version": info.get("redis_version", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "used_memory_human": info.get("used_memory_human", "unknown"),
                "total_keys": await self.redis.dbsize(),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }

    async def clear_all(self) -> int:
        """Clear all cache data."""
        pattern = f"{self.config.key_prefix}*"
        keys = []
        async for key in self.redis.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            return await self.redis.delete(*keys)
        return 0


@asynccontextmanager
async def cache_context(config: CacheConfig | None = None):
    """Async context manager for cache operations."""
    manager = CacheManager(config)
    try:
        await manager.connect()
        yield manager
    finally:
        await manager.disconnect()


async def get_cache_manager() -> CacheManager:
    """Get or create cache manager from settings."""
    settings = get_settings()
    config = CacheConfig(
        host=settings.redis_host or "localhost",
        port=settings.redis_port or 6379,
        password=settings.redis_password,
        ssl=settings.redis_ssl or False,
        default_ttl=settings.cache_ttl or 3600,
        key_prefix=settings.cache_prefix or "comfyui:",
    )
    manager = CacheManager(config)
    await manager.connect()
    return manager
