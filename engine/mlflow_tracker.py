"""MLflow integration for experiment tracking and model versioning.

Tracks generation parameters, image metrics, and model lineage automatically.
Supports both local MLflow and remote tracking servers.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class GenerationMetrics:
    """Metrics captured for each image generation."""

    prompt_id: str
    seed: int
    steps: int
    cfg_scale: float
    sampler: str
    model_name: str
    loras: list[str] = field(default_factory=list)
    generation_time_ms: float = 0.0
    queue_time_ms: float = 0.0
    image_width: int = 512
    image_height: int = 512
    file_size_bytes: int = 0
    # CLIP score or aesthetic predictor (optional)
    aesthetic_score: float | None = None
    # Perceptual hash for deduplication
    perceptual_hash: str | None = None


@dataclass
class ExperimentConfig:
    """Configuration for an MLflow experiment."""

    experiment_name: str = "comfyui_generations"
    tracking_uri: str | None = None  # None = use local default
    artifact_location: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


class MLflowTracker:
    """Async-aware MLflow tracker for ComfyUI generation experiments.

    Handles experiment lifecycle, run tracking, parameter logging,
    metric recording, and artifact storage with full async support.
    """

    def __init__(
        self,
        config: ExperimentConfig | None = None,
        enabled: bool = True,
        batch_size: int = 10,
        flush_interval_sec: float = 30.0,
    ):
        self.config = config or ExperimentConfig()
        self.enabled = enabled and self._check_mlflow_available()
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec

        # Internal state
        self._experiment_id: str | None = None
        self._active_run_id: str | None = None
        self._pending_metrics: list[dict[str, Any]] = []
        self._pending_params: list[dict[str, Any]] = []
        self._flush_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

        # Local fallback storage when MLflow server unavailable
        self._fallback_dir = Path("logs/mlflow_fallback")
        self._fallback_dir.mkdir(parents=True, exist_ok=True)

        # Metrics cache for quick aggregation
        self._metrics_cache: dict[str, list[float]] = {}

    def _check_mlflow_available(self) -> bool:
        """Check if MLflow Python package is installed."""
        try:
            import mlflow

            return True
        except ImportError:
            logger.warning("MLflow not installed. Tracking disabled. Install with: pip install mlflow")
            return False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session for MLflow REST API."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def initialize(self) -> bool:
        """Initialize MLflow experiment. Returns True if tracking is active."""
        if not self.enabled:
            logger.info("MLflow tracking disabled")
            return False

        try:
            import mlflow

            # Set tracking URI if provided
            if self.config.tracking_uri:
                mlflow.set_tracking_uri(self.config.tracking_uri)
                logger.info(f"MLflow tracking URI: {self.config.tracking_uri}")

            # Create or get experiment
            experiment = mlflow.get_experiment_by_name(self.config.experiment_name)
            if experiment is None:
                self._experiment_id = mlflow.create_experiment(
                    self.config.experiment_name,
                    artifact_location=self.config.artifact_location,
                    tags=self.config.tags,
                )
                logger.info(f"Created MLflow experiment: {self.config.experiment_name}")
            else:
                self._experiment_id = experiment.experiment_id
                logger.info(f"Using existing MLflow experiment: {self.config.experiment_name}")

            # Start background flush task
            self._flush_task = asyncio.create_task(self._background_flush())

            return True

        except Exception as e:
            logger.error(f"Failed to initialize MLflow: {e}")
            self.enabled = False
            return False

    async def start_run(
        self,
        run_name: str | None = None,
        tags: dict[str, str] | None = None,
        parent_run_id: str | None = None,
    ) -> str | None:
        """Start a new MLflow run. Returns run_id or None."""
        if not self.enabled or not self._experiment_id:
            return None

        try:
            import mlflow

            run_tags = tags or {}
            run_tags.setdefault("start_time", datetime.utcnow().isoformat())

            with mlflow.start_run(
                experiment_id=self._experiment_id,
                run_name=run_name,
                tags=run_tags,
                nested=parent_run_id is not None,
            ) as run:
                self._active_run_id = run.info.run_id
                logger.info(f"Started MLflow run: {run_name or self._active_run_id}")
                return self._active_run_id

        except Exception as e:
            logger.error(f"Failed to start MLflow run: {e}")
            return None

    async def log_params(self, params: dict[str, Any]) -> None:
        """Log parameters to the active run."""
        if not self.enabled or not self._active_run_id:
            return

        # Convert to serializable types
        clean_params = {}
        for k, v in params.items():
            if isinstance(v, list | dict):
                clean_params[k] = json.dumps(v)
            elif isinstance(v, int | float | str | bool):
                clean_params[k] = v
            else:
                clean_params[k] = str(v)

        async with self._lock:
            self._pending_params.append(
                {
                    "run_id": self._active_run_id,
                    "params": clean_params,
                    "timestamp": time.time(),
                }
            )

            if len(self._pending_params) >= self.batch_size:
                await self._flush_params()

    async def log_metrics(self, metrics: dict[str, int | float], step: int | None = None) -> None:
        """Log metrics to the active run."""
        if not self.enabled or not self._active_run_id:
            return

        async with self._lock:
            self._pending_metrics.append(
                {
                    "run_id": self._active_run_id,
                    "metrics": metrics,
                    "step": step,
                    "timestamp": time.time(),
                }
            )

            # Update cache
            for k, v in metrics.items():
                self._metrics_cache.setdefault(k, []).append(float(v))

            if len(self._pending_metrics) >= self.batch_size:
                await self._flush_metrics()

    async def log_generation(self, metrics: GenerationMetrics, image_path: Path | None = None) -> None:
        """Log a complete generation event with all parameters and metrics."""
        if not self.enabled:
            return

        # Log parameters
        params = {
            "prompt_id": metrics.prompt_id,
            "seed": metrics.seed,
            "steps": metrics.steps,
            "cfg_scale": metrics.cfg_scale,
            "sampler": metrics.sampler,
            "model_name": metrics.model_name,
            "loras": json.dumps(metrics.loras),
            "image_width": metrics.image_width,
            "image_height": metrics.image_height,
        }
        await self.log_params(params)

        # Log metrics
        metric_values = {
            "generation_time_ms": metrics.generation_time_ms,
            "queue_time_ms": metrics.queue_time_ms,
            "file_size_bytes": metrics.file_size_bytes,
        }
        if metrics.aesthetic_score is not None:
            metric_values["aesthetic_score"] = metrics.aesthetic_score

        await self.log_metrics(metric_values)

        # Log artifact if available
        if image_path and image_path.exists():
            await self.log_artifact(image_path, artifact_path="images")

    async def log_artifact(self, local_path: Path, artifact_path: str | None = None) -> None:
        """Log an artifact (image, config, etc.) to the active run."""
        if not self.enabled or not self._active_run_id:
            return

        try:
            import mlflow

            # Use synchronous mlflow in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: mlflow.log_artifact(
                    str(local_path),
                    artifact_path=artifact_path,
                ),
            )
            logger.debug(f"Logged artifact: {local_path}")

        except Exception as e:
            logger.warning(f"Failed to log artifact {local_path}: {e}")
            # Fallback: copy to local storage
            await self._fallback_artifact(local_path)

    async def _fallback_artifact(self, local_path: Path) -> None:
        """Store artifact locally when MLflow server is unavailable."""
        try:
            import shutil

            dest = self._fallback_dir / local_path.name
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, shutil.copy2, local_path, dest)
            logger.info(f"Fallback artifact stored: {dest}")
        except Exception as e:
            logger.error(f"Fallback artifact storage failed: {e}")

    async def _flush_params(self) -> None:
        """Flush pending parameters to MLflow."""
        if not self._pending_params:
            return

        try:
            import mlflow

            batch = self._pending_params[: self.batch_size]
            self._pending_params = self._pending_params[self.batch_size :]

            for item in batch:
                run_id = item["run_id"]
                params = item["params"]

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda p=params, r=run_id: mlflow.log_params(p, run_id=r),
                )

            logger.debug(f"Flushed {len(batch)} parameter sets")

        except Exception as e:
            logger.warning(f"Parameter flush failed: {e}")
            # Re-queue for retry
            async with self._lock:
                self._pending_params.extend(batch)

    async def _flush_metrics(self) -> None:
        """Flush pending metrics to MLflow."""
        if not self._pending_metrics:
            return

        try:
            import mlflow

            batch = self._pending_metrics[: self.batch_size]
            self._pending_metrics = self._pending_metrics[self.batch_size :]

            for item in batch:
                run_id = item["run_id"]
                metrics = item["metrics"]
                step = item.get("step")

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda m=metrics, s=step, r=run_id: mlflow.log_metrics(m, step=s, run_id=r),
                )

            logger.debug(f"Flushed {len(batch)} metric sets")

        except Exception as e:
            logger.warning(f"Metric flush failed: {e}")
            # Re-queue for retry
            async with self._lock:
                self._pending_metrics.extend(batch)

    async def _background_flush(self) -> None:
        """Background task to periodically flush pending data."""
        while self.enabled:
            try:
                await asyncio.sleep(self.flush_interval_sec)

                async with self._lock:
                    if self._pending_params:
                        await self._flush_params()
                    if self._pending_metrics:
                        await self._flush_metrics()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background flush error: {e}")

    async def end_run(self, status: str = "FINISHED") -> None:
        """End the active MLflow run."""
        if not self.enabled or not self._active_run_id:
            return

        # Final flush
        async with self._lock:
            if self._pending_params:
                await self._flush_params()
            if self._pending_metrics:
                await self._flush_metrics()

        try:
            import mlflow

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: mlflow.set_terminated(self._active_run_id, status=status),
            )

            logger.info(f"Ended MLflow run: {self._active_run_id} ({status})")
            self._active_run_id = None

        except Exception as e:
            logger.error(f"Failed to end run: {e}")

    def get_summary_stats(self) -> dict[str, dict[str, float]]:
        """Get summary statistics for tracked metrics."""
        stats = {}
        for metric_name, values in self._metrics_cache.items():
            if values:
                stats[metric_name] = {
                    "count": len(values),
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "last": values[-1],
                }
        return stats

    async def search_runs(
        self,
        experiment_ids: list[str] | None = None,
        filter_string: str = "",
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Search MLflow runs with async wrapper."""
        if not self.enabled:
            return []

        try:
            import mlflow

            exp_ids = experiment_ids or [self._experiment_id]

            loop = asyncio.get_event_loop()
            runs = await loop.run_in_executor(
                None,
                lambda: mlflow.search_runs(
                    experiment_ids=exp_ids,
                    filter_string=filter_string,
                    max_results=max_results,
                ),
            )

            # Convert DataFrame to dict records
            return runs.to_dict("records") if hasattr(runs, "to_dict") else []

        except Exception as e:
            logger.error(f"Run search failed: {e}")
            return []

    async def shutdown(self) -> None:
        """Gracefully shutdown the tracker."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        if self._session and not self._session.closed:
            await self._session.close()

        # Final flush
        if self.enabled:
            await self.end_run()

        logger.info("MLflow tracker shutdown complete")

    @asynccontextmanager
    async def run_context(
        self,
        run_name: str | None = None,
        tags: dict[str, str] | None = None,
    ):
        """Async context manager for MLflow runs."""
        run_id = await self.start_run(run_name=run_name, tags=tags)
        try:
            yield run_id
        finally:
            await self.end_run()


class ModelRegistry:
    """Manage model versions and lineage tracking via MLflow Model Registry."""

    def __init__(self, tracker: MLflowTracker):
        self.tracker = tracker

    async def register_model(
        self,
        model_name: str,
        run_id: str,
        model_path: str = "model",
        tags: dict[str, str] | None = None,
        description: str | None = None,
    ) -> str | None:
        """Register a model version in MLflow Model Registry."""
        if not self.tracker.enabled:
            return None

        try:
            import mlflow

            loop = asyncio.get_event_loop()
            version = await loop.run_in_executor(
                None,
                lambda: mlflow.register_model(
                    model_uri=f"runs:/{run_id}/{model_path}",
                    name=model_name,
                    tags=tags,
                    description=description,
                ),
            )

            logger.info(f"Registered model {model_name} version {version.version}")
            return version.version

        except Exception as e:
            logger.error(f"Model registration failed: {e}")
            return None

    async def transition_stage(
        self,
        model_name: str,
        version: str,
        stage: str,  # "Staging", "Production", "Archived"
        description: str | None = None,
    ) -> bool:
        """Transition a model version to a new stage."""
        if not self.tracker.enabled:
            return False

        try:
            import mlflow

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: mlflow.transition_model_version_stage(
                    name=model_name,
                    version=version,
                    stage=stage,
                    description=description,
                ),
            )

            logger.info(f"Transitioned {model_name} v{version} to {stage}")
            return True

        except Exception as e:
            logger.error(f"Stage transition failed: {e}")
            return False


# Convenience functions for direct use
async def create_tracker(
    experiment_name: str = "comfyui_generations",
    tracking_uri: str | None = None,
    enabled: bool = True,
) -> MLflowTracker:
    """Factory function to create and initialize an MLflow tracker."""
    config = ExperimentConfig(
        experiment_name=experiment_name,
        tracking_uri=tracking_uri,
    )
    tracker = MLflowTracker(config=config, enabled=enabled)
    await tracker.initialize()
    return tracker


async def log_batch_generations(
    tracker: MLflowTracker,
    generations: list[GenerationMetrics],
    run_name: str | None = None,
) -> None:
    """Log a batch of generations under a single run."""
    async with tracker.run_context(run_name=run_name or f"batch_{int(time.time())}"):
        for gen in generations:
            await tracker.log_generation(gen)

        # Log aggregate metrics
        if generations:
            avg_time = sum(g.generation_time_ms for g in generations) / len(generations)
            await tracker.log_metrics(
                {
                    "avg_generation_time_ms": avg_time,
                    "total_generations": len(generations),
                }
            )
