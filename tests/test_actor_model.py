"""
Test for Actor Model Implementation
Tests Phase 1 of Kiro Protocol v3.0: Actor Model Concurrency Architecture
"""

import asyncio
import pytest
from engine.actor.base import Actor, ActorMessage, MessagePriority
from engine.actor.router import ShardedActorRouter
from engine.actor.manager import ActorManager, JobProcessorActor


class TestActor(Actor):
    """Test actor for verification."""

    def __init__(self, actor_id: str):
        super().__init__(actor_id)
        self.received_messages = []
        self.register_handler("test_message", self._handle_test_message)
        self.register_handler("get_received", self._handle_get_received)

    async def handle_message(self, message: ActorMessage) -> Any:
        """Handle incoming messages."""
        self.received_messages.append(message)
        return {"status": "received", "actor_id": self.actor_id}

    async def _handle_test_message(self, message: ActorMessage) -> Any:
        """Handle test message."""
        return {
            "status": "test_received",
            "actor_id": self.actor_id,
            "payload": message.payload,
        }

    async def _handle_get_received(self, message: ActorMessage) -> Any:
        """Handle get received messages request."""
        return {
            "count": len(self.received_messages),
            "messages": [msg.payload for msg in self.received_messages[-5:]],  # Last 5
        }


@pytest.mark.asyncio
async def test_actor_creation_and_messaging():
    """Test basic actor creation and message passing."""
    actor = TestActor("test_actor")
    await actor.start()

    # Send a test message
    message = ActorMessage(
        sender="test_sender",
        recipient="test_actor",
        payload={"type": "test_message", "data": "hello world"},
        priority=MessagePriority.NORMAL,
    )

    # Enqueue the message
    result = await actor.enqueue(message)
    assert result is True

    # Give it time to process
    await asyncio.sleep(0.1)

    # Check that message was received
    stats = await actor.get_stats()
    assert stats.messages_processed >= 1

    await actor.stop()


@pytest.mark.asyncio
async def test_actor_router():
    """Test the sharded actor router."""
    router = ShardedActorRouter(num_shards=4)

    # Create test actors
    actor1 = TestActor("actor_1")
    actor2 = TestActor("actor_2")

    # Register actors
    result1 = await router.register_actor(actor1)
    result2 = await router.register_actor(actor2)
    assert result1 is True
    assert result2 is True

    # Get actors
    retrieved_actor1 = await router.get_actor("actor_1")
    retrieved_actor2 = await router.get_actor("actor_2")
    assert retrieved_actor1 is not None
    assert retrieved_actor2 is not None
    assert retrieved_actor1.actor_id == "actor_1"
    assert retrieved_actor2.actor_id == "actor_2"

    # Test message routing
    message = ActorMessage(
        sender="test",
        recipient="actor_1",
        payload={"type": "test_message", "data": "router test"},
        priority=MessagePriority.HIGH,
    )

    start_actors_processed = actor1.stats.messages_processed
    success = await router.route_message("actor_1", message)
    assert success is True

    # Give time to process
    await asyncio.sleep(0.1)

    # Check that actor processed the message
    stats = await actor1.get_stats()
    assert stats.messages_processed > start_actors_processed

    # Cleanup
    await actor1.stop()
    await actor2.stop()


@pytest.mark.asyncio
async def test_actor_manager():
    """Test the actor manager."""
    manager = ActorManager()
    await manager.initialize()

    # Create and register an actor through manager
    actor = JobProcessorActor("job_processor_1")
    success = manager.register_actor(actor)
    assert success is True

    # Get the actor
    retrieved_actor = manager.get_actor("job_processor_1")
    assert retrieved_actor is not None
    assert retrieved_actor.actor_id == "job_processor_1"

    # Send a message via manager
    message_id = await manager.send_message(
        "job_processor_1",
        {"type": "process_job", "job_data": {"job_id": "test_job_123", "data": "test"}},
    )
    # Note: send_message is simplified in our implementation

    # Check stats
    stats = manager.get_stats()
    assert stats["managed_actors"] >= 1

    await manager.shutdown()


@pytest.mark.asyncio
async def test_priority_mailbox():
    """Test that priority mailbox works correctly."""
    actor = TestActor("priority_test")
    await actor.start()

    # Send messages with different priorities
    messages = []
    for i in range(5):
        msg = ActorMessage(
            sender="test",
            recipient="priority_test",
            payload={"type": "test_message", "data": f"message_{i}"},
            priority=MessagePriority.LOW if i % 2 == 0 else MessagePriority.HIGH,
        )
        messages.append(msg)
        await actor.enqueue(msg)

    # Give time to process
    await asyncio.sleep(0.2)

    # Check that all messages were processed
    stats = await actor.get_stats()
    assert stats.messages_processed >= 5

    await actor.stop()


if __name__ == "__main__":
    # Run tests manually
    async def run_tests():
        await test_actor_creation_and_messaging()
        print("✓ Basic actor creation and messaging test passed")

        await test_actor_router()
        print("✓ Actor router test passed")

        await test_actor_manager()
        print("✓ Actor manager test passed")

        await test_priority_mailbox()
        print("✓ Priority mailbox test passed")

        print("\nAll actor model tests passed! 🎉")

    asyncio.run(run_tests())
