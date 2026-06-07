"""
ComfyUI Async Generation Engine v2.0 - WebSocket Manager
Production-grade WebSocket with auto-reconnection, heartbeat, and health monitoring.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp


logger = logging.getLogger(__name__)


class WSState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    FAILED = auto()


@dataclass
class WSConfig:
    """WebSocket connection configuration."""
    url: str = "ws://127.0.0.1:8188/ws"
    heartbeat_interval: float = 30.0
    heartbeat_timeout: float = 10.0
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 60.0
    reconnect_max_attempts: int = 10
    reconnect_exponential_base: float = 2.0
    message_timeout: float = 5.0
    connection_timeout: float = 10.0


@dataclass
class WSMessage:
    """Parsed WebSocket message."""
    type: str
    data: Dict[str, Any]
    raw: str
    timestamp: float = field(default_factory=time.time)


class WebSocketManager:
    """
    Production-grade WebSocket manager for ComfyUI.

    Features:
    - Auto-reconnection with exponential backoff
    - Heartbeat/ping-pong health checks
    - Message queueing during disconnections
    - Event-driven architecture
    - Connection state machine
    """

    def __init__(
        self,
        config: Optional[WSConfig] = None,
        session: Optional[aiohttp.ClientSession] = None,
        metrics=None,
    ):
        self.config = config or WSConfig()
        self._session = session
        self.metrics = metrics

        self._state = WSState.DISCONNECTED
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._last_pong: float = 0.0
        self._connection_attempts: int = 0
        self._shutdown: bool = False
        self._lock = asyncio.Lock()

        self.logger = logging.getLogger(f"{__name__}.WSManager")

    @property
    def state(self) -> WSState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == WSState.CONNECTED and self._ws is not None

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.connection_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def connect(self) -> bool:
        """Establish WebSocket connection with retry."""
        async with self._lock:
            if self._state in (WSState.CONNECTED, WSState.CONNECTING):
                return True
            self._state = WSState.CONNECTING

        try:
            session = self._get_session()
            self.logger.info(f"Connecting to {self.config.url}")

            self._ws = await session.ws_connect(
                self.config.url,
                heartbeat=self.config.heartbeat_interval,
                autoping=True,
            )

            self._state = WSState.CONNECTED
            self._connection_attempts = 0
            self._last_pong = time.time()

            self.logger.info("WebSocket connected")
            if self.metrics:
                await self.metrics.inc("ws_connections_established")

            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            return True

        except Exception as e:
            self.logger.error(f"WebSocket connection failed: {e}")
            self._state = WSState.DISCONNECTED
            if self.metrics:
                await self.metrics.inc("ws_connections_failed")
            return False

    async def disconnect(self) -> None:
        """Graceful disconnection."""
        self._shutdown = True

        # Cancel background tasks
        for task in (self._heartbeat_task, self._receive_task, self._reconnect_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close websocket
        if self._ws and not self._ws.closed:
            await self._ws.close()

        self._state = WSState.DISCONNECTED
        self.logger.info("WebSocket disconnected")

    async def _receive_loop(self) -> None:
        """Main message receive loop."""
        while not self._shutdown and self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive(timeout=self.config.message_timeout)

                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_text_message(msg.data)

                elif msg.type == aiohttp.WSMsgType.BINARY:
                    self.logger.debug(f"Received binary message: {len(msg.data)} bytes")

                elif msg.type == aiohttp.WSMsgType.PING:
                    self._last_pong = time.time()
                    if self.metrics:
                        await self.metrics.inc("ws_pongs_received")

                elif msg.type == aiohttp.WSMsgType.PONG:
                    self._last_pong = time.time()
                    if self.metrics:
                        await self.metrics.inc("ws_pongs_received")

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    self.logger.warning(f"WebSocket closed: {msg.type}")
                    break

            except asyncio.TimeoutError:
                # No message received, check health
                if time.time() - self._last_pong > self.config.heartbeat_timeout * 2:
                    self.logger.warning("WebSocket heartbeat timeout")
                    break

            except Exception as e:
                self.logger.error(f"Receive loop error: {e}")
                break

        # Connection lost, trigger reconnection
        if not self._shutdown:
            self._state = WSState.DISCONNECTED
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _handle_text_message(self, data: str) -> None:
        """Parse and dispatch text message."""
        try:
            parsed = json.loads(data)
            msg_type = parsed.get("type", "unknown")

            ws_msg = WSMessage(
                type=msg_type,
                data=parsed.get("data", {}),
                raw=data,
            )

            # Update metrics
            if self.metrics:
                await self.metrics.inc("ws_messages_received")

            # Dispatch to handlers
            await self._dispatch_event(msg_type, ws_msg)

            # Handle specific ComfyUI message types
            if msg_type == "status":
                await self._handle_status_message(ws_msg)
            elif msg_type == "execution_start":
                await self._handle_execution_start(ws_msg)
            elif msg_type == "executing":
                await self._handle_executing(ws_msg)
            elif msg_type == "execution_error":
                await self._handle_execution_error(ws_msg)
            elif msg_type == "execution_cached":
                await self._handle_execution_cached(ws_msg)

        except json.JSONDecodeError:
            self.logger.warning(f"Invalid JSON received: {data[:200]}")
        except Exception as e:
            self.logger.error(f"Message handling error: {e}")

    async def _handle_status_message(self, msg: WSMessage) -> None:
        """Handle ComfyUI status updates."""
        data = msg.data
        status = data.get("status", {})
        exec_info = status.get("exec_info", {})
        queue_remaining = exec_info.get("queue_remaining", 0)

        if self.metrics:
            await self.metrics.gauge("comfyui_queue_depth", float(queue_remaining))

        self.logger.debug(f"Queue remaining: {queue_remaining}")

    async def _handle_execution_start(self, msg: WSMessage) -> None:
        """Handle execution start event."""
        prompt_id = msg.data.get("prompt_id")
        self.logger.info(f"Execution started: {prompt_id}")
        if self.metrics:
            await self.metrics.inc("ws_execution_started")

    async def _handle_executing(self, msg: WSMessage) -> None:
        """Handle executing node event."""
        data = msg.data
        node = data.get("node")
        prompt_id = data.get("prompt_id")
        self.logger.debug(f"Executing node {node} for {prompt_id}")

    async def _handle_execution_error(self, msg: WSMessage) -> None:
        """Handle execution error event."""
        data = msg.data
        prompt_id = data.get("prompt_id")
        error = data.get("error", {})
        self.logger.error(f"Execution error for {prompt_id}: {error}")
        if self.metrics:
            await self.metrics.inc("ws_execution_errors")

    async def _handle_execution_cached(self, msg: WSMessage) -> None:
        """Handle execution cached event."""
        data = msg.data
        prompt_id = data.get("prompt_id")
        self.logger.info(f"Execution cached: {prompt_id}")

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to keep connection alive."""
        while not self._shutdown and self.is_connected:
            try:
                if self._ws and not self._ws.closed:
                    await self._ws.ping()
                    if self.metrics:
                        await self.metrics.inc("ws_pings_sent")

                await asyncio.sleep(self.config.heartbeat_interval)

            except Exception as e:
                self.logger.debug(f"Heartbeat error: {e}")
                break

    async def _reconnect(self) -> None:
        """Reconnection with exponential backoff."""
        self._state = WSState.RECONNECTING

        while not self._shutdown and self._connection_attempts < self.config.reconnect_max_attempts:
            self._connection_attempts += 1

            # Calculate delay with exponential backoff and jitter
            delay = min(
                self.config.reconnect_base_delay * (
                    self.config.reconnect_exponential_base ** (self._connection_attempts - 1)
                ),
                self.config.reconnect_max_delay,
            )
            jitter = delay * 0.1 * (2 * (time.time() % 1) - 1)
            actual_delay = max(0.1, delay + jitter)

            self.logger.info(
                f"Reconnection attempt {self._connection_attempts}/"
                f"{self.config.reconnect_max_attempts} in {actual_delay:.1f}s"
            )

            await asyncio.sleep(actual_delay)

            if await self.connect():
                self.logger.info("Reconnection successful")
                return

        # Max attempts reached
        self._state = WSState.FAILED
        self.logger.error(f"WebSocket failed after {self._connection_attempts} reconnection attempts")
        if self.metrics:
            await self.metrics.inc("ws_reconnect_failed")

    async def _dispatch_event(self, event_type: str, msg: WSMessage) -> None:
        """Dispatch message to registered handlers."""
        handlers = self._event_handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(msg))
                else:
                    handler(msg)
            except Exception as e:
                self.logger.error(f"Event handler error: {e}")

    def on(self, event_type: str, handler: Callable) -> None:
        """Register event handler."""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)

    def off(self, event_type: str, handler: Optional[Callable] = None) -> None:
        """Unregister event handler."""
        if event_type in self._event_handlers:
            if handler is None:
                self._event_handlers[event_type] = []
            else:
                self._event_handlers[event_type] = [
                    h for h in self._event_handlers[event_type] if h != handler
                ]

    async def send(self, data: Dict[str, Any]) -> bool:
        """Send message with queueing during disconnections."""
        if self.is_connected and self._ws:
            try:
                await self._ws.send_json(data)
                if self.metrics:
                    await self.metrics.inc("ws_messages_sent")
                return True
            except Exception as e:
                self.logger.error(f"Send error: {e}")

        # Queue for later if disconnected
        await self._message_queue.put(data)
        self.logger.debug("Message queued for later delivery")
        return False

    async def flush_queue(self) -> int:
        """Flush queued messages after reconnection."""
        flushed = 0
        while not self._message_queue.empty() and self.is_connected:
            try:
                data = self._message_queue.get_nowait()
                if await self.send(data):
                    flushed += 1
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                self.logger.error(f"Flush error: {e}")
                break

        if flushed > 0:
            self.logger.info(f"Flushed {flushed} queued messages")
        return flushed

    def get_stats(self) -> Dict[str, Any]:
        """Return connection statistics."""
        return {
            "state": self._state.name,
            "connected": self.is_connected,
            "connection_attempts": self._connection_attempts,
            "last_pong_age": time.time() - self._last_pong if self._last_pong else None,
            "queued_messages": self._message_queue.qsize(),
            "registered_handlers": {
                event: len(handlers)
                for event, handlers in self._event_handlers.items()
            },
        }
