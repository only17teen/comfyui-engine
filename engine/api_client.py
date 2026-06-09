"""ComfyUI Async Generation Engine v2.0 - Async API Client
Resilient client with circuit breaker, retry logic, metrics, and WebSocket support.
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


class ComfyUIJob:
    """Represents a single queued generation job with full lifecycle tracking."""

    def __init__(
        self,
        prompt_id: str,
        payload: dict,
        config_meta: dict,
        job_id: str | None = None,
    ):
        self.prompt_id = prompt_id
        self.job_id = job_id or f"job_{uuid.uuid4().hex[:8]}"
        self.payload = payload
        self.config_meta = config_meta
        self.status = "pending"  # pending | queued | running | completed | error | cancelled
        self.outputs: list[dict] = []
        self.error_msg: str | None = None
        self.created_at = time.time()
        self.queued_at: float | None = None
        self.started_at: float | None = None
        self.completed_at: float | None = None
        self.retry_count: int = 0
        self.downloaded_files: list[Path] = []

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
    """Production-grade async client for ComfyUI API.

    Features:
    - Circuit breaker for API resilience
    - Exponential backoff retry with jitter
    - Metrics collection (Prometheus-style)
    - WebSocket + HTTP fallback polling
    - Connection pooling with keep-alive
    - Graceful degradation on overload
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

        self._session: aiohttp.ClientSession | None = None
        # FIX: initialise semaphore eagerly in __init__ to prevent a race condition
        # where two concurrent run_job() calls could each create a semaphore before
        # either stores it, resulting in two independent semaphores and double the
        # intended concurrency.
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._jobs: dict[str, ComfyUIJob] = {}
        self._ws_task: asyncio.Task | None = None
        self._ws_queue: asyncio.Queue | None = None
        self._shutdown: bool = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy-init aiohttp session with optimized connection pool."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.timeout,
                connect=10.0,
                sock_read=30.0,
            )
            connector = aiohttp.TCPConnector(
                limit=50,
                limit_per_host=10,
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

    async def health_check(self) -> bool:
        """Quick health check against ComfyUI server."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/system_stats", timeout=5.0) as resp:
                return resp.status == 200
        except Exception:
            return False

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
                    # Rate limited - raise for retry
                    text = await resp.text()
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=429,
                        message=f"Rate limited: {text}",
                    )
                if resp.status == 503:
                    # Server overloaded - raise for retry
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
            await self.metrics.inc("api_errors")
            logger.debug(f"History fetch error for {prompt_id}: {e}")
            return None

    async def _poll_job(self, job: ComfyUIJob) -> None:
        """Poll job status with adaptive timeout."""
        job.started_at = time.time()
        job.status = "running"
        start_time = time.time()

        # Adaptive poll interval: start fast, slow down
        fast_poll = 0.5
        slow_poll = self.poll_interval
        current_poll = fast_poll
        fast_poll_duration = 30.0  # Fast poll for first 30 seconds

        while True:
            elapsed = time.time() - start_time
            if elapsed > self.timeout:
                job.status = "error"
                job.error_msg = f"Timeout after {self.timeout}s"
                job.completed_at = time.time()
                await self.metrics.inc("jobs_timeout")
                logger.error(f"Job {job.prompt_id} timed out")
                return

            # Adjust poll interval based on elapsed time
            if elapsed > fast_poll_duration:
                current_poll = slow_poll

            history = await self._fetch_history(job.prompt_id)
            if history:
                status = history.get("status", {})
                status_str = status.get("status_str", "")

                if status_str == "success" or "outputs" in history:
                    job.status = "completed"
                    job.completed_at = time.time()
                    job.outputs = self._extract_outputs(history.get("outputs", {}))
                    await self.metrics.inc("jobs_completed")
                    await self.metrics.observe("processing_time", job.processing_time)
                    logger.info(
                        f"Job {job.prompt_id} completed in {job.processing_time:.1f}s " f"({len(job.outputs)} outputs)"
                    )
                    return

                elif status_str == "error":
                    job.status = "error"
                    job.error_msg = status.get("messages", "Unknown error")
                    job.completed_at = time.time()
                    await self.metrics.inc("jobs_failed")
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
            # Could track queue depth here
            queue_remaining = status_data.get("status", {}).get("exec_info", {}).get("queue_remaining", 0)
            if queue_remaining > 0:
                await self.metrics.gauge("comfyui_queue_depth", float(queue_remaining))

    async def submit_job(self, payload: dict, config_meta: dict) -> ComfyUIJob:
        """Submit job with circuit breaker protection."""
        try:
            prompt_id = await self.circuit.call(self._post_prompt, payload)
            job = ComfyUIJob(prompt_id, payload, config_meta)
            job.queued_at = time.time()
            self._jobs[job.job_id] = job
            await self.metrics.inc("jobs_submitted")
            return job
        except CircuitBreakerOpenError:
            # Create failed job with circuit breaker status
            job = ComfyUIJob(
                prompt_id=f"cb-open-{uuid.uuid4().hex[:8]}",
                payload=payload,
                config_meta=config_meta,
            )
            job.status = "error"
            job.error_msg = "Circuit breaker OPEN - ComfyUI API unavailable"
            self._jobs[job.job_id] = job
            await self.metrics.inc("jobs_failed")
            return job

    async def run_job(self, payload: dict, config_meta: dict) -> ComfyUIJob:
        """Submit and fully process a single job (semaphore-guarded)."""
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
                job = ComfyUIJob(
                    prompt_id=f"exception-{uuid.uuid4().hex[:8]}",
                    payload=payload,
                    config_meta=meta,
                )
                job.status = "error"
                job.error_msg = str(e)
                job.completed_at = time.time()
                await self.metrics.inc("jobs_failed")

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
                job = ComfyUIJob(
                    prompt_id=f"batch-fail-{uuid.uuid4().hex[:8]}",
                    payload=payloads[i],
                    config_meta=config_metas[i],
                )
                job.status = "error"
                job.error_msg = str(res)
                job.completed_at = time.time()
                await self.metrics.inc("jobs_failed")
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

                await self.metrics.inc("download_bytes", total_bytes)
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
        """Graceful shutdown."""
        self._shutdown = True
        await self.stop_ws_listener()
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
