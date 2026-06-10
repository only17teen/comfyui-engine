from __future__ import annotations
import asyncio
import logging
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

__all__ = ["MsgpackBridge", "MsgpackBridgeError", "MsgpackTimeoutError"]
log = logging.getLogger(__name__)
_FRAME = struct.Struct(">I")


class MsgpackBridgeError(RuntimeError):
    pass


class MsgpackTimeoutError(TimeoutError):
    pass


@dataclass
class _Pending:
    future: asyncio.Future[Any]
    timeout_handle: asyncio.TimerHandle | None = field(default=None)


class MsgpackBridge:
    def __init__(
        self,
        cmd: list[str],
        *,
        default_timeout: float = 30.0,
        startup_timeout: float = 5.0,
    ) -> None:
        self._cmd = cmd
        self._default_timeout = default_timeout
        self._startup_timeout = startup_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[str, _Pending] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._running = False
        self._msgpack: Any = None

    async def start(self) -> None:
        try:
            import msgpack

            self._msgpack = msgpack
        except ImportError:
            raise RuntimeError("pip install msgpack") from None
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
                p.future.set_exception(MsgpackBridgeError("bridge shut down"))
        self._pending.clear()

    async def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        if not self._running:
            raise RuntimeError("MsgpackBridge not running")
        cid = str(uuid4())
        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        eff = timeout if timeout is not None else self._default_timeout
        handle = loop.call_later(eff, self._on_timeout, cid)
        self._pending[cid] = _Pending(future=future, timeout_handle=handle)
        payload = self._msgpack.packb(
            {"id": cid, "method": method, "params": dict(params or {})},
            use_bin_type=True,
        )
        assert self._process and self._process.stdin
        self._process.stdin.write(_FRAME.pack(len(payload)) + payload)
        await self._process.stdin.drain()
        return await future

    def _on_timeout(self, cid: str) -> None:
        p = self._pending.pop(cid, None)
        if p and not p.future.done():
            p.future.set_exception(MsgpackTimeoutError(f"call {cid!r} timed out"))

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        reader = self._process.stdout
        try:
            while True:
                hdr = await reader.readexactly(_FRAME.size)
                (n,) = _FRAME.unpack(hdr)
                payload = await reader.readexactly(n)
                try:
                    msg: dict[str, Any] = self._msgpack.unpackb(payload, raw=False)
                except Exception:
                    continue
                self._dispatch(msg)
        except asyncio.IncompleteReadError:
            pass
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
            p.future.set_exception(MsgpackBridgeError(str(err)))
        else:
            p.future.set_result(msg.get("result"))

    async def __aenter__(self) -> MsgpackBridge:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def is_running(self) -> bool:
        return (
            self._running
            and self._process is not None
            and self._process.returncode is None
        )
