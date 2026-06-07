"""Automatic model warmup and intelligent cache management.

Pre-loads models into GPU memory before generation requests,
manages model cache with LRU eviction, and optimizes memory
usage across concurrent generation jobs.
"""

import asyncio
import gc
import logging
import os
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import psutil

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    """Information about a cached model."""

    name: str
    path: Path
    model_type: str  # "checkpoint", "lora", "vae", "controlnet", "clip", etc.
    size_bytes: int = 0
    loaded: bool = False
    load_time_ms: float = 0.0
    last_used: float = field(default_factory=time.time)
    use_count: int = 0
    memory_footprint_mb: float = 0.0
    device: str = "cpu"  # cpu, cuda:0, cuda:1, etc.
    # Reference to actual model object (weakref to avoid cycles)
    _model_ref: weakref.ref | None = None

    @property
    def model(self) -> Any | None:
        """Get the actual model object if still alive."""
        if self._model_ref is not None:
            return self._model_ref()
        return None

    @model.setter
    def model(self, value: Any) -> None:
        """Set model object via weak reference."""
        if value is not None:
            self._model_ref = weakref.ref(value)
        else:
            self._model_ref = None


@dataclass
class CacheStats:
    """Cache performance statistics."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_loaded: int = 0
    total_unloaded: int = 0
    memory_used_mb: float = 0.0
    memory_limit_mb: float = 0.0
    hit_rate: float = 0.0
    avg_load_time_ms: float = 0.0


class ModelCache:
    """Intelligent LRU model cache with memory-aware eviction.

    Manages model loading/unloading with:
    - LRU eviction policy
    - Memory pressure monitoring
    - Async preloading
    - Reference counting for shared models
    """

    def __init__(
        self,
        max_memory_mb: float = 8192,  # 8GB default
        max_models: int = 10,
        warmup_on_start: bool = True,
        preload_models: list[str] | None = None,
        eviction_policy: str = "lru_memory",  # lru, lru_memory, freq
    ):
        self.max_memory_mb = max_memory_mb
        self.max_models = max_models
        self.warmup_on_start = warmup_on_start
        self.preload_models = preload_models or []
        self.eviction_policy = eviction_policy

        # Cache storage: OrderedDict for LRU ordering
        self._cache: OrderedDict[str, ModelInfo] = OrderedDict()
        self._lock = asyncio.Lock()
        self._stats = CacheStats(memory_limit_mb=max_memory_mb)

        # Background tasks
        self._preload_queue: asyncio.Queue = asyncio.Queue()
        self._preload_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

        # Model loaders (registered by type)
        self._loaders: dict[str, Callable[[Path], Any]] = {}
        self._unloaders: dict[str, Callable[[Any], None]] = {}

        # Memory tracking
        self._current_memory_mb = 0.0
        self._memory_check_interval = 5.0

    def register_loader(
        self,
        model_type: str,
        loader: Callable[[Path], Any],
        unloader: Callable[[Any], None] | None = None,
    ) -> None:
        """Register a model loader function for a specific type.

        Args:
            model_type: Type identifier (e.g., "checkpoint", "lora")
            loader: Function that takes a Path and returns loaded model
            unloader: Optional cleanup function
        """
        self._loaders[model_type] = loader
        if unloader:
            self._unloaders[model_type] = unloader
        logger.info(f"Registered loader for model type: {model_type}")

    async def initialize(self) -> None:
        """Initialize cache and start background tasks."""
        # Start memory monitor
        self._monitor_task = asyncio.create_task(self._memory_monitor())

        # Start preload worker
        self._preload_task = asyncio.create_task(self._preload_worker())

        # Warmup if configured
        if self.warmup_on_start and self.preload_models:
            logger.info(f"Warming up {len(self.preload_models)} models...")
            for model_name in self.preload_models:
                await self._preload_queue.put(model_name)

    async def get_model(
        self,
        name: str,
        path: Path,
        model_type: str = "checkpoint",
    ) -> Any | None:
        """Get a model from cache, loading if necessary.

        Args:
            name: Unique model identifier
            path: Path to model file
            model_type: Registered model type

        Returns:
            Loaded model object or None
        """
        async with self._lock:
            # Check cache
            if name in self._cache:
                info = self._cache[name]
                info.last_used = time.time()
                info.use_count += 1

                # Move to end (most recently used)
                self._cache.move_to_end(name)

                self._stats.hits += 1
                self._update_hit_rate()

                logger.debug(f"Cache hit: {name} (uses: {info.use_count})")
                return info.model

            self._stats.misses += 1
            self._update_hit_rate()

        # Load model (outside lock to allow concurrent loads)
        logger.info(f"Cache miss: loading {name}")
        model = await self._load_model(name, path, model_type)

        if model is not None:
            async with self._lock:
                # Check if another task loaded it while we were loading
                if name in self._cache:
                    # Use the already loaded one
                    if model_type in self._unloaders:
                        try:
                            self._unloaders[model_type](model)
                        except Exception:
                            pass
                    return self._cache[name].model

                # Add to cache
                info = ModelInfo(
                    name=name,
                    path=path,
                    model_type=model_type,
                    loaded=True,
                    last_used=time.time(),
                    use_count=1,
                )
                info.model = model

                # Estimate memory footprint
                info.memory_footprint_mb = await self._estimate_memory(model)

                # Evict if necessary
                await self._ensure_space(info.memory_footprint_mb)

                self._cache[name] = info
                self._current_memory_mb += info.memory_footprint_mb
                self._stats.total_loaded += 1
                self._stats.memory_used_mb = self._current_memory_mb

                logger.info(f"Cached model: {name} ({info.memory_footprint_mb:.1f} MB)")

        return model

    async def _load_model(
        self,
        name: str,
        path: Path,
        model_type: str,
    ) -> Any | None:
        """Load a model using the registered loader."""
        if model_type not in self._loaders:
            logger.error(f"No loader registered for model type: {model_type}")
            return None

        if not path.exists():
            logger.error(f"Model file not found: {path}")
            return None

        start_time = time.time()

        try:
            # Run loader in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            model = await loop.run_in_executor(None, self._loaders[model_type], path)

            load_time = (time.time() - start_time) * 1000
            logger.info(f"Loaded {name} in {load_time:.1f}ms")

            return model

        except Exception as e:
            logger.error(f"Failed to load model {name}: {e}")
            return None

    async def _unload_model(self, name: str) -> None:
        """Unload a model from cache."""
        async with self._lock:
            if name not in self._cache:
                return

            info = self._cache[name]
            model = info.model

            if model is not None and info.model_type in self._unloaders:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, self._unloaders[info.model_type], model
                    )
                except Exception as e:
                    logger.warning(f"Error unloading {name}: {e}")

            self._current_memory_mb -= info.memory_footprint_mb
            self._stats.memory_used_mb = self._current_memory_mb
            self._stats.total_unloaded += 1

            del self._cache[name]

            logger.info(
                f"Evicted model: {name} (freed {info.memory_footprint_mb:.1f} MB)"
            )

    async def _ensure_space(self, needed_mb: float) -> None:
        """Ensure enough memory is available by evicting models."""
        while (
            self._current_memory_mb + needed_mb > self.max_memory_mb
            or len(self._cache) >= self.max_models
        ) and self._cache:

            # Select victim based on policy
            victim = self._select_victim()
            if victim is None:
                break

            await self._unload_model(victim)
            self._stats.evictions += 1

    def _select_victim(self) -> str | None:
        """Select a model to evict based on policy."""
        if not self._cache:
            return None

        if self.eviction_policy == "lru":
            # First item is least recently used
            return next(iter(self._cache.keys()))

        elif self.eviction_policy == "lru_memory":
            # Weighted by memory size and recency
            now = time.time()
            best_score = float("inf")
            victim = None

            for name, info in self._cache.items():
                # Score: lower = more likely to evict
                # Factor: age * memory_size / use_count
                age = now - info.last_used
                score = age * info.memory_footprint_mb / max(info.use_count, 1)

                if score > best_score:
                    best_score = score
                    victim = name

            return victim

        elif self.eviction_policy == "freq":
            # Least frequently used
            min_uses = float("inf")
            victim = None

            for name, info in self._cache.items():
                if info.use_count < min_uses:
                    min_uses = info.use_count
                    victim = name

            return victim

        return next(iter(self._cache.keys()))

    async def _estimate_memory(self, model: Any) -> float:
        """Estimate model memory footprint in MB."""
        try:
            # Try PyTorch
            import torch

            if hasattr(model, "parameters"):
                param_size = sum(
                    p.nelement() * p.element_size() for p in model.parameters()
                )
                buffer_size = sum(
                    b.nelement() * b.element_size() for b in model.buffers()
                )
                return (param_size + buffer_size) / (1024 * 1024)
        except ImportError:
            pass

        # Fallback: try to get object size
        try:
            import sys

            size = sys.getsizeof(model)
            # Rough estimate for nested objects
            return size / (1024 * 1024) * 2
        except Exception:
            pass

        return 500.0  # Default 500MB estimate

    async def _preload_worker(self) -> None:
        """Background worker for preloading models."""
        while not self._shutdown_event.is_set():
            try:
                model_name = await asyncio.wait_for(
                    self._preload_queue.get(),
                    timeout=1.0,
                )

                # Preload logic would go here
                logger.debug(f"Preloading model: {model_name}")

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Preload worker error: {e}")

    async def _memory_monitor(self) -> None:
        """Monitor system memory and trigger cleanup if needed."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._memory_check_interval,
                )
                break
            except asyncio.TimeoutError:
                # Check system memory
                memory = psutil.virtual_memory()
                available_mb = memory.available / (1024 * 1024)

                # If low on system memory, be more aggressive
                if memory.percent > 90:
                    logger.warning(f"System memory critical: {memory.percent}% used")
                    async with self._lock:
                        # Evict half the cache
                        victims = list(self._cache.keys())[: len(self._cache) // 2]
                    for victim in victims:
                        await self._unload_model(victim)

                elif memory.percent > 80:
                    logger.info(f"System memory high: {memory.percent}% used")
                    # Evict least recently used
                    async with self._lock:
                        if self._cache:
                            victim = next(iter(self._cache.keys()))
                    if victim:
                        await self._unload_model(victim)

            except Exception as e:
                logger.error(f"Memory monitor error: {e}")

    def _update_hit_rate(self) -> None:
        """Update cache hit rate statistic."""
        total = self._stats.hits + self._stats.misses
        if total > 0:
            self._stats.hit_rate = self._stats.hits / total * 100

    async def preload(self, model_names: list[str]) -> None:
        """Queue models for preloading."""
        for name in model_names:
            await self._preload_queue.put(name)
        logger.info(f"Queued {len(model_names)} models for preloading")

    async def warmup(self, model_configs: list[dict[str, Any]]) -> None:
        """Warm up models by loading them into cache.

        Args:
            model_configs: List of dicts with keys: name, path, type
        """
        logger.info(f"Warming up {len(model_configs)} models...")

        for config in model_configs:
            name = config["name"]
            path = Path(config["path"])
            model_type = config.get("type", "checkpoint")

            model = await self.get_model(name, path, model_type)
            if model is not None:
                logger.info(f"Warmed up: {name}")
            else:
                logger.warning(f"Failed to warm up: {name}")

            # Small delay to avoid overwhelming the system
            await asyncio.sleep(0.5)

    async def clear(self) -> None:
        """Clear all cached models."""
        async with self._lock:
            names = list(self._cache.keys())

        for name in names:
            await self._unload_model(name)

        logger.info("Cache cleared")

    def get_stats(self) -> CacheStats:
        """Get current cache statistics."""
        return CacheStats(
            hits=self._stats.hits,
            misses=self._stats.misses,
            evictions=self._stats.evictions,
            total_loaded=self._stats.total_loaded,
            total_unloaded=self._stats.total_unloaded,
            memory_used_mb=self._current_memory_mb,
            memory_limit_mb=self.max_memory_mb,
            hit_rate=self._stats.hit_rate,
        )

    async def shutdown(self) -> None:
        """Gracefully shutdown cache and cleanup."""
        self._shutdown_event.set()

        # Cancel background tasks
        if self._preload_task:
            self._preload_task.cancel()
            try:
                await self._preload_task
            except asyncio.CancelledError:
                pass

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Clear cache
        await self.clear()

        # Force garbage collection
        gc.collect()

        logger.info("Model cache shutdown complete")


class ModelWarmupManager:
    """Manages model warmup strategies for different scenarios.

    Provides:
    - Scheduled warmup (daily/weekly)
    - On-demand warmup before batches
    - Smart warmup based on usage patterns
    - Parallel warmup for multiple models
    """

    def __init__(self, cache: ModelCache):
        self.cache = cache
        self._warmup_history: dict[str, list[float]] = {}
        self._schedule_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

    async def schedule_warmup(
        self,
        model_configs: list[dict[str, Any]],
        schedule: str = "daily",  # daily, weekly, or cron expression
        time_of_day: str = "04:00",  # HH:MM
    ) -> None:
        """Schedule automatic warmup at specified times.

        Args:
            model_configs: Models to warm up
            schedule: Schedule type
            time_of_day: Time in HH:MM format
        """
        self._schedule_task = asyncio.create_task(
            self._scheduled_warmup_loop(model_configs, schedule, time_of_day)
        )

    async def _scheduled_warmup_loop(
        self,
        model_configs: list[dict[str, Any]],
        schedule: str,
        time_of_day: str,
    ) -> None:
        """Background loop for scheduled warmups."""
        target_hour, target_minute = map(int, time_of_day.split(":"))

        while not self._shutdown_event.is_set():
            try:
                now = datetime.now()
                target = now.replace(hour=target_hour, minute=target_minute, second=0)

                if target <= now:
                    if schedule == "daily":
                        target += timedelta(days=1)
                    elif schedule == "weekly":
                        target += timedelta(weeks=1)

                wait_seconds = (target - now).total_seconds()

                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=wait_seconds,
                )
                break

            except asyncio.TimeoutError:
                # Time to warmup
                logger.info(f"Scheduled warmup starting ({schedule} at {time_of_day})")
                await self.cache.warmup(model_configs)

            except asyncio.CancelledError:
                break

    async def smart_warmup(
        self,
        recent_jobs: list[dict[str, Any]],
        top_n: int = 5,
    ) -> None:
        """Warm up most frequently used models from recent jobs.

        Args:
            recent_jobs: List of recent generation jobs with model info
            top_n: Number of top models to warm up
        """
        # Count model usage
        model_counts: dict[str, dict[str, Any]] = {}

        for job in recent_jobs:
            model_name = job.get("model_name", "")
            if model_name:
                if model_name not in model_counts:
                    model_counts[model_name] = {
                        "count": 0,
                        "path": job.get("model_path", ""),
                        "type": job.get("model_type", "checkpoint"),
                    }
                model_counts[model_name]["count"] += 1

        # Sort by usage and take top N
        top_models = sorted(
            model_counts.items(),
            key=lambda x: x[1]["count"],
            reverse=True,
        )[:top_n]

        configs = [
            {
                "name": name,
                "path": info["path"],
                "type": info["type"],
            }
            for name, info in top_models
            if info["path"]
        ]

        if configs:
            logger.info(f"Smart warmup: {len(configs)} models from usage patterns")
            await self.cache.warmup(configs)

    async def parallel_warmup(
        self,
        model_configs: list[dict[str, Any]],
        max_concurrent: int = 2,
    ) -> None:
        """Warm up multiple models in parallel with concurrency limit.

        Args:
            model_configs: Models to warm up
            max_concurrent: Maximum concurrent loads
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def load_with_limit(config: dict[str, Any]) -> None:
            async with semaphore:
                await self.cache.get_model(
                    config["name"],
                    Path(config["path"]),
                    config.get("type", "checkpoint"),
                )

        tasks = [load_with_limit(config) for config in model_configs]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"Parallel warmup complete: {len(model_configs)} models")

    async def shutdown(self) -> None:
        """Shutdown warmup manager."""
        self._shutdown_event.set()

        if self._schedule_task:
            self._schedule_task.cancel()
            try:
                await self._schedule_task
            except asyncio.CancelledError:
                pass


# Convenience functions for common loaders
def create_torch_loader(device: str = "cuda") -> Callable[[Path], Any]:
    """Create a PyTorch model loader.

    Args:
        device: Target device (cuda, cpu, cuda:0, etc.)

    Returns:
        Loader function for ModelCache
    """
    import torch

    def loader(path: Path) -> Any:
        return torch.load(path, map_location=device, weights_only=True)

    return loader


def create_safetensors_loader(device: str = "cuda") -> Callable[[Path], Any]:
    """Create a safetensors model loader.

    Args:
        device: Target device

    Returns:
        Loader function for ModelCache
    """
    try:
        from safetensors.torch import load_file

        def loader(path: Path) -> Any:
            return load_file(str(path), device=device)

        return loader
    except ImportError:
        logger.warning("safetensors not available, falling back to torch.load")
        return create_torch_loader(device)


def create_torch_unloader() -> Callable[[Any], None]:
    """Create a PyTorch model unloader that moves to CPU and frees memory."""
    import torch

    def unloader(model: Any) -> None:
        if hasattr(model, "to"):
            model.to("cpu")
        if hasattr(model, "cpu"):
            model.cpu()
        del model
        torch.cuda.empty_cache()

    return unloader


# Example usage and initialization
async def create_default_cache(
    max_memory_gb: float = 8.0,
    device: str = "cuda",
) -> ModelCache:
    """Create a ModelCache with default PyTorch loaders."""
    cache = ModelCache(max_memory_mb=max_memory_gb * 1024)

    # Register common loaders
    cache.register_loader(
        "checkpoint",
        create_safetensors_loader(device),
        create_torch_unloader(),
    )
    cache.register_loader(
        "lora",
        create_safetensors_loader(device),
        create_torch_unloader(),
    )
    cache.register_loader(
        "vae",
        create_safetensors_loader(device),
        create_torch_unloader(),
    )

    await cache.initialize()
    return cache


if __name__ == "__main__":
    # Example setup
    async def main():
        cache = await create_default_cache(max_memory_gb=4.0)

        # Example warmup
        await cache.warmup(
            [
                {
                    "name": "sdxl_base",
                    "path": "/models/sdxl.safetensors",
                    "type": "checkpoint",
                },
                {
                    "name": "anime_lora",
                    "path": "/models/anime.safetensors",
                    "type": "lora",
                },
            ]
        )

        # Get stats
        stats = cache.get_stats()
        print(f"Cache stats: {stats}")

        await cache.shutdown()

    asyncio.run(main())
