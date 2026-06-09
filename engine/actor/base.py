"""Actor Model Base Implementation for ComfyUI Engine
Implements Phase 1 of Kiro Protocol v3.0: Actor Model Concurrency Architecture
"""

import asyncio
import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict
from enum import Enum
import time
import heapq


logger = logging.getLogger(__name__)


class MessagePriority(Enum):
    """Priority levels for actor mailbox."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass
class ActorMessage:
    """Message passed between actors."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: Optional[str] = None
    recipient: str = ""
    payload: Any = None
    priority: MessagePriority = MessagePriority.NORMAL
    timestamp: float = field(default_factory=time.time)
    ttl: float = 30.0  # Time to live in seconds

    def is_expired(self) -> bool:
        """Check if message has expired."""
        return time.time() - self.timestamp > self.ttl

    def __lt__(self, other):
        """For priority queue ordering (lower priority value = higher priority)."""
        if self.priority.value != other.priority.value:
            return self.priority.value < other.priority.value
        return self.timestamp < other.timestamp


@dataclass
class ActorStats:
    """Statistics for an actor."""
    messages_processed: int = 0
    messages_failed: int = 0
    average_processing_time: float = 0.0
    last_processed: float = 0.0
    mailbox_size: int = 0


class Actor(ABC):
    """Base actor class implementing the actor model."""

    def __init__(self, actor_id: str, mailbox_size: int = 1000):
        self.actor_id = actor_id
        self.mailbox_size = mailbox_size
        self._mailbox: List[ActorMessage] = []  # Priority queue
        self._mailbox_lock = asyncio.Lock()
        self._processing = False
        self._stopped = False
        self._stats = ActorStats()
        self._message_handlers: Dict[str, Callable] = {}
        self._background_task: Optional[asyncio.Task] = None

        # Register default handlers
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register default message handlers."""
        self._message_handlers.update({
            "ping": self._handle_ping,
            "get_stats": self._handle_get_stats,
            "stop": self._handle_stop,
        })

    async def _handle_ping(self, message: ActorMessage) -> Any:
        """Handle ping message."""
        return {"actor_id": self.actor_id, "timestamp": time.time(), "status": "alive"}

    async def _handle_get_stats(self, message: ActorMessage) -> Any:
        """Handle get_stats message."""
        return self._stats

    async def _handle_stop(self, message: ActorMessage) -> Any:
        """Handle stop message."""
        await self.stop()
        return {"status": "stopping"}

    def register_handler(self, message_type: str, handler: Callable[[ActorMessage], Any]):
        """Register a handler for a specific message type."""
        self._message_handlers[message_type] = handler

    async def send(self, recipient: str, payload: Any,
                   priority: MessagePriority = MessagePriority.NORMAL,
                   ttl: float = 30.0) -> str:
        """Send a message to another actor.

        Returns:
            Message ID
        """
        # This would typically go through an actor registry/system
        # For now, we'll just return the message ID
        message = ActorMessage(
            sender=self.actor_id,
            recipient=recipient,
            payload=payload,
            priority=priority,
            ttl=ttl
        )
        # In a full implementation, this would go through the actor system
        logger.debug(f"Actor {self.actor_id} sending message {message.id} to {recipient}")
        return message.id

    async def enqueue(self, message: ActorMessage):
        """Enqueue a message to this actor's mailbox."""
        async with self._mailbox_lock:
            if len(self._mailbox) >= self.mailbox_size:
                logger.warning(f"Actor {self.actor_id} mailbox full, dropping message")
                return False

            # Remove expired messages before adding new one
            self._mailbox = [msg for msg in self._mailbox if not msg.is_expired()]
            heapq.heappush(self._mailbox, message)
            self._stats.mailbox_size = len(self._mailbox)
            return True

    async def _process_message(self, message: ActorMessage) -> Any:
        """Process a single message."""
        start_time = time.time()

        try:
            # Check if we have a handler for this message type
            # For simplicity, we'll treat payload as dict with 'type' field
            if isinstance(message.payload, dict) and 'type' in message.payload:
                message_type = message.payload['type']
                handler = self._message_handlers.get(message_type)

                if handler:
                    result = await handler(message)
                else:
                    # Fall back to abstract handle_message method
                    result = await self.handle_message(message)
            else:
                # Treat as generic message
                result = await self.handle_message(message)

            # Update stats
            processing_time = time.time() - start_time
            self._stats.messages_processed += 1
            self._stats.last_processed = time.time()

            # Update average processing time
            if self._stats.messages_processed == 1:
                self._stats.average_processing_time = processing_time
            else:
                self._stats.average_processing_time = (
                    (self._stats.average_processing_time * (self._stats.messages_processed - 1) + processing_time) /
                    self._stats.messages_processed
                )

            return result

        except Exception as e:
            logger.error(f"Actor {self.actor_id} failed to process message {message.id}: {e}")
            self._stats.messages_failed += 1
            await self.handle_failure(message, e)
            raise

    @abstractmethod
    async def handle_message(self, message: ActorMessage) -> Any:
        """Handle a message. Must be implemented by subclasses."""
        pass

    async def handle_failure(self, message: ActorMessage, exception: Exception):
        """Handle message processing failure."""
        logger.warning(f"Actor {self.actor_id} handling failure for message {message.id}: {exception}")
        # Default implementation - can be overridden

    async def _mailbox_processor(self):
        """Background task to process messages from mailbox."""
        logger.info(f"Actor {self.actor_id} mailbox processor started")

        while not self._stopped:
            message = None

            # Get next message from mailbox
            async with self._mailbox_lock:
                # Skip expired messages
                while self._mailbox and self._mailbox[0].is_expired():
                    expired_msg = heapq.heappop(self._mailbox)
                    logger.debug(f"Actor {self.actor_id} dropping expired message {expired_msg.id}")

                if self._mailbox:
                    message = heapq.heappop(self._mailbox)
                    self._stats.mailbox_size = len(self._mailbox)

            # Process message if we got one
            if message:
                try:
                    await self._process_message(message)
                except Exception as e:
                    logger.error(f"Actor {self.actor_id} error processing message: {e}")
            else:
                # No messages, sleep briefly to avoid busy waiting
                await asyncio.sleep(0.01)

        logger.info(f"Actor {self.actor_id} mailbox processor stopped")

    async def start(self):
        """Start the actor's mailbox processor."""
        if self._processing:
            logger.warning(f"Actor {self.actor_id} already started")
            return

        self._processing = True
        self._stopped = False
        self._background_task = asyncio.create_task(self._mailbox_processor())
        logger.info(f"Actor {self.actor_id} started")

    async def stop(self):
        """Stop the actor's mailbox processor."""
        if not self._processing:
            return

        self._stopped = True
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass

        self._processing = False
        logger.info(f"Actor {self.actor_id} stopped")

    async def get_stats(self) -> ActorStats:
        """Get actor statistics."""
        async with self._mailbox_lock:
            self._stats.mailbox_size = len(self._mailbox)
        return self._stats


