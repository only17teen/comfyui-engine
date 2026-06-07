"""
ComfyUI Async Generation Engine v2.0 - Distributed Queue
Redis-backed queue for multi-GPU scaling across multiple engine instances.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)


# Try to import redis, provide fallback if not available
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis not installed, distributed queue unavailable. "
                   "Install: pip install redis")


@dataclass
class DistributedJob:
    """Job representation for distributed queue."""
    job_id: str
    payload: Dict[str, Any]
    config_meta: Dict[str, Any]
    priority: int = 2
    created_at: float = field(default_factory=time.time)
    worker_id: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    status: str = "pending"  # pending | claimed | running | completed | failed
    result: Optional[Dict] = None
    error: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "DistributedJob":
        return cls(**data)


class RedisQueue:
    """
    Redis-backed distributed queue for multi-GPU scaling.

    Features:
    - Priority queue with Redis sorted sets
    - Job claiming with worker IDs (prevents duplicate processing)
    - Dead letter queue for failed jobs
    - Job result storage
    - Queue depth monitoring
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_name: str = "comfyui:jobs",
        worker_id: Optional[str] = None,
        claim_timeout: float = 300.0,  # Job claimed but not completed
        max_retries: int = 3,
    ):
        if not REDIS_AVAILABLE:
            raise RuntimeError("redis package not installed. Run: pip install redis")

        self.redis_url = redis_url
        self.queue_name = queue_name
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self.claim_timeout = claim_timeout
        self.max_retries = max_retries

        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._shutdown: bool = False

        self._handlers: Dict[str, List[Callable]] = {}

        self.logger = logging.getLogger(f"{__name__}.RedisQueue")

    async def connect(self) -> None:
        """Connect to Redis."""
        self._redis = await aioredis.from_url(
            self.redis_url,
            decode_responses=True,
        )
        await self._redis.ping()
        self.logger.info(f"Connected to Redis: {self.redis_url}")

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        self._shutdown = True

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            await self._pubsub.close()

        if self._redis:
            await self._redis.close()
        self.logger.info("Disconnected from Redis")

    async def enqueue(
        self,
        payload: Dict[str, Any],
        config_meta: Dict[str, Any],
        priority: int = 2,
    ) -> str:
        """
        Add job to distributed queue.

        Returns:
            job_id: Unique job identifier.
        """
        job = DistributedJob(
            job_id=f"job_{uuid.uuid4().hex}",
            payload=payload,
            config_meta=config_meta,
            priority=priority,
        )

        # Store job data
        await self._redis.hset(
            f"{self.queue_name}:jobs",
            job.job_id,
            json.dumps(job.to_dict()),
        )

        # Add to priority queue (sorted set, lower score = higher priority)
        score = priority * 1000000000 + time.time()
        await self._redis.zadd(
            f"{self.queue_name}:pending",
            {job.job_id: score},
        )

        # Publish notification
        await self._redis.publish(
            f"{self.queue_name}:notifications",
            json.dumps({"type": "job_added", "job_id": job.job_id}),
        )

        self.logger.debug(f"Enqueued job: {job.job_id}")
        return job.job_id

    async def claim_job(self) -> Optional[DistributedJob]:
        """
        Claim next available job from queue.

        Returns:
            DistributedJob or None if queue empty.
        """
        # Get job with lowest score (highest priority)
        result = await self._redis.zpopmin(f"{self.queue_name}:pending", count=1)

        if not result:
            return None

        job_id = result[0][0]

        # Get job data
        job_data = await self._redis.hget(f"{self.queue_name}:jobs", job_id)
        if not job_data:
            self.logger.warning(f"Job data missing for {job_id}")
            return None

        job = DistributedJob.from_dict(json.loads(job_data))

        # Check if already claimed and not timed out
        if job.status == "claimed" and job.started_at:
            if time.time() - job.started_at < self.claim_timeout:
                # Re-queue with higher priority (lower score)
                score = (job.priority - 1) * 1000000000 + time.time()
                await self._redis.zadd(
                    f"{self.queue_name}:pending",
                    {job_id: score},
                )
                return await self.claim_job()  # Try next

        # Claim job
        job.status = "claimed"
        job.worker_id = self.worker_id
        job.started_at = time.time()

        await self._redis.hset(
            f"{self.queue_name}:jobs",
            job_id,
            json.dumps(job.to_dict()),
        )

        # Add to claimed set
        await self._redis.zadd(
            f"{self.queue_name}:claimed",
            {job_id: time.time()},
        )

        self.logger.info(f"Claimed job: {job_id}")
        return job

    async def complete_job(
        self,
        job_id: str,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark job as completed or failed."""
        job_data = await self._redis.hget(f"{self.queue_name}:jobs", job_id)
        if not job_data:
            return

        job = DistributedJob.from_dict(json.loads(job_data))
        job.completed_at = time.time()

        if error:
            job.status = "failed"
            job.error = error
            job.retry_count += 1

            if job.retry_count < self.max_retries:
                # Re-queue for retry with lower priority
                job.status = "pending"
                job.worker_id = None
                job.started_at = None
                score = (job.priority + 1) * 1000000000 + time.time()
                await self._redis.zadd(
                    f"{self.queue_name}:pending",
                    {job_id: score},
                )
                self.logger.info(f"Re-queued job for retry: {job_id}")
            else:
                # Move to dead letter queue
                await self._redis.zadd(
                    f"{self.queue_name}:dead",
                    {job_id: time.time()},
                )
                self.logger.warning(f"Job moved to dead letter queue: {job_id}")
        else:
            job.status = "completed"
            job.result = result

        # Update job data
        await self._redis.hset(
            f"{self.queue_name}:jobs",
            job_id,
            json.dumps(job.to_dict()),
        )

        # Remove from claimed
        await self._redis.zrem(f"{self.queue_name}:claimed", job_id)

        # Publish completion
        await self._redis.publish(
            f"{self.queue_name}:notifications",
            json.dumps({
                "type": "job_completed",
                "job_id": job_id,
                "status": job.status,
                "worker_id": self.worker_id,
            }),
        )

    async def get_queue_depth(self) -> Dict[str, int]:
        """Get queue statistics."""
        pending = await self._redis.zcard(f"{self.queue_name}:pending")
        claimed = await self._redis.zcard(f"{self.queue_name}:claimed")
        dead = await self._redis.zcard(f"{self.queue_name}:dead")

        return {
            "pending": pending,
            "claimed": claimed,
            "dead": dead,
            "total": pending + claimed + dead,
        }

    async def get_job_status(self, job_id: str) -> Optional[Dict]:
        """Get job status."""
        job_data = await self._redis.hget(f"{self.queue_name}:jobs", job_id)
        if job_data:
            return json.loads(job_data)
        return None

    async def cleanup_stale_claims(self) -> int:
        """Re-queue jobs claimed but not completed within timeout."""
        cutoff = time.time() - self.claim_timeout
        stale = await self._redis.zrangebyscore(
            f"{self.queue_name}:claimed",
            0,
            cutoff,
        )

        requeued = 0
        for job_id in stale:
            job_data = await self._redis.hget(f"{self.queue_name}:jobs", job_id)
            if job_data:
                job = DistributedJob.from_dict(json.loads(job_data))
                job.status = "pending"
                job.worker_id = None
                job.started_at = None

                score = job.priority * 1000000000 + time.time()
                await self._redis.zadd(
                    f"{self.queue_name}:pending",
                    {job_id: score},
                )
                await self._redis.zrem(f"{self.queue_name}:claimed", job_id)
                await self._redis.hset(
                    f"{self.queue_name}:jobs",
                    job_id,
                    json.dumps(job.to_dict()),
                )
                requeued += 1

        if requeued > 0:
            self.logger.info(f"Re-queued {requeued} stale jobs")
        return requeued

    async def start_cleanup_task(self, interval: float = 60.0) -> None:
        """Start background task to clean up stale claims."""
        async def _cleanup_loop():
            while not self._shutdown:
                await self.cleanup_stale_claims()
                await asyncio.sleep(interval)

        asyncio.create_task(_cleanup_loop())

    async def start_listener(self) -> None:
        """Start pub/sub listener for queue notifications."""
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(f"{self.queue_name}:notifications")

        async def _listen():
            async for message in self._pubsub.listen():
                if self._shutdown:
                    break
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await self._dispatch_event(data.get("type"), data)
                    except json.JSONDecodeError:
                        pass

        self._listener_task = asyncio.create_task(_listen())

    async def _dispatch_event(self, event_type: str, data: Dict) -> None:
        """Dispatch to registered handlers."""
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(data))
                else:
                    handler(data)
            except Exception as e:
                self.logger.error(f"Event handler error: {e}")

    def on(self, event_type: str, handler: Callable) -> None:
        """Register event handler."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def purge_dead_letter(self, max_age: float = 86400.0) -> int:
        """Remove old dead letter jobs."""
        cutoff = time.time() - max_age
        removed = await self._redis.zremrangebyscore(
            f"{self.queue_name}:dead",
            0,
            cutoff,
        )
        if removed > 0:
            self.logger.info(f"Purged {removed} dead letter jobs")
        return removed


class DistributedWorker:
    """
    Worker that processes jobs from distributed queue.

    Usage:
        worker = DistributedWorker(redis_url="redis://localhost:6379")
        await worker.connect()
        await worker.start(processing_func)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_name: str = "comfyui:jobs",
        poll_interval: float = 1.0,
        max_concurrent: int = 1,
    ):
        self.queue = RedisQueue(
            redis_url=redis_url,
            queue_name=queue_name,
        )
        self.poll_interval = poll_interval
        self.max_concurrent = max_concurrent
        self._shutdown = False
        self._processing_func: Optional[Callable] = None

    async def connect(self) -> None:
        await self.queue.connect()
        await self.queue.start_cleanup_task()
        await self.queue.start_listener()

    async def start(self, processing_func: Callable) -> None:
        """Start processing jobs from queue."""
        self._processing_func = processing_func
        self._shutdown = False

        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _process_job(job: DistributedJob) -> None:
            async with semaphore:
                try:
                    result = await processing_func(job.payload, job.config_meta)
                    await self.queue.complete_job(job.job_id, result=result)
                except Exception as e:
                    await self.queue.complete_job(job.job_id, error=str(e))

        while not self._shutdown:
            job = await self.queue.claim_job()
            if job:
                asyncio.create_task(_process_job(job))
            else:
                await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        """Stop worker."""
        self._shutdown = True
        await self.queue.disconnect()
