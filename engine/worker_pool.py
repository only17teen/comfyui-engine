from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from engine.subprocess_bridge import SubprocessBridge

__all__ = ["WorkerPool", "PoolExhaustedError"]
log = logging.getLogger(__name__)


class PoolExhaustedError(RuntimeError):
    pass


@dataclass
class _WorkerSlot:
    bridge: SubprocessBridge
    pending: int = field(default=0)
    restarts: int = field(default=0)
    healthy: bool = field(default=True)


class WorkerPool:
    def __init__(
        self,
        cmd: list[str],
        *,
        size: int = 4,
        default_timeout: float = 30.0,
        health_check_interval: float = 10.0,
        max_restarts: int = 3,
    ) -> None:
        if size < 1:
            raise ValueError(f"pool size >= 1 required, got {size}")
        self._cmd = cmd
        self._size = size
        self._default_timeout = default_timeout
        self._health_check_interval = health_check_interval
        self._max_restarts = max_restarts
        self._slots: list[_WorkerSlot] = []
        self._monitor_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        self._slots = []
        for _ in range(self._size):
            b = SubprocessBridge(
                self._cmd,
                default_timeout=self._default_timeout,
                max_restarts=self._max_restarts,
            )
            await b.start()
            self._slots.append(_WorkerSlot(bridge=b))
        self._running = True
        self._monitor_task = asyncio.ensure_future(self._health_monitor())

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        await asyncio.gather(
            *[s.bridge.stop() for s in self._slots], return_exceptions=True
        )
        self._slots.clear()

    def _pick_slot(self) -> _WorkerSlot:
        alive = [s for s in self._slots if s.healthy and s.bridge.is_running]
        if not alive:
            raise PoolExhaustedError(f"all {self._size} workers unhealthy")
        return min(alive, key=lambda s: s.pending)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        slot = self._pick_slot()
        slot.pending += 1
        try:
            return await slot.bridge.call(method, params, timeout=timeout)
        finally:
            slot.pending -= 1

    async def _health_monitor(self) -> None:
        while self._running:
            await asyncio.sleep(self._health_check_interval)
            for i, slot in enumerate(self._slots):
                if slot.bridge.is_running:
                    continue
                slot.healthy = False
                if slot.restarts < self._max_restarts:
                    try:
                        await slot.bridge.start()
                        slot.restarts += 1
                        slot.healthy = True
                    except Exception as e:
                        log.error(
                            "restart failed", extra={"worker": i, "error": str(e)}
                        )

    async def __aenter__(self) -> WorkerPool:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    @property
    def size(self) -> int:
        return self._size

    @property
    def alive_count(self) -> int:
        return sum(1 for s in self._slots if s.healthy and s.bridge.is_running)

    @property
    def total_pending(self) -> int:
        return sum(s.pending for s in self._slots)