class ActorSystem:
    """Actor system managing multiple actors and message routing."""

    def __init__(self):
        self._actors: Dict[str, Actor] = {}
        self._actor_lock = asyncio.Lock()
        self._sharded_router: Dict[int, Dict[str, Actor]] = defaultdict(dict)
        self._num_shards = 16  # DashMap-style sharding
        logger.info("ActorSystem initialized")

    def _get_shard(self, actor_id: str) -> int:
        """Get shard number for actor ID (consistent hashing)."""
        return hash(actor_id) % self._num_shards

    async def register_actor(self, actor: Actor):
        """Register an actor with the system."""
        async with self._actor_lock:
            self._actors[actor.actor_id] = actor
            shard = self._get_shard(actor.actor_id)
            self._sharded_router[shard][actor.actor_id] = actor
            logger.info(f"Registered actor {actor.actor_id} in shard {shard}")

    async def unregister_actor(self, actor_id: str):
        """Unregister an actor from the system."""
        async with self._actor_lock:
            if actor_id in self._actors:
                del self._actors[actor_id]
                shard = self._get_shard(actor_id)
                if actor_id in self._sharded_router[shard]:
                    del self._sharded_router[shard][actor_id]
                logger.info(f"Unregistered actor {actor_id}")

    async def get_actor(self, actor_id: str) -> Optional[Actor]:
        """Get an actor by ID using sharded lookup."""
        shard = self._get_shard(actor_id)
        async with self._actor_lock:
            return self._sharded_router[shard].get(actor_id)

    async def send(self, recipient: str, payload: Any,
                   priority: MessagePriority = MessagePriority.NORMAL,
                   ttl: float = 30.0) -> Optional[str]:
        """Send a message to an actor."""
        actor = await self.get_actor(recipient)
        if actor:
            return await actor.send(recipient, payload, priority, ttl)
        else:
            logger.warning(f"Attempted to send message to unknown actor: {recipient}")
            return None

    async def start_all(self):
        """Start all registered actors."""
        async with self._actor_lock:
            for actor in self._actors.values():
                await actor.start()

    async def stop_all(self):
        """Stop all registered actors."""
        async with self._actor_lock:
            for actor in self._actors.values():
                await actor.stop()

    async def get_system_stats(self) -> Dict[str, Any]:
        """Get statistics for the entire actor system."""
        stats = {
            "total_actors": len(self._actors),
            "actors": {},
            "shard_distribution": {}
        }

        async with self._actor_lock:
            for actor_id, actor in self._actors.items():
                stats["actors"][actor_id] = await actor.get_stats()

            # Shard distribution
            for shard_id in range(self._num_shards):
                shard_count = len(self._sharded_router[shard_id])
                if shard_count > 0:
                    stats["shard_distribution"][shard_id] = shard_count

        return stats


# Global actor system instance
_actor_system: Optional[ActorSystem] = None


def get_actor_system() -> ActorSystem:
    """Get the global actor system instance."""
    global _actor_system
    if _actor_system is None:
        _actor_system = ActorSystem()
    return _actor_system


async def initialize_actor_system():
    """Initialize the global actor system."""
    system = get_actor_system()
    await system.start_all()
    logger.info("Global actor system initialized")


async def shutdown_actor_system():
    """Shutdown the global actor system."""
    system = get_actor_system()
    await system.stop_all()
    logger.info("Global actor system shutdown")