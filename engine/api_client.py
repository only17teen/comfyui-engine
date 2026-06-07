"""ComfyUI Async Generation Engine v5.1 - Async API Client (Optimized)
Kiro Protocol optimizations: connection pooling, async polling, object pooling.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set
from pathlib import Path

import aiohttp

from engine.core import (
    CircuitBreaker,
    CircuitBreakerConfig,
    MetricsCollector,
    RetryConfig,
    with_retry,
    CircuitBreakerOpenError,
)

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────
# Object Pool for ComfyUIJob (Kiro Rule 6: Memory First)
# ───────────────────────────────────────────────────────────────
class JobPool:
    """Object pool for ComfyUIJob instances to reduce allocation overhead.
    
    Kiro Protocol optimizations:
    - Pre-allocated job objects (Rule 6: Memory First)
    - Reset and reuse pattern (Rule 1: Relentless Optimization)
    - Lock-free acquisition via asyncio.Queue (Rule 6: Memory First)
    """

    def __init__(self, initial_size: int = 50, max_size: int = 200):
        self._available: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._max_size = max_size
        self._created = 0
        
        # Pre-populate pool
        for _ in range(initial_size):
            self._available.put_nowait(self._create_job())

    def _create_job(self) -> "ComfyUIJob":
        """Create a new job instance."""
        self._created += 1
        return ComfyUIJob.__new__(ComfyUIJob)

    async def acquire(
        self,
        prompt_id: str,
        payload: dict,
        config_meta: dict,
        job_id: str | None = None,
    ) -> "ComfyUIJob":
        """Acquire a job from pool or create new."""
        try:
            job = self._available.get_nowait()
            job.reset(prompt_id, payload, config_meta, job_id)
            return job
        except asyncio.QueueEmpty:
            if self._created < self._max_size:
                job = self._create_job()
                job.reset(prompt_id, payload, config_meta, job_id)
                return job
            # Wait for available job
            job = await self._available.get()
            job.reset(prompt_id, payload, config_meta, job_id)
            return job

    async def release(self, job: "ComfyUIJob") -> None:
        """Return job to pool for reuse."""
        job.clear()
        try:
            self._available.put_nowait(job)
        except asyncio.QueueFull:
            pass  # Drop if pool is full

    def stats(self) -> dict[str, int]:
        """Pool statistics."""
        return {
            "created": self._created,
            "available": self._available.qsize(),
            "max_size": self._max_size,
        }


class ComfyUIJob:
    """Represents a single queued generation job with full lifecycle tracking.
    
    Kiro Protocol optimizations:
    - __slots__ for memory efficiency (Rule 6: Memory First)
    - reset() method for pool reuse (Rule 6: Memory First)
    - clear() method for pool cleanup (Rule 6: Memory First)
    """

    __slots__ = [
        "prompt_id", "job_id", "payload", "config_meta", "status",
        "outputs", "error_msg", "created_at", "queued_at",
        "started_at", "completed_at", "retry_count", "downloaded_files",
    ]

    def __init__(
        self,
        prompt_id: str = "",
        payload: dict = None,
        config_meta: dict = None,
        job_id: str | None = None,
    ):
        self.reset(prompt_id, payload or {}, config_meta or {}, job_id)

    def reset(
        self,
        prompt_id: str,
        payload: dict,
        config_meta: dict,
        job_id: str | None = None,
    ) -> None:
        """Reset job for reuse from pool."""
        self.prompt_id = prompt_id
        self.job_id = job_id or f"job_{uuid.uuid4().hex[:8]}"
        self.payload = payload
        self.config_meta = config_meta
        self.status = "pending"
        self.outputs = []
        self.error_msg = None
        self.created_at = time.time()
        self.queued_at = None
        self.started_at = None
        self.completed_at = None
        self.retry_count = 0
        self.downloaded_files = []

    def clear(self) -> None:
        """Clear job for pool return."""
        self.prompt_id = ""
        self.job_id = ""
        self.payload = {}
        self.config_meta = {}
        self.status = "pending"
        self.outputs.clear()
        self.error_msg = None
        self.created_at = 0.0
        self.queued_at = None
        self.started_at = None
        self.completed_at = None
        self.retry_count = 0
        self.downloaded_files.clear()

    @property
    def wait_time(self) -> float:
        """Time spent waiting in queue."""
        if self.queued_at and self.started_at:
            return self.started_at - self.queued_at
        return 0.0

    @property
    def processing_time(self) -> float:
        """Time spent processing on GPU."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return 0.0

    @property
    def total_time(self) -> float:
        """Total time from creation to completion."""
        end = self.completed_at or time.time()
        return end - self.created_at

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "prompt_id": self.prompt_id,
            "status": self.status,
            "created_at": self.created_at,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "wait_time": self.wait_time,
            "processing_time": self.processing_time,
            "total_time": self.total_time,
            "retry_count": self.retry_count,
            "outputs": self.outputs,
            "error_msg": self.error_msg,
            "config_meta": self.config_meta,
        }


