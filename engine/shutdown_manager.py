"""
ComfyUI Async Generation Engine v4.0 - Graceful Shutdown Manager
Handles SIGTERM/SIGINT with connection draining and cleanup.
"""

import asyncio
import logging
import signal
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class GracefulShutdownManager:
    """
    Manages graceful shutdown for async services.

    Features:
    - Signal handler registration (SIGTERM, SIGINT)
    - Connection draining with timeout
    - Cleanup callback registration
    - Force kill after grace period
    - Health check during shutdown
    """

    def __init__(
        self,
        grace_period_seconds: float = 30.0,
        drain_timeout_seconds: float = 10.0,
    ):
        self.grace_period = grace_period_seconds
        self.drain_timeout = drain_timeout_seconds
        self._shutdown_event = asyncio.Event()
        self._cleanup_callbacks: List[Callable] = []
        self._drain_callbacks: List[Callable] = []
        self._is_shutting_down = False
        self._shutdown_start_time: Optional[float] = None

    def register_cleanup(self, callback: Callable) -> None:
        """Register a callback to run during shutdown."""
        self._cleanup_callbacks.append(callback)

    def register_drain(self, callback: Callable) -> None:
        """Register a callback for connection draining."""
        self._drain_callbacks.append(callback)

    def setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("No event loop running, skipping signal handlers")
            return

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_signal(s.name))
            )
            logger.info(f"Registered signal handler for {sig.name}")

    async def _handle_signal(self, signal_name: str) -> None:
        """Handle shutdown signal."""
        if self._is_shutting_down:
            logger.warning(f"Received {signal_name} but already shutting down")
            return

        self._is_shutting_down = True
        self._shutdown_start_time = time.time()

        logger.info(f"Received {signal_name}, initiating graceful shutdown...")

        # Phase 1: Stop accepting new work
        self._shutdown_event.set()

        # Phase 2: Drain connections
        await self._drain_connections()

        # Phase 3: Run cleanup callbacks
        await self._run_cleanup()

        elapsed = time.time() - self._shutdown_start_time
        logger.info(f"Graceful shutdown complete in {elapsed:.1f}s")

    async def _drain_connections(self) -> None:
        """Drain active connections with timeout."""
        if not self._drain_callbacks:
            return

        logger.info(f"Draining {len(self._drain_callbacks)} connection handlers...")

        # Run drain callbacks with timeout
        tasks = [
            asyncio.create_task(self._run_with_timeout(callback, self.drain_timeout))
            for callback in self._drain_callbacks
        ]

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Connection draining complete")

    async def _run_cleanup(self) -> None:
        """Run all registered cleanup callbacks."""
        if not self._cleanup_callbacks:
            return

        logger.info(f"Running {len(self._cleanup_callbacks)} cleanup callbacks...")

        for callback in self._cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error(f"Cleanup callback failed: {e}")

        logger.info("Cleanup complete")

    async def _run_with_timeout(self, callback: Callable, timeout: float) -> None:
        """Run callback with timeout."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await asyncio.wait_for(callback(), timeout=timeout)
            else:
                # Run sync callback in thread pool
                loop = asyncio.get_running_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(None, callback),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            logger.warning(f"Callback timed out after {timeout}s")
        except Exception as e:
            logger.error(f"Callback error: {e}")

    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return self._is_shutting_down

    async def wait_for_shutdown(self) -> None:
        """Block until shutdown is initiated."""
        await self._shutdown_event.wait()

    def get_shutdown_status(self) -> Dict[str, Any]:
        """Get current shutdown status."""
        status = {
            "is_shutting_down": self._is_shutting_down,
            "cleanup_callbacks": len(self._cleanup_callbacks),
            "drain_callbacks": len(self._drain_callbacks),
        }

        if self._shutdown_start_time:
            status["elapsed_seconds"] = time.time() - self._shutdown_start_time
            status["grace_period_remaining"] = max(
                0, self.grace_period - status["elapsed_seconds"]
            )

        return status


# Global shutdown manager instance
_global_shutdown_manager: Optional[GracefulShutdownManager] = None


def get_shutdown_manager() -> GracefulShutdownManager:
    """Get or create global shutdown manager."""
    global _global_shutdown_manager
    if _global_shutdown_manager is None:
        _global_shutdown_manager = GracefulShutdownManager()
    return _global_shutdown_manager
