"""ComfyUI Async Generation Engine v5.0 - WebSocket Streaming
Real-time job progress updates and notifications via WebSocket.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Callable

logger = logging.getLogger(__name__)


class StreamEventType(Enum):
    """Types of WebSocket stream events."""

    JOB_CREATED = "job.created"
    JOB_QUEUED = "job.queued"
    JOB_STARTED = "job.started"
    JOB_PROGRESS = "job.progress"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_CANCELLED = "job.cancelled"
    QUEUE_UPDATE = "queue.update"
    METRICS_UPDATE = "metrics.update"
    SYSTEM_STATUS = "system.status"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"


@dataclass
class StreamEvent:
    """A single WebSocket stream event."""

    event_type: StreamEventType
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event_type.value,
            "timestamp": self.timestamp,
            "data": self.data,
            "job_id": self.job_id,
            "session_id": self.session_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class WebSocketStreamManager:
    """Manages WebSocket connections for real-time streaming.

    Features:
    - Multi-client connection management
    - Event broadcasting and filtering
    - Heartbeat/ping-pong for connection health
    - Automatic reconnection support
    - Subscription-based event filtering
    - Rate limiting per connection
    """

    def __init__(
        self,
        heartbeat_interval: float = 30.0,
        connection_timeout: float = 60.0,
        max_connections: int = 1000,
        rate_limit_per_second: float = 100.0,
    ):
        self.heartbeat_interval = heartbeat_interval
        self.connection_timeout = connection_timeout
        self.max_connections = max_connections
        self.rate_limit_per_second = rate_limit_per_second

        self._connections: dict[str, Any] = {}  # connection_id -> websocket
        self._subscriptions: dict[str, set[StreamEventType]] = (
            {}
        )  # connection_id -> event types
        self._job_subscriptions: dict[str, set[str]] = {}  # connection_id -> job_ids
        self._session_subscriptions: dict[str, set[str]] = (
            {}
        )  # connection_id -> session_ids
        self._last_activity: dict[str, float] = {}  # connection_id -> timestamp
        self._message_count: dict[str, int] = {}  # connection_id -> count
        self._lock = asyncio.Lock()
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        """Start the stream manager."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._event_processor())
        logger.info("WebSocket stream manager started")

    async def stop(self) -> None:
        """Stop the stream manager and close all connections."""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            for _conn_id, websocket in list(self._connections.items()):
                try:
                    await websocket.close()
                except Exception:
                    pass
            self._connections.clear()
            self._subscriptions.clear()
            self._job_subscriptions.clear()
            self._session_subscriptions.clear()

        logger.info("WebSocket stream manager stopped")

    async def register_connection(
        self,
        connection_id: str,
        websocket: Any,
        event_types: list[StreamEventType] | None = None,
        job_ids: list[str] | None = None,
        session_ids: list[str] | None = None,
    ) -> bool:
        """Register a new WebSocket connection."""
        async with self._lock:
            if len(self._connections) >= self.max_connections:
                logger.warning(f"Max connections reached ({self.max_connections})")
                return False

            self._connections[connection_id] = websocket
            self._subscriptions[connection_id] = set(event_types or [])
            self._job_subscriptions[connection_id] = set(job_ids or [])
            self._session_subscriptions[connection_id] = set(session_ids or [])
            self._last_activity[connection_id] = time.time()
            self._message_count[connection_id] = 0

        logger.info(f"WebSocket connection registered: {connection_id}")
        return True

    async def unregister_connection(self, connection_id: str) -> None:
        """Unregister a WebSocket connection."""
        async with self._lock:
            if connection_id in self._connections:
                del self._connections[connection_id]
            if connection_id in self._subscriptions:
                del self._subscriptions[connection_id]
            if connection_id in self._job_subscriptions:
                del self._job_subscriptions[connection_id]
            if connection_id in self._session_subscriptions:
                del self._session_subscriptions[connection_id]
            if connection_id in self._last_activity:
                del self._last_activity[connection_id]
            if connection_id in self._message_count:
                del self._message_count[connection_id]

        logger.info(f"WebSocket connection unregistered: {connection_id}")

    async def update_subscription(
        self,
        connection_id: str,
        event_types: list[StreamEventType] | None = None,
        job_ids: list[str] | None = None,
        session_ids: list[str] | None = None,
    ) -> bool:
        """Update subscription filters for a connection."""
        async with self._lock:
            if connection_id not in self._connections:
                return False

            if event_types is not None:
                self._subscriptions[connection_id] = set(event_types)
            if job_ids is not None:
                self._job_subscriptions[connection_id] = set(job_ids)
            if session_ids is not None:
                self._session_subscriptions[connection_id] = set(session_ids)

        return True

    async def broadcast_event(self, event: StreamEvent) -> int:
        """Broadcast an event to all subscribed connections.

        Returns:
            Number of connections that received the event.
        """
        sent_count = 0
        disconnected = []

        async with self._lock:
            for conn_id, websocket in self._connections.items():
                # Check if connection is subscribed to this event type
                subscriptions = self._subscriptions.get(conn_id, set())
                if subscriptions and event.event_type not in subscriptions:
                    continue

                # Check if connection is subscribed to this job
                job_subs = self._job_subscriptions.get(conn_id, set())
                if job_subs and event.job_id and event.job_id not in job_subs:
                    continue

                # Check if connection is subscribed to this session
                session_subs = self._session_subscriptions.get(conn_id, set())
                if (
                    session_subs
                    and event.session_id
                    and event.session_id not in session_subs
                ):
                    continue

                # Check rate limit
                if self._message_count.get(conn_id, 0) > self.rate_limit_per_second:
                    continue

                try:
                    await websocket.send_text(event.to_json())
                    self._message_count[conn_id] = (
                        self._message_count.get(conn_id, 0) + 1
                    )
                    self._last_activity[conn_id] = time.time()
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"Failed to send to {conn_id}: {e}")
                    disconnected.append(conn_id)

        # Clean up disconnected clients
        for conn_id in disconnected:
            await self.unregister_connection(conn_id)

        return sent_count

    async def send_to_connection(
        self,
        connection_id: str,
        event: StreamEvent,
    ) -> bool:
        """Send an event to a specific connection."""
        async with self._lock:
            websocket = self._connections.get(connection_id)
            if not websocket:
                return False

            try:
                await websocket.send_text(event.to_json())
                self._last_activity[connection_id] = time.time()
                return True
            except Exception as e:
                logger.warning(f"Failed to send to {connection_id}: {e}")
                return False

    async def queue_event(self, event: StreamEvent) -> None:
        """Queue an event for asynchronous broadcasting."""
        await self._event_queue.put(event)

    async def _event_processor(self) -> None:
        """Process queued events."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0,
                )
                await self.broadcast_event(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Event processor error: {e}")

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats and check connection health."""
        while self._running:
            try:
                await asyncio.sleep(self.heartbeat_interval)

                current_time = time.time()
                disconnected = []

                # Send ping to all connections
                ping_event = StreamEvent(
                    event_type=StreamEventType.PING,
                    data={"timestamp": current_time},
                )

                async with self._lock:
                    for conn_id, websocket in self._connections.items():
                        # Check timeout
                        last_activity = self._last_activity.get(conn_id, 0)
                        if current_time - last_activity > self.connection_timeout:
                            disconnected.append(conn_id)
                            continue

                        # Reset message count
                        self._message_count[conn_id] = 0

                        try:
                            await websocket.send_text(ping_event.to_json())
                        except Exception:
                            disconnected.append(conn_id)

                # Clean up disconnected clients
                for conn_id in disconnected:
                    await self.unregister_connection(conn_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get stream manager statistics."""
        return {
            "total_connections": len(self._connections),
            "max_connections": self.max_connections,
            "running": self._running,
            "heartbeat_interval": self.heartbeat_interval,
            "connection_timeout": self.connection_timeout,
            "rate_limit_per_second": self.rate_limit_per_second,
            "event_queue_size": self._event_queue.qsize(),
            "subscriptions_by_type": {
                event_type.value: sum(
                    1 for subs in self._subscriptions.values() if event_type in subs
                )
                for event_type in StreamEventType
            },
        }

    async def handle_pong(self, connection_id: str, data: dict[str, Any]) -> None:
        """Handle pong response from client."""
        async with self._lock:
            self._last_activity[connection_id] = time.time()

    @staticmethod
    def create_job_event(
        event_type: StreamEventType,
        job_id: str,
        session_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> StreamEvent:
        """Factory method for creating job-related events."""
        return StreamEvent(
            event_type=event_type,
            job_id=job_id,
            session_id=session_id,
            data=data or {},
        )

    @staticmethod
    def create_progress_event(
        job_id: str,
        progress: float,
        stage: str,
        session_id: str | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> StreamEvent:
        """Factory method for creating progress events."""
        data = {
            "progress": progress,
            "stage": stage,
            **(extra_data or {}),
        }
        return StreamEvent(
            event_type=StreamEventType.JOB_PROGRESS,
            job_id=job_id,
            session_id=session_id,
            data=data,
        )


# Global stream manager instance
_global_stream_manager: WebSocketStreamManager | None = None


def get_stream_manager() -> WebSocketStreamManager:
    """Get or create global stream manager."""
    global _global_stream_manager
    if _global_stream_manager is None:
        _global_stream_manager = WebSocketStreamManager()
    return _global_stream_manager


async def initialize_stream_manager(
    heartbeat_interval: float = 30.0,
    connection_timeout: float = 60.0,
    max_connections: int = 1000,
) -> WebSocketStreamManager:
    """Initialize and start the stream manager."""
    global _global_stream_manager
    _global_stream_manager = WebSocketStreamManager(
        heartbeat_interval=heartbeat_interval,
        connection_timeout=connection_timeout,
        max_connections=max_connections,
    )
    await _global_stream_manager.start()
    return _global_stream_manager
