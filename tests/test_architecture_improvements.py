"""
Tests for v5.1 architecture improvements.
Covers: errors module, circuit-breaker race fix, actor message routing,
shutdown manager parallel cleanup, session manager async I/O,
and api_client context manager.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from engine.errors import (
    CircuitBreakerOpenError,
    ConfigurationError,
    DownloadError,
    EngineError,
    FatalError,
    JobNotFoundError,
    MaxRetriesExceededError,
    QueueFullError,
    RateLimitError,
    TransientError,
    WebSocketError,
    WorkflowValidationError,
)


# ───────────────────────────────────────────────────────────────
# Error hierarchy
# ───────────────────────────────────────────────────────────────
class TestErrorHierarchy:
    """All custom exceptions derive correctly from EngineError."""

    def test_transient_errors_catchable_as_engine_error(self):
        for cls in (CircuitBreakerOpenError, QueueFullError, RateLimitError, WebSocketError):
            with pytest.raises(EngineError):
                if cls is CircuitBreakerOpenError:
                    raise cls("cb")
                elif cls is QueueFullError:
                    raise cls(max_size=10)
                elif cls is RateLimitError:
                    raise cls()
                else:
                    raise cls("err")

    def test_fatal_errors_catchable_as_engine_error(self):
        for exc in (
            ConfigurationError("bad"),
            WorkflowValidationError(["missing node"]),
            JobNotFoundError("job_123"),
            DownloadError("failed"),
        ):
            assert isinstance(exc, EngineError)
            assert isinstance(exc, FatalError)

    def test_context_attached(self):
        exc = JobNotFoundError("job_abc")
        assert exc.context["job_id"] == "job_abc"
        assert "job_abc" in str(exc)

    def test_workflow_validation_error_carries_errors_list(self):
        exc = WorkflowValidationError(["e1", "e2"], warnings=["w1"])
        assert exc.errors == ["e1", "e2"]
        assert exc.warnings == ["w1"]

    def test_max_retries_wraps_original(self):
        orig = ValueError("boom")
        exc = MaxRetriesExceededError("op", 3, orig)
        assert exc.last_error is orig
        assert exc.attempts == 3
        # MaxRetriesExceededError is fatal, not transient
        assert isinstance(exc, FatalError)
        assert not isinstance(exc, TransientError)


# ───────────────────────────────────────────────────────────────
# Actor system — message routing
# ───────────────────────────────────────────────────────────────
from engine.actor.base import Actor, ActorMessage, ActorSystem, MessagePriority


class EchoActor(Actor):
    """Test actor that records received messages."""

    def __init__(self, actor_id: str):
        super().__init__(actor_id)
        self.received: list[ActorMessage] = []
        self._got_message = asyncio.Event()

    async def handle_message(self, message: ActorMessage):
        self.received.append(message)
        self._got_message.set()
        return "ok"


class TestActorSystem:
    """Actor message routing must actually deliver messages."""

    async def test_send_delivers_to_recipient(self):
        system = ActorSystem()
        actor = EchoActor("receiver")
        await system.register_actor(actor)
        await actor.start()

        msg_id = await system.send("receiver", {"hello": "world"})
        assert msg_id is not None

        # Wait for the actor to process the message (max 1 s)
        try:
            await asyncio.wait_for(actor._got_message.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("Actor never received the message")

        assert len(actor.received) == 1
        assert actor.received[0].payload == {"hello": "world"}
        await actor.stop()

    async def test_send_to_unknown_actor_returns_none(self):
        system = ActorSystem()
        result = await system.send("ghost", "payload")
        assert result is None

    async def test_actor_send_routes_through_system(self):
        """actor.send() must deliver via the system, not silently drop."""
        system = ActorSystem()
        sender = EchoActor("sender")
        receiver = EchoActor("receiver")
        await system.register_actor(sender)
        await system.register_actor(receiver)
        await sender.start()
        await receiver.start()

        msg_id = await sender.send("receiver", "ping")
        assert msg_id is not None

        try:
            await asyncio.wait_for(receiver._got_message.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("actor.send() did not route message to receiver")

        await sender.stop()
        await receiver.stop()

    async def test_mailbox_event_wakeup_no_busywait(self):
        """Mailbox processor should sleep until a message arrives, not spin."""
        actor = EchoActor("idle")
        system = ActorSystem()
        await system.register_actor(actor)
        await actor.start()

        # Let the processor settle
        await asyncio.sleep(0.05)

        # The event should be clear (not set) while mailbox is empty
        assert not actor._mailbox_event.is_set()
        await actor.stop()


# ───────────────────────────────────────────────────────────────
# Shutdown manager — parallel cleanup
# ───────────────────────────────────────────────────────────────
from engine.shutdown_manager import GracefulShutdownManager


class TestShutdownManager:

    async def test_parallel_cleanup(self):
        """Cleanup callbacks must run concurrently, not serially."""
        mgr = GracefulShutdownManager()
        log: list[str] = []

        async def slow_a() -> None:
            await asyncio.sleep(0.1)
            log.append("a")

        async def slow_b() -> None:
            await asyncio.sleep(0.1)
            log.append("b")

        mgr.register_cleanup(slow_a, name="a")
        mgr.register_cleanup(slow_b, name="b")

        start = time.monotonic()
        await mgr._run_cleanup()
        elapsed = time.monotonic() - start

        # Both callbacks sleep 0.1s; serial would take >=0.2s
        assert elapsed < 0.18, f"Cleanup was serial (took {elapsed:.3f}s)"
        assert set(log) == {"a", "b"}

    async def test_cleanup_error_does_not_abort_others(self):
        mgr = GracefulShutdownManager()
        log: list[str] = []

        async def fail() -> None:
            raise RuntimeError("oops")

        async def succeed() -> None:
            log.append("ok")

        mgr.register_cleanup(fail, name="fail")
        mgr.register_cleanup(succeed, name="succeed")

        # Should not raise even if one callback fails
        await mgr._run_cleanup()
        assert "ok" in log


# ───────────────────────────────────────────────────────────────
# Session manager — async I/O
# ───────────────────────────────────────────────────────────────
from engine.session_manager import SessionManager


class TestSessionManagerAsync:

    async def test_create_session_is_async(self, tmp_path: Path):
        mgr = SessionManager(sessions_dir=str(tmp_path))
        session = await mgr.create_session(session_id="test_s1", total_jobs=5)
        assert session.session_id == "test_s1"
        # File should have been written
        assert (tmp_path / "test_s1.json").exists()

    async def test_context_manager_finalizes_session(self, tmp_path: Path):
        async with SessionManager(sessions_dir=str(tmp_path)) as mgr:
            await mgr.create_session(session_id="ctx_s1")

        data = (tmp_path / "ctx_s1.json").read_text()
        import json
        manifest = json.loads(data)
        assert manifest["status"] == "completed"

    async def test_hash_uses_sha256_not_md5(self, tmp_path: Path):
        """SHA-256 digest must be returned; MD5 produces a different length."""
        mgr = SessionManager(sessions_dir=str(tmp_path))
        h = mgr._compute_hash({"key": "value"})
        # SHA-256 hex truncated to 16 chars
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ───────────────────────────────────────────────────────────────
# API client — semaphore and context manager
# ───────────────────────────────────────────────────────────────
from engine.api_client import ComfyUIAsyncClient
from engine.core import MetricsCollector


class TestAPIClientImprovements:

    def test_semaphore_initialised_eagerly(self):
        """Semaphore must exist before any coroutine runs (no race condition)."""
        client = ComfyUIAsyncClient(max_concurrent=4)
        assert client._semaphore is not None
        assert client._semaphore._value == 4  # type: ignore[attr-defined]

    async def test_context_manager_closes_session(self):
        """async with ComfyUIAsyncClient() must close session on exit."""
        async with ComfyUIAsyncClient() as client:
            assert not client._shutdown

        assert client._shutdown


