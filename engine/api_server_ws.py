"""
ComfyUI Async Generation Engine v5.0 - WebSocket Streaming Integration
WebSocket endpoint for real-time job progress and status updates.
Integrates with the WebSocketStreamManager and REST API server.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from .websocket_stream import (
    WebSocketStreamManager,
    StreamEvent,
    StreamEventType,
    get_stream_manager,
)

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """
    Handles WebSocket connections for the API server.
    Bridges FastAPI WebSocket with WebSocketStreamManager.
    """

    def __init__(self, stream_manager: Optional[WebSocketStreamManager] = None):
        self.stream_manager = stream_manager or get_stream_manager()
        self._connection_counter = 0

    async def handle_connection(
        self,
        websocket: WebSocket,
        connection_id: Optional[str] = None,
    ) -> None:
        """Handle a WebSocket connection lifecycle."""
        self._connection_counter += 1
        conn_id = connection_id or f"ws_{self._connection_counter}_{int(time.time())}"

        await websocket.accept()
        logger.info(f"WebSocket connection accepted: {conn_id}")

        # Register with stream manager
        registered = await self.stream_manager.register_connection(
            connection_id=conn_id,
            websocket=websocket,
        )

        if not registered:
            await websocket.close(code=1013, reason="Server at capacity")
            logger.warning(f"WebSocket connection rejected: {conn_id}")
            return

        try:
            # Send welcome message
            welcome_event = StreamEvent(
                event_type=StreamEventType.SYSTEM_STATUS,
                data={
                    "message": "Connected to ComfyUI Engine WebSocket",
                    "connection_id": conn_id,
                    "version": "5.0.0",
                },
            )
            await websocket.send_text(welcome_event.to_json())

            # Main message loop
            while True:
                try:
                    message = await websocket.receive_text()
                    await self._handle_message(conn_id, websocket, message)
                except WebSocketDisconnect:
                    logger.info(f"WebSocket disconnected: {conn_id}")
                    break
                except Exception as e:
                    logger.error(f"WebSocket message error: {e}")
                    error_event = StreamEvent(
                        event_type=StreamEventType.ERROR,
                        data={"error": str(e)},
                    )
                    try:
                        await websocket.send_text(error_event.to_json())
                    except Exception:
                        break

        finally:
            await self.stream_manager.unregister_connection(conn_id)
            logger.info(f"WebSocket connection closed: {conn_id}")

    async def _handle_message(
        self,
        connection_id: str,
        websocket: WebSocket,
        message: str,
    ) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")

            if msg_type == "subscribe":
                await self._handle_subscribe(connection_id, data)
            elif msg_type == "unsubscribe":
                await self._handle_unsubscribe(connection_id, data)
            elif msg_type == "ping":
                await self._handle_ping(connection_id, websocket, data)
            elif msg_type == "pong":
                await self.stream_manager.handle_pong(connection_id, data)
            elif msg_type == "get_stats":
                await self._handle_get_stats(connection_id, websocket)
            else:
                logger.warning(f"Unknown WebSocket message type: {msg_type}")

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {message[:200]}")
            error_event = StreamEvent(
                event_type=StreamEventType.ERROR,
                data={"error": "Invalid JSON"},
            )
            await websocket.send_text(error_event.to_json())

    async def _handle_subscribe(
        self,
        connection_id: str,
        data: Dict[str, Any],
    ) -> None:
        """Handle subscription request."""
        event_types = data.get("event_types", [])
        job_ids = data.get("job_ids", [])
        session_ids = data.get("session_ids", [])

        # Convert string event types to StreamEventType enum
        parsed_event_types = []
        for et in event_types:
            try:
                parsed_event_types.append(StreamEventType(et))
            except ValueError:
                logger.warning(f"Unknown event type: {et}")

        await self.stream_manager.update_subscription(
            connection_id=connection_id,
            event_types=parsed_event_types or None,
            job_ids=job_ids or None,
            session_ids=session_ids or None,
        )

        logger.info(
            f"Updated subscription for {connection_id}: "
            f"events={len(parsed_event_types)}, jobs={len(job_ids)}, sessions={len(session_ids)}"
        )

    async def _handle_unsubscribe(
        self,
        connection_id: str,
        data: Dict[str, Any],
    ) -> None:
        """Handle unsubscription request."""
        await self.stream_manager.update_subscription(
            connection_id=connection_id,
            event_types=[],
            job_ids=[],
            session_ids=[],
        )
        logger.info(f"Cleared subscription for {connection_id}")

    async def _handle_ping(
        self,
        connection_id: str,
        websocket: WebSocket,
        data: Dict[str, Any],
    ) -> None:
        """Handle ping message."""
        pong_event = StreamEvent(
            event_type=StreamEventType.PONG,
            data={"timestamp": time.time(), "client_timestamp": data.get("timestamp")},
        )
        await websocket.send_text(pong_event.to_json())

    async def _handle_get_stats(
        self,
        connection_id: str,
        websocket: WebSocket,
    ) -> None:
        """Handle stats request."""
        stats = self.stream_manager.get_stats()
        stats_event = StreamEvent(
            event_type=StreamEventType.SYSTEM_STATUS,
            data=stats,
        )
        await websocket.send_text(stats_event.to_json())


# Global handler instance
_global_ws_handler: Optional[WebSocketHandler] = None


def get_ws_handler() -> WebSocketHandler:
    """Get or create global WebSocket handler."""
    global _global_ws_handler
    if _global_ws_handler is None:
        _global_ws_handler = WebSocketHandler()
    return _global_ws_handler


async def initialize_ws_handler(
    stream_manager: Optional[WebSocketStreamManager] = None,
) -> WebSocketHandler:
    """Initialize global WebSocket handler."""
    global _global_ws_handler
    _global_ws_handler = WebSocketHandler(stream_manager)
    return _global_ws_handler
