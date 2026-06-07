"""Tests for WebSocket streaming functionality."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from engine.websocket_stream import (
    WebSocketStreamManager,
    StreamEvent,
    StreamEventType,
    get_stream_manager,
    initialize_stream_manager,
)
from engine.api_server_ws import WebSocketHandler, get_ws_handler


@pytest.fixture
async def stream_manager():
    """Create a stream manager for testing."""
    manager = WebSocketStreamManager(
        heartbeat_interval=1.0,
        connection_timeout=5.0,
        max_connections=10,
    )
    await manager.start()
    yield manager
    await manager.stop()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


class TestStreamEvent:
    """Test StreamEvent dataclass."""

    def test_create_event(self):
        event = StreamEvent(
            event_type=StreamEventType.JOB_CREATED,
            job_id="test_123",
            data={"status": "pending"},
        )
        assert event.event_type == StreamEventType.JOB_CREATED
        assert event.job_id == "test_123"
        assert event.data["status"] == "pending"

    def test_to_dict(self):
        event = StreamEvent(
            event_type=StreamEventType.JOB_PROGRESS,
            job_id="test_123",
            data={"progress": 50},
        )
        d = event.to_dict()
        assert d["event"] == "job.progress"
        assert d["job_id"] == "test_123"
        assert d["data"]["progress"] == 50

    def test_to_json(self):
        event = StreamEvent(
            event_type=StreamEventType.JOB_COMPLETED,
            job_id="test_123",
        )
        json_str = event.to_json()
        parsed = json.loads(json_str)
        assert parsed["event"] == "job.completed"
        assert parsed["job_id"] == "test_123"

    def test_create_progress_event(self):
        event = WebSocketStreamManager.create_progress_event(
            job_id="test_123",
            progress=75.0,
            stage="sampling",
            extra_data={"step": 10},
        )
        assert event.event_type == StreamEventType.JOB_PROGRESS
        assert event.data["progress"] == 75.0
        assert event.data["stage"] == "sampling"
        assert event.data["step"] == 10


class TestWebSocketStreamManager:
    """Test WebSocketStreamManager."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        manager = WebSocketStreamManager()
        await manager.start()
        assert manager._running is True
        await manager.stop()
        assert manager._running is False

    @pytest.mark.asyncio
    async def test_register_connection(self, stream_manager, mock_websocket):
        result = await stream_manager.register_connection(
            "conn_1", mock_websocket
        )
        assert result is True
        assert "conn_1" in stream_manager._connections

    @pytest.mark.asyncio
    async def test_max_connections(self, stream_manager, mock_websocket):
        # Fill to capacity
        for i in range(10):
            await stream_manager.register_connection(
                f"conn_{i}", mock_websocket
            )

        # Next should fail
        result = await stream_manager.register_connection(
            "conn_overflow", mock_websocket
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_unregister_connection(self, stream_manager, mock_websocket):
        await stream_manager.register_connection("conn_1", mock_websocket)
        await stream_manager.unregister_connection("conn_1")
        assert "conn_1" not in stream_manager._connections

    @pytest.mark.asyncio
    async def test_broadcast_event(self, stream_manager, mock_websocket):
        await stream_manager.register_connection("conn_1", mock_websocket)

        event = StreamEvent(
            event_type=StreamEventType.JOB_CREATED,
            job_id="job_1",
        )
        sent = await stream_manager.broadcast_event(event)
        assert sent == 1
        mock_websocket.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_filtering(self, stream_manager, mock_websocket):
        # Register with specific event type filter
        await stream_manager.register_connection(
            "conn_1", mock_websocket, event_types=[StreamEventType.JOB_COMPLETED]
        )

        # Send unfiltered event - should not be delivered
        event = StreamEvent(
            event_type=StreamEventType.JOB_CREATED,
            job_id="job_1",
        )
        sent = await stream_manager.broadcast_event(event)
        assert sent == 0

        # Send filtered event - should be delivered
        event = StreamEvent(
            event_type=StreamEventType.JOB_COMPLETED,
            job_id="job_1",
        )
        sent = await stream_manager.broadcast_event(event)
        assert sent == 1

    @pytest.mark.asyncio
    async def test_job_filtering(self, stream_manager, mock_websocket):
        await stream_manager.register_connection(
            "conn_1", mock_websocket, job_ids=["job_1"]
        )

        # Wrong job ID
        event = StreamEvent(
            event_type=StreamEventType.JOB_PROGRESS,
            job_id="job_2",
        )
        sent = await stream_manager.broadcast_event(event)
        assert sent == 0

        # Correct job ID
        event = StreamEvent(
            event_type=StreamEventType.JOB_PROGRESS,
            job_id="job_1",
        )
        sent = await stream_manager.broadcast_event(event)
        assert sent == 1

    @pytest.mark.asyncio
    async def test_queue_event(self, stream_manager, mock_websocket):
        await stream_manager.register_connection("conn_1", mock_websocket)

        event = StreamEvent(
            event_type=StreamEventType.JOB_STARTED,
            job_id="job_1",
        )
        await stream_manager.queue_event(event)

        # Wait for event processor
        await asyncio.sleep(0.5)
        mock_websocket.send_text.assert_called()

    def test_get_stats(self, stream_manager):
        stats = stream_manager.get_stats()
        assert "total_connections" in stats
        assert "max_connections" in stats
        assert stats["max_connections"] == 10


class TestWebSocketHandler:
    """Test WebSocketHandler."""

    @pytest.mark.asyncio
    async def test_handle_connection(self, mock_websocket):
        handler = WebSocketHandler()

        # Simulate client disconnect after first message
        mock_websocket.receive_text.side_effect = [
            json.dumps({"type": "ping", "timestamp": 123}),
            asyncio.TimeoutError(),  # Simulate disconnect
        ]

        await handler.handle_connection(mock_websocket, "test_conn")

        mock_websocket.accept.assert_called_once()
        mock_websocket.send_text.assert_called()

    @pytest.mark.asyncio
    async def test_handle_subscribe(self, mock_websocket):
        handler = WebSocketHandler()
        await handler.stream_manager.start()

        await handler._handle_subscribe(
            "conn_1",
            {
                "event_types": ["job.created", "job.completed"],
                "job_ids": ["job_1"],
            },
        )

        # Verify subscription was updated
        assert StreamEventType.JOB_CREATED in handler.stream_manager._subscriptions["conn_1"]
        assert "job_1" in handler.stream_manager._job_subscriptions["conn_1"]

        await handler.stream_manager.stop()

    @pytest.mark.asyncio
    async def test_handle_ping(self, mock_websocket):
        handler = WebSocketHandler()
        await handler._handle_ping("conn_1", mock_websocket, {"timestamp": 123})

        mock_websocket.send_text.assert_called_once()
        sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
        assert sent_data["event"] == "pong"

    @pytest.mark.asyncio
    async def test_handle_get_stats(self, mock_websocket):
        handler = WebSocketHandler()
        await handler._handle_get_stats("conn_1", mock_websocket)

        mock_websocket.send_text.assert_called_once()
        sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
        assert sent_data["event"] == "system.status"
        assert "total_connections" in sent_data["data"]


class TestGlobalInstances:
    """Test global instance functions."""

    def test_get_stream_manager(self):
        manager = get_stream_manager()
        assert isinstance(manager, WebSocketStreamManager)

        # Should return same instance
        manager2 = get_stream_manager()
        assert manager is manager2

    @pytest.mark.asyncio
    async def test_initialize_stream_manager(self):
        manager = await initialize_stream_manager(
            heartbeat_interval=5.0,
            max_connections=100,
        )
        assert manager.heartbeat_interval == 5.0
        assert manager.max_connections == 100
        assert manager._running is True
        await manager.stop()

    def test_get_ws_handler(self):
        handler = get_ws_handler()
        assert isinstance(handler, WebSocketHandler)

        # Should return same instance
        handler2 = get_ws_handler()
        assert handler is handler2

    @pytest.mark.asyncio
    async def test_initialize_ws_handler(self):
        handler = await initialize_ws_handler()
        assert isinstance(handler, WebSocketHandler)
        assert handler.stream_manager is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