class ComfyUIAsyncClient:
    """Production-grade async client for ComfyUI API (Optimized).

    Kiro Protocol optimizations:
    - Tuned connection pool for ComfyUI single-instance (Rule 1: Optimization)
    - Async polling with adaptive intervals (Rule 7: Async Correctness)
    - Object pooling for jobs (Rule 6: Memory First)
    - Batch metric updates (Rule 1: Optimization)
    - Connection health checks (Rule 4: Reliability)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        max_concurrent: int = 3,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
        retry_config: RetryConfig | None = None,
        circuit_config: CircuitBreakerConfig | None = None,
        metrics: MetricsCollector | None = None,
        use_websocket: bool = True,
        enable_job_pooling: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_concurrent = max_concurrent
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.use_websocket = use_websocket

        self.retry_config = retry_config or RetryConfig()
        self.metrics = metrics or MetricsCollector()
        self.circuit = CircuitBreaker(
            name="comfyui_api",
            config=circuit_config or CircuitBreakerConfig(),
            metrics=self.metrics,
        )

        # Object pool for jobs (Kiro Rule 6)
        self._job_pool: JobPool | None = JobPool() if enable_job_pooling else None

        self._session: aiohttp.ClientSession | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._jobs: dict[str, ComfyUIJob] = {}
        self._ws_task: asyncio.Task | None = None
        self._ws_queue: asyncio.Queue | None = None
        self._shutdown: bool = False
        
        # Metric batching (Kiro Rule 1)
        self._metric_batch: list[tuple[str, int]] = []
        self._metric_batch_size = 10

    async def _batch_metric(self, metric: str, value: int = 1) -> None:
        """Batch metric updates for efficiency."""
        self._metric_batch.append((metric, value))
        if len(self._metric_batch) >= self._metric_batch_size:
            for m, v in self._metric_batch:
                await self.metrics.inc(m, v)
            self._metric_batch.clear()

    async def _flush_metrics(self) -> None:
        """Flush remaining batched metrics."""
        if self._metric_batch:
            for m, v in self._metric_batch:
                await self.metrics.inc(m, v)
            self._metric_batch.clear()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-init aiohttp session with optimized connection pool.
        
        Kiro Protocol optimizations:
        - limit=10 (was 50) for single ComfyUI instance (Rule 1: Optimization)
        - limit_per_host=5 (was 10) for single host (Rule 1: Optimization)
        - enable_cleanup_closed=True for connection hygiene (Rule 4: Reliability)
        - force_close=False for connection reuse (Rule 1: Optimization)
        """
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.timeout,
                connect=10.0,
                sock_read=30.0,
            )
            connector = aiohttp.TCPConnector(
                limit=10,  # Tuned for single ComfyUI instance
                limit_per_host=5,  # Single host optimization
                keepalive_timeout=30.0,
                enable_cleanup_closed=True,
                force_close=False,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                raise_for_status=False,
            )
        return self._session

    async def health_check(self) -> dict[str, Any]:
        """Detailed health check with latency metrics.
        
        Kiro Protocol: Detailed health status (Rule 11: Observability)
        """
        start = time.time()
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/system_stats", timeout=5.0) as resp:
                latency = (time.time() - start) * 1000
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "status": "healthy",
                        "latency_ms": round(latency, 2),
                        "comfyui_version": data.get("system", {}).get("comfyui_version", "unknown"),
                        "python_version": data.get("system", {}).get("python_version", "unknown"),
                        "devices": len(data.get("system", {}).get("devices", [])),
                    }
                return {
                    "status": "degraded",
                    "latency_ms": round(latency, 2),
                    "http_status": resp.status,
                }
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {
                "status": "unhealthy",
                "latency_ms": round(latency, 2),
                "error": str(e),
            }

    async def _post_prompt(self, payload: dict) -> str:
        """Submit workflow with circuit breaker and retry protection."""

        async def _do_post():
            session = await self._get_session()
            url = f"{self.base_url}/prompt"
            body = {
                "prompt": payload,
                "client_id": str(uuid.uuid4()),
            }

            async with session.post(url, json=body) as resp:
                if resp.status == 429:
                    text = await resp.text()
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=429,
                        message=f"Rate limited: {text}",
                    )
                if resp.status == 503:
                    text = await resp.text()
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=503,
                        message=f"Server overloaded: {text}",
                    )
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Prompt submission failed: {resp.status} - {text}")

                data = await resp.json()
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    raise RuntimeError(f"No prompt_id in response: {data}")
                return prompt_id

        return await with_retry(
            _do_post,
            config=self.retry_config,
            metrics=self.metrics,
        )

    async def _fetch_history(self, prompt_id: str) -> dict | None:
        """Fetch execution history with retry."""

        async def _do_fetch():
            session = await self._get_session()
            url = f"{self.base_url}/history/{prompt_id}"
            async with session.get(url) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                data = await resp.json()
                return data.get(prompt_id)

        try:
            return await with_retry(
                _do_fetch,
                config=RetryConfig(max_retries=2, base_delay=0.5),
                metrics=self.metrics,
            )
        except Exception as e:
            await self._batch_metric("api_errors", 1)
            logger.debug(f"History fetch error for {prompt_id}: {e}")
            return None

    async def _poll_job(self, job: ComfyUIJob) -> None:
        """Poll job status with adaptive intervals.
        
        Kiro Protocol optimizations:
        - Async polling with adaptive intervals (Rule 7: Async Correctness)
        - Exponential backoff for slow jobs (Rule 1: Optimization)
        - Batch metric updates (Rule 1: Optimization)
        """
        job.started_at = time.time()
        job.status = "running"
        start_time = time.time()

        # Adaptive poll intervals
        fast_poll = 0.5
        medium_poll = self.poll_interval
        slow_poll = min(self.poll_interval * 2.0, 5.0)  # Cap at 5s
        current_poll = fast_poll
        
        # Phase transitions
        fast_phase = 30.0  # First 30 seconds
        medium_phase = 120.0  # Next 2 minutes

        while True:
            elapsed = time.time() - start_time
            if elapsed > self.timeout:
                job.status = "error"
                job.error_msg = f"Timeout after {self.timeout}s"
                job.completed_at = time.time()
                await self._batch_metric("jobs_timeout", 1)
                logger.error(f"Job {job.prompt_id} timed out")
                return

            # Adjust poll interval based on elapsed time
            if elapsed > medium_phase:
                current_poll = slow_poll
            elif elapsed > fast_phase:
                current_poll = medium_poll
            else:
                current_poll = fast_poll

            history = await self._fetch_history(job.prompt_id)
            if history:
                status = history.get("status", {})
                status_str = status.get("status_str", "")

                if status_str == "success" or "outputs" in history:
                    job.status = "completed"
                    job.completed_at = time.time()
                    job.outputs = self._extract_outputs(history.get("outputs", {}))
                    await self._batch_metric("jobs_completed", 1)
                    await self.metrics.observe("processing_time", job.processing_time)
                    logger.info(
                        f"Job {job.prompt_id} completed in {job.processing_time:.1f}s ({len(job.outputs)} outputs)"
                    )
                    return

                elif status_str == "error":
                    job.status = "error"
                    job.error_msg = status.get("messages", "Unknown error")
                    job.completed_at = time.time()
                    await self._batch_metric("jobs_failed", 1)
                    logger.error(f"Job {job.prompt_id} failed: {job.error_msg}")
                    return

            await asyncio.sleep(current_poll)

    async def _ws_connect(self) -> None:
        """WebSocket connection for real-time status updates."""
        if not self.use_websocket:
            return

        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws"

        try:
            session = await self._get_session()
            async with session.ws_connect(ws_url) as ws:
                logger.info(f"WebSocket connected: {ws_url}")
                async for msg in ws:
                    if self._shutdown:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(data)
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        logger.warning("WebSocket closed")
                        break
        except Exception as e:
            logger.debug(f"WebSocket error: {e}")

    async def _handle_ws_message(self, data: dict) -> None:
        """Process WebSocket message for job status updates."""
        msg_type = data.get("type")
        if msg_type == "status":
            status_data = data.get("data", {})
            queue_remaining = status_data.get("status", {}).get("exec_info", {}).get("queue_remaining", 0)
            if queue_remaining > 0:
                await self.metrics.gauge("comfyui_queue_depth", float(queue_remaining))

    async def submit_job(self, payload: dict, config_meta: dict) -> ComfyUIJob:
        """Submit job with circuit breaker protection and object pooling."""
        try:
            prompt_id = await self.circuit.call(self._post_prompt, payload)
            
            # Use object pool if available (Kiro Rule 6)
            if self._job_pool:
                job = await self._job_pool.acquire(prompt_id, payload, config_meta)
            else:
                job = ComfyUIJob(prompt_id, payload, config_meta)
            
            job.queued_at = time.time()
            self._jobs[job.job_id] = job
            await self._batch_metric("jobs_submitted", 1)
            return job
        except CircuitBreakerOpenError:
            if self._job_pool:
                job = await self._job_pool.acquire(
                    f"cb-open-{uuid.uuid4().hex[:8]}",
                    payload,
                    config_meta,
                )
            else:
                job = ComfyUIJob(
                    prompt_id=f"cb-open-{uuid.uuid4().hex[:8]}",
                    payload=payload,
                    config_meta=config_meta,
                )
            job.status = "error"
            job.error_msg = "Circuit breaker OPEN - ComfyUI API unavailable"
            self._jobs[job.job_id] = job
            await self._batch_metric("jobs_failed", 1)
            return job

    async def run_job(self, payload: dict, config_meta: dict) -> ComfyUIJob:
        """Submit and fully process a single job (semaphore-guarded)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)

        async with self._semaphore:
            await self.metrics.gauge("active_workers", float(self._semaphore._value))
            job = await self.submit_job(payload, config_meta)
            if job.status != "error":
                await self._poll_job(job)
            await self.metrics.gauge("active_workers", float(self._semaphore._value))
            return job

    async def run_batch(
        self,
        payloads: list[dict],
        config_metas: list[dict],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[ComfyUIJob]:
        """Run batch with concurrent execution and progress tracking."""
        if len(payloads) != len(config_metas):
            raise ValueError("payloads and config_metas must have same length")

        total = len(payloads)
        completed = 0

        async def _wrapped_run(idx: int, payload: dict, meta: dict) -> ComfyUIJob:
            nonlocal completed
            try:
                job = await self.run_job(payload, meta)
            except Exception as e:
                logger.error(f"Job {idx} failed with exception: {e}")
                if self._job_pool:
                    job = await self._job_pool.acquire(
                        f"exception-{uuid.uuid4().hex[:8]}",
                        payload,
                        meta,
                    )
                else:
                    job = ComfyUIJob(
                        prompt_id=f"exception-{uuid.uuid4().hex[:8]}",
                        payload=payload,
                        config_meta=meta,
                    )
                job.status = "error"
                job.error_msg = str(e)
                job.completed_at = time.time()
                await self._batch_metric("jobs_failed", 1)

            completed += 1
            if progress_callback:
                progress_callback(total, completed, job.status)
            return job

        tasks = [asyncio.create_task(_wrapped_run(i, p, m)) for i, (p, m) in enumerate(zip(payloads, config_metas))]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        jobs = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Batch job {i} failed: {res}")
                if self._job_pool:
                    job = await self._job_pool.acquire(
                        f"batch-fail-{uuid.uuid4().hex[:8]}",
                        payloads[i],
                        config_metas[i],
                    )
                else:
                    job = ComfyUIJob(
                        prompt_id=f"batch-fail-{uuid.uuid4().hex[:8]}",
                        payload=payloads[i],
                        config_meta=config_metas[i],
                    )
                job.status = "error"
                job.error_msg = str(res)
                job.completed_at = time.time()
                await self._batch_metric("jobs_failed", 1)
                jobs.append(job)
            else:
                jobs.append(res)

        return jobs

    async def download_output(
        self,
        filename: str,
        subfolder: str = "",
        output_type: str = "output",
        save_path: Path = Path("output_models"),
        max_retries: int = 3,
    ) -> Path:
        """Download with retry and byte counting."""

        async def _do_download():
            session = await self._get_session()
            url = f"{self.base_url}/view"
            params = {
                "filename": filename,
                "subfolder": subfolder,
                "type": output_type,
            }

            save_path.mkdir(parents=True, exist_ok=True)
            local_file = save_path / filename

            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                total_bytes = 0
                with open(local_file, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                        total_bytes += len(chunk)

                await self._batch_metric("download_bytes", total_bytes)
                return local_file

        return await with_retry(
            _do_download,
            config=RetryConfig(max_retries=max_retries, base_delay=1.0),
            metrics=self.metrics,
        )

    def _extract_outputs(self, outputs: dict) -> list[dict]:
        """Extract image references from ComfyUI history."""
        results = []
        for node_id, node_outputs in outputs.items():
            if isinstance(node_outputs, dict):
                images = node_outputs.get("images", [])
                for img in images:
                    if isinstance(img, dict):
                        results.append(
                            {
                                "node_id": node_id,
                                "filename": img.get("filename"),
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                            }
                        )
        return results

    async def start_ws_listener(self) -> None:
        """Start background WebSocket listener."""
        if self.use_websocket and self._ws_task is None:
            self._ws_task = asyncio.create_task(self._ws_connect())

    async def stop_ws_listener(self) -> None:
        """Stop WebSocket listener."""
        self._shutdown = True
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        """Graceful shutdown with metric flush and pool cleanup."""
        self._shutdown = True
        await self.stop_ws_listener()
        await self._flush_metrics()
        
        # Return jobs to pool
        if self._job_pool:
            for job in self._jobs.values():
                await self._job_pool.release(job)
            self._jobs.clear()
        
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("API client session closed")

    def get_job(self, job_id: str) -> ComfyUIJob | None:
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[ComfyUIJob]:
        return list(self._jobs.values())

    def get_job_stats(self) -> dict[str, int]:
        """Return job statistics by status."""
        stats = {}
        for job in self._jobs.values():
            stats[job.status] = stats.get(job.status, 0) + 1
        return stats

    def get_pool_stats(self) -> dict[str, int] | None:
        """Get job pool statistics if pooling is enabled."""
        return self._job_pool.stats() if self._job_pool else None
