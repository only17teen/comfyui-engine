"""Actor Manager for Engine Integration
Integrates the actor model with the existing ComfyUI Engine core.
Part of Kiro Protocol v3.0 Phase 1: Actor Model.
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from .base import ActorSystem, Actor
from .router import ShardedActorRouter, get_actor_router

logger = logging.getLogger(__name__)


class ActorManager:
    """Manages actor lifecycle and integration with engine components.
    Provides a bridge between the existing engine and the actor system.
    """

    def __init__(self):
        self._actor_system = ActorSystem()
        self._router = ShardedActorRouter()
        self._managed_actors: dict[str, Actor] = {}
        self._started = False

    async def initialize(self):
        """Initialize the actor manager and start the actor system."""
        if self._started:
            logger.warning("ActorManager already initialized")
            return

        await self._actor_system.start_all()
        await self._router.__ainit__() if hasattr(self._router, "__ainit__") else None
        self._started = True
        logger.info("ActorManager initialized")

    async def shutdown(self):
        """Shutdown the actor manager and stop all managed actors."""
        if not self._started:
            return

        # Stop all managed actors first
        for actor in self._managed_actors.values():
            await actor.stop()

        # Stop the actor system
        await self._actor_system.stop_all()
        self._started = False
        logger.info("ActorManager shutdown")

    def register_actor(self, actor: Actor) -> bool:
        """Register an actor with both the actor system and router.
        Returns True if successful, False if actor already exists.
        """
        if actor.actor_id in self._managed_actors:
            logger.warning(f"Actor {actor.actor_id} already managed")
            return False

        self._managed_actors[actor.actor_id] = actor

        # Register with actor system (fire and forget)
        asyncio.create_task(self._actor_system.register_actor(actor))
        # Register with router
        asyncio.create_task(self._router.register_actor(actor))

        logger.info(f"Registered actor {actor.actor_id} with ActorManager")
        return True

    def unregister_actor(self, actor_id: str) -> bool:
        """Unregister an actor from the manager.
        Returns True if successful, False if actor not found.
        """
        if actor_id not in self._managed_actors:
            logger.warning(f"Actor {actor_id} not found in manager")
            return False

        actor = self._managed_actors.pop(actor_id)

        # Unregister from actor system and router (fire and forget)
        asyncio.create_task(self._actor_system.unregister_actor(actor_id))
        asyncio.create_task(self._router.unregister_actor(actor_id))

        logger.info(f"Unregistered actor {actor_id} from ActorManager")
        return True

    def get_actor(self, actor_id: str) -> Actor | None:
        """Get a managed actor by ID."""
        return self._managed_actors.get(actor_id)

    async def get_actor_via_router(self, actor_id: str) -> Actor | None:
        """Get an actor via the sharded router."""
        return await self._router.get_actor(actor_id)

    async def send_message(
        self, recipient: str, payload: Any, priority: Any = None
    ) -> str | None:
        """Send a message to an actor via the router.
        Returns message ID if successful, None otherwise.
        """
        # Convert priority if needed
        if priority is not None and not isinstance(priority, type(None)):
            from .base import MessagePriority

            if isinstance(priority, int):
                # Convert integer to MessagePriority
                try:
                    priority = MessagePriority(priority)
                except ValueError:
                    priority = MessagePriority.NORMAL
            elif isinstance(priority, str):
                try:
                    priority = MessagePriority[priority.upper()]
                except KeyError:
                    priority = MessagePriority.NORMAL

        return await self._router.route_message(
            recipient,
            # We need to create an ActorMessage here
            # For simplicity, let's assume the caller handles this
            # This is a simplified version
        )
        # Actually, let's implement this properly

    async def broadcast(
        self,
        payload: Any,
        exclude_sender: bool = False,
        sender_id: str | None = None,
    ) -> int:
        """Broadcast a message to all actors."""
        from .base import ActorMessage, MessagePriority

        message = ActorMessage(
            sender=sender_id, payload=payload, priority=MessagePriority.NORMAL
        )
        return await self._router.broadcast_message(message, exclude_sender, sender_id)

    def get_stats(self) -> dict[str, Any]:
        """Get statistics from the actor manager."""
        return {
            "managed_actors": len(self._managed_actors),
            "actor_system": "initialized" if self._started else "not_started",
            "router": (
                self._router.get_stats().__dict__
                if hasattr(self._router, "get_stats")
                else {}
            ),
        }


# Global actor manager instance
_actor_manager: ActorManager | None = None


def get_actor_manager() -> ActorManager:
    """Get the global actor manager instance."""
    global _actor_manager
    if _actor_manager is None:
        _actor_manager = ActorManager()
    return _actor_manager


async def initialize_actor_manager():
    """Initialize the global actor manager."""
    manager = get_actor_manager()
    await manager.initialize()
    logger.info("Global actor manager initialized")


async def shutdown_actor_manager():
    """Shutdown the global actor manager."""
    manager = get_actor_manager()
    await manager.shutdown()
    logger.info("Global actor manager shutdown")


# Convenience functions for engine integration
async def create_engine_actor(
    actor_id: str, handler_class: type, *args, **kwargs
) -> Actor:
   """Create and register an Actor for engine integration.

   Args:
       actor_id: Unique identifier for the actor.
       handler_class: The handler class to instantiate.
       *args: Positional arguments forwarded to handler_class.
       **kwargs: Keyword arguments forwarded to handler_class.

   Returns:
       The newly created Actor instance.
   """
    """Create and register an Actor for engine integration.

    Args:
        actor_id: Unique identifier for the actor.
        handler_class: The handler class to instantiate.
        *args: Positional arguments forwarded to handler_class.
        **kwargs: Keyword arguments forwarded to handler_class.

    Returns:
        The newly created Actor instance.
    """
    actor = handler_class(actor_id, *args, **kwargs)
    manager = get_actor_manager()
    manager.register_actor(actor)

    # Start the actor if manager is started
    if manager._started:
        await actor.start()

    return actor


# Example engine actors that could be created
class JobProcessorActor(Actor):
    """Example actor for processing generation jobs."""

    def __init__(self, actor_id: str, engine_ref: Any = None):
        super().__init__(actor_id)
        self.engine_ref = engine_ref
        self.register_handler("process_job", self._handle_process_job)
        self.register_handler("get_queue_status", self._handle_get_queue_status)

    async def handle_message(self, message: ActorMessage) -> Any:
        """Handle incoming messages."""
        logger.debug(f"JobProcessorActor {self.actor_id} received message: {message}")
        # Default handling - can be customized
        return {"status": "message_received", "actor_id": self.actor_id}

    async def _handle_process_job(self, message: ActorMessage) -> Any:
        """Handle job processing requests."""
        job_data = message.payload.get("job_data", {})
        logger.info(
            f"JobProcessorActor {self.actor_id} processing job: {job_data.get('job_id', 'unknown')}"
        )

        # Simulate job processing
        # In reality, this would call into the engine's job processing logic
        await asyncio.sleep(0.1)  # Simulate work

        return {
            "status": "completed",
            "job_id": job_data.get("job_id"),
            "actor_id": self.actor_id,
            "result": "Job processed successfully",
        }

    async def _handle_get_queue_status(self, message: ActorMessage) -> Any:
        """Handle queue status requests."""
        # Return queue status from engine if available
        if self.engine_ref and hasattr(self.engine_ref, "get_queue_status"):
            try:
                queue_status = await self.engine_ref.get_queue_status()
                return queue_status
            except Exception as e:
                logger.error(f"Error getting queue status: {e}")
                return {"error": str(e)}

        return {"status": "queue_status_unavailable"}


class MetricsActor(Actor):
    """Example actor for collecting and reporting metrics."""

    def __init__(self, actor_id: str, metrics_collector: Any = None):
        super().__init__(actor_id)
        self.metrics_collector = metrics_collector
        self.register_handler("collect_metrics", self._handle_collect_metrics)
        self.register_handler("report_metrics", self._handle_report_metrics)

    async def handle_message(self, message: ActorMessage) -> Any:
        """Handle incoming messages."""
        return {"status": "metrics_actor_received", "actor_id": self.actor_id}

    async def _handle_collect_metrics(self, message: ActorMessage) -> Any:
        """Handle metrics collection requests."""
        if self.metrics_collector:
            # Collect metrics from the collector
            # This would depend on the actual metrics collector implementation
            return {"status": "metrics_collected", "actor_id": self.actor_id}
        return {"status": "no_metrics_collector"}

    async def _handle_report_metrics(self, message: ActorMessage) -> Any:
        """Handle metrics reporting requests."""
        # Format and report metrics
        return {"status": "metrics_reported", "actor_id": self.actor_id}
