"""Sharded Router for Actor Model
Implements DashMap-style 16-lock sharding for concurrent actor access.
Part of Kiro Protocol v3.0 Phase 1: Actor Model.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from .base import Actor, ActorSystem, ActorMessage

logger = logging.getLogger(__name__)


@dataclass
class RoutingStats:
    """Statistics for the router."""
    messages_routed: int = 0
    messages_failed: int = 0
    average_routing_time: float = 0.0
    shard_utilization: List[int] = None

    def __post_init__(self):
        if self.shard_utilization is None:
            self.shard_utilization = [0] * 16  # Default 16 shards


class ShardedActorRouter:
    """
    Sharded router for actor messaging inspired by DashMap.
    Provides concurrent access to actors with minimal locking.
    """

    def __init__(self, num_shards: int = 16):
        self.num_shards = num_shards
        # Each shard has its own lock and actor dictionary
        self._shards: List[Tuple[asyncio.Lock, Dict[str, Actor]]] = [
            (asyncio.Lock(), {}) for _ in range(num_shards)
        ]
        self._stats = RoutingStats()
        logger.info(f"ShardedActorRouter initialized with {num_shards} shards")

    def _get_shard(self, key: str) -> int:
        """Determine which shard a key belongs to."""
        return hash(key) % self.num_shards

    async def get_actor(self, actor_id: str) -> Optional[Actor]:
        """Get an actor by ID with sharded locking."""
        shard_index = self._get_shard(actor_id)
        lock, shard_dict = self._shards[shard_index]

        async with lock:
            actor = shard_dict.get(actor_id)
            # Update shard utilization stats
            self._stats.shard_utilization[shard_index] = len(shard_dict)
            return actor

    async def register_actor(self, actor: Actor) -> bool:
        """Register an actor with the router."""
        shard_index = self._get_shard(actor.actor_id)
        lock, shard_dict = self._shards[shard_index]

        async with lock:
            if actor.actor_id in shard_dict:
                logger.warning(f"Actor {actor.actor_id} already registered in shard {shard_index}")
                return False

            shard_dict[actor.actor_id] = actor
            self._stats.shard_utilization[shard_index] = len(shard_dict)
            logger.info(f"Registered actor {actor.actor_id} in shard {shard_index}")
            return True

    async def unregister_actor(self, actor_id: str) -> bool:
        """Unregister an actor from the router."""
        shard_index = self._get_shard(actor_id)
        lock, shard_dict = self._shards[shard_index]

        async with lock:
            if actor_id not in shard_dict:
                logger.warning(f"Actor {actor_id} not found in shard {shard_index}")
                return False

            del shard_dict[actor_id]
            self._stats.shard_utilization[shard_index] = len(shard_dict)
            logger.info(f"Unregistered actor {actor_id} from shard {shard_index}")
            return True

    async def route_message(self, recipient: str, message: ActorMessage) -> bool:
        """
        Route a message to the recipient actor.
        Returns True if message was successfully queued, False otherwise.
        """
        start_time = time.time()

        try:
            actor = await self.get_actor(recipient)
            if actor:
                success = await actor.enqueue(message)
                if success:
                    self._stats.messages_routed += 1
                else:
                    self._stats.messages_failed += 1
                    logger.warning(f"Failed to enqueue message for actor {recipient}")

                # Update routing stats
                routing_time = time.time() - start_time
                if self._stats.messages_routed == 1:
                    self._stats.average_routing_time = routing_time
                else:
                    self._stats.average_routing_time = (
                        (self._stats.average_routing_time * (self._stats.messages_routed - 1) + routing_time) /
                        self._stats.messages_routed
                    )

                return success
            else:
                self._stats.messages_failed += 1
                logger.warning(f"Actor {recipient} not found for message routing")
                return False

        except Exception as e:
            self._stats.messages_failed += 1
            logger.error(f"Error routing message to {recipient}: {e}")
            return False

    async def broadcast_message(self, message: ActorMessage,
                               exclude_sender: bool = False,
                               sender_id: Optional[str] = None) -> int:
        """
        Broadcast a message to all actors.
        Returns the number of actors the message was sent to.
        """
        sent_count = 0

        # Route to all shards
        for shard_index in range(self.num_shards):
            lock, shard_dict = self._shards[shard_index]
            async with lock:
                for actor_id, actor in shard_dict.items():
                    # Skip sender if requested
                    if exclude_sender and sender_id and actor_id == sender_id:
                        continue

                    # Create a copy of the message for each recipient
                    message_copy = ActorMessage(
                        sender=message.sender,
                        recipient=actor_id,
                        payload=message.payload,
                        priority=message.priority,
                        timestamp=message.timestamp,
                        ttl=message.ttl
                    )

                    if await actor.enqueue(message_copy):
                        sent_count += 1

        logger.info(f"Broadcast message sent to {sent_count} actors")
        return sent_count

    async def get_actor_count(self) -> int:
        """Get total number of registered actors."""
        total = 0
        for shard_index in range(self.num_shards):
            lock, shard_dict = self._shards[shard_index]
            async with lock:
                total += len(shard_dict)
        return total

    async def get_shard_stats(self) -> List[int]:
        """Get actor count per shard."""
        stats = []
        for shard_index in range(self.num_shards):
            lock, shard_dict = self._shards[shard_index]
            async with lock:
                stats.append(len(shard_dict))
        return stats

    def get_stats(self) -> RoutingStats:
        """Get routing statistics."""
        # Update shard utilization
        for shard_index in range(self.num_shards):
            lock, shard_dict = self._shards[shard_index]
            # Note: We're not locking here for stats as it's approximate
            self._stats.shard_utilization[shard_index] = len(shard_dict)
        return self._stats


# Global router instance
_router: Optional[ShardedActorRouter] = None


def get_actor_router() -> ShardedActorRouter:
    """Get the global actor router instance."""
    global _router
    if _router is None:
        _router = ShardedActorRouter()
    return _router


async def initialize_actor_router():
    """Initialize the global actor router."""
    router = get_actor_router()
    logger.info("Global actor router initialized")


async def shutdown_actor_router():
    """Shutdown the global actor router."""
    global _router
    if _router is not None:
        logger.info("Global actor router shutdown")
        _router = None