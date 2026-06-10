"""Graceful Shutdown Manager.

Addresses Issue #29: Graceful shutdown — drain queue on SIGTERM before exit.
"""
import asyncio
import signal
import logging
from typing import List, Callable, Awaitable

logger = logging.getLogger(__name__)

class GracefulShutdown:
    """Manages graceful shutdown of the engine by draining queues and finishing tasks."""
    
    def __init__(self):
        self.is_shutting_down = False
        self._callbacks: List[Callable[[], Awaitable[None]]] = []
        self._setup_signals()
        
    def _setup_signals(self):
        """Register signal handlers for SIGINT and SIGTERM."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.shutdown(s)))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler fully
                pass

    def register_callback(self, callback: Callable[[], Awaitable[None]]):
        """Register an async callback to be called during shutdown (e.g., closing connections)."""
        self._callbacks.append(callback)
        
    async def shutdown(self, sig: int = None):
        """Trigger the shutdown sequence."""
        if self.is_shutting_down:
            return
            
        self.is_shutting_down = True
        logger.info(f"Received shutdown signal {sig}. Initiating graceful shutdown...")
        
        # Give components time to finish in-flight requests (e.g. draining)
        logger.info("Running shutdown callbacks...")
        results = await asyncio.gather(
            *[cb() for cb in self._callbacks],
            return_exceptions=True
        )
        
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Error during shutdown callback: {res}")
                
        logger.info("Graceful shutdown complete.")
        # Optional: Force exit if needed
        # import sys
        # sys.exit(0)

# Global singleton
shutdown_manager = GracefulShutdown()
