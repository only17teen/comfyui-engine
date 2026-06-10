from __future__ import annotations
import io
import logging
from pathlib import Path
from typing import Any

__all__ = ["AsyncProfiler", "profile_async"]
log = logging.getLogger(__name__)


class AsyncProfiler:
    def __init__(self, clock_type: str = "wall", builtins: bool = False) -> None:
        self._clock_type = clock_type
        self._builtins = builtins
        self._yappi: Any = None
        self._available = False

    async def __aenter__(self) -> AsyncProfiler:
        try:
            import yappi

            self._yappi = yappi
            yappi.set_clock_type(self._clock_type)
            yappi.start(builtins=self._builtins)
            self._available = True
        except ImportError:
            log.warning("yappi not installed — profiling disabled")
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._available and self._yappi:
            self._yappi.stop()

    def print_stats(self, n: int = 20, sort_by: str = "ttot") -> None:
        if not self._available or not self._yappi:
            return
        stats = self._yappi.get_func_stats()
        stats.sort(sort_by)
        buf = io.StringIO()
        stats.print_all(out=buf)
        print("\n".join(buf.getvalue().splitlines()[: n + 3]))

    def save(self, path: str | Path, fmt: str = "pstat") -> None:
        if not self._available or not self._yappi:
            return
        self._yappi.get_func_stats().save(str(path), type=fmt)

    def get_thread_stats(self) -> Any:
        return (
            self._yappi.get_thread_stats() if self._available and self._yappi else None
        )

    @property
    def is_available(self) -> bool:
        return self._available


async def profile_async(
    coro: Any,
    output_path: str | Path | None = None,
    clock_type: str = "wall",
    print_n: int = 20,
) -> Any:
    async with AsyncProfiler(clock_type=clock_type) as prof:
        result = await coro
    if output_path:
        prof.save(output_path)
    else:
        prof.print_stats(n=print_n)
    return result
