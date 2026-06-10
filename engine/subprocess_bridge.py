from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

__all__ = ["SubprocessBridge", "BridgeError", "BridgeTimeoutError"]
log = logging.getLogger(__name__)


class BridgeError(RuntimeError):
    """Raised when the SubprocessBridge encounters a protocol error."""

    pass


class BridgeTimeoutError(TimeoutError):
    """Raised when a SubprocessBridge request times out."""

    pass


@dataclass
class _PendingCall:
    future: asyncio.Future[Any]
    timeout_handle: asyncio.TimerHandle | None = field(default=None)


class SubprocessBridge:
    """JSON-lines IPC bridge to a subprocess with multiplexed futures."""

    def __init__(
        self,
        cmd: list[str],
        *,
        max_restarts: int = 3,
        default_timeout: float = 30.0,
        startup_timeout: float = 5.0,
    ) -> None:
        self._cmd = cmd
        self._default_timeout = default_timeout
        self._startup_timeout = startup_timeout
        self._max_restarts = max_restarts
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[str, _PendingCall] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True
        self._reader_task = asyncio.ensure_future(self._read_loop())

    async def stop(self) -> None:
        self._running = False
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        for p in list(self._pending.values()):
            if not p.future.done():
                p.future.set_exception(BridgeError("bridge shut down"))
        self._pending.clear()

    async def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        if not self._running or self._process is None:
            raise RuntimeError("SubprocessBridge not running")
        call_id = str(uuid4())
        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        eff = timeout if timeout is not None else self._default_timeout
        handle = loop.call_later(eff, self._on_timeout, call_id)
        self._pending[call_id] = _PendingCall(future=future, timeout_handle=handle)
        req = json.dumps({"id": call_id, "method": method, "params": dict(params or {})}) + "\n"
        assert self._process.stdin is not None
        self._process.stdin.write(req.encode())
        await self._process.stdin.drain()
        return await future

    def _on_timeout(self, call_id: str) -> None:
        p = self._pending.pop(call_id, None)
        if p and not p.future.done():
            p.future.set_exception(BridgeTimeoutError(f"call {call_id!r} timed out"))

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        try:
            async for raw in self._process.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except asyncio.CancelledError:
            pass

    def _dispatch(self, msg: dict[str, Any]) -> None:
        cid = str(msg.get("id", ""))
        p = self._pending.pop(cid, None)
        if not p or p.future.done():
            return
        if p.timeout_handle:
            p.timeout_handle.cancel()
        err = msg.get("error")
        if err:
            p.future.set_exception(BridgeError(str(err)))
        else:
            p.future.set_result(msg.get("result"))

    async def __aenter__(self) -> SubprocessBridge:
        """JSON-lines IPC bridge to a subprocess with multiplexed futures."""
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.returncode is None
