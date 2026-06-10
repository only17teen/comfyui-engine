"""ComfyUI Async Generation Engine v4.0 - Performance Profiler
cProfile and memory profiling integration for performance analysis.
"""

import asyncio
import cProfile
import functools
import io
import json
import logging
import pstats
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ProfileResult:
    """Result of a profiling session."""

    function_name: str
    total_time: float
    call_count: int
    per_call_time: float
    cumulative_time: float
    memory_peak_mb: float = 0.0
    memory_current_mb: float = 0.0
    timestamp: float = field(default_factory=time.time)
    top_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_name": self.function_name,
            "total_time": self.total_time,
            "call_count": self.call_count,
            "per_call_time": self.per_call_time,
            "cumulative_time": self.cumulative_time,
            "memory_peak_mb": self.memory_peak_mb,
            "memory_current_mb": self.memory_current_mb,
            "timestamp": self.timestamp,
            "top_calls": self.top_calls,
        }


class PerformanceProfiler:
    """Context manager and decorator for profiling code execution.

    Features:
    - CPU profiling with cProfile
    - Memory profiling with tracemalloc
    - Async function support
    - Results export to JSON/SVG flamegraph
    - Threshold-based alerting
    """

    def __init__(
        self,
        output_dir: str = "profiles",
        slow_threshold_ms: float = 1000.0,
        memory_threshold_mb: float = 512.0,
        top_n_calls: int = 20,
    ):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.slow_threshold_ms = slow_threshold_ms
        self.memory_threshold_mb = memory_threshold_mb
        self.top_n_calls = top_n_calls
        self._results: list[ProfileResult] = []

    @contextmanager
    def profile(self, name: str):
        """Context manager for profiling a code block."""
        profiler = cProfile.Profile()
        tracemalloc.start()

        start_time = time.time()
        profiler.enable()

        try:
            yield self
        finally:
            profiler.disable()
            elapsed = time.time() - start_time

            # Memory stats
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            # Process stats
            stats = pstats.Stats(profiler, stream=io.StringIO())
            stats.sort_stats(pstats.SortKey.CUMULATIVE)
            stats.print_stats(self.top_n_calls)

            # Extract top calls
            top_calls = []
            for func, (_cc, nc, tt, ct, _callers) in stats.stats.items():
                if ct > 0.001:  # Only significant calls
                    top_calls.append(
                        {
                            "function": f"{func[0]}:{func[1]}({func[2]})",
                            "call_count": nc,
                            "total_time": tt,
                            "cumulative_time": ct,
                        }
                    )

            top_calls.sort(key=lambda x: x["cumulative_time"], reverse=True)
            top_calls = top_calls[: self.top_n_calls]

            result = ProfileResult(
                function_name=name,
                total_time=elapsed,
                call_count=sum(1 for _ in stats.stats.items()),
                per_call_time=elapsed / max(sum(nc for _, (cc, nc, tt, ct, callers) in stats.stats.items()), 1),
                cumulative_time=sum(ct for _, (cc, nc, tt, ct, callers) in stats.stats.items()),
                memory_peak_mb=peak / (1024 * 1024),
                memory_current_mb=current / (1024 * 1024),
                top_calls=top_calls,
            )

            self._results.append(result)

            # Alert on slow execution
            if elapsed > self.slow_threshold_ms / 1000:
                logger.warning(
                    f"SLOW: {name} took {elapsed*1000:.1f}ms " f"(threshold: {self.slow_threshold_ms:.1f}ms)"
                )

            # Alert on high memory
            if result.memory_peak_mb > self.memory_threshold_mb:
                logger.warning(
                    f"HIGH MEMORY: {name} peak {result.memory_peak_mb:.1f}MB "
                    f"(threshold: {self.memory_threshold_mb:.1f}MB)"
                )

            # Save profile
            self._save_profile(name, profiler, result)

    def _save_profile(
        self,
        name: str,
        profiler: cProfile.Profile,
        result: ProfileResult,
    ) -> None:
        """Save profile data to disk."""
        timestamp = int(time.time())
        base_name = f"{name}_{timestamp}"

        # Save raw profile
        profile_path = self.output_dir / f"{base_name}.prof"
        profiler.dump_stats(str(profile_path))

        # Save JSON summary
        json_path = self.output_dir / f"{base_name}.json"
        json_path.write_text(
            json.dumps(result.to_dict(), indent=2),
            encoding="utf-8",
        )

        logger.info(f"Profile saved: {profile_path}")

    def decorator(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator for profiling function calls."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with self.profile(func.__name__):
                return func(*args, **kwargs)

        return wrapper

    def async_decorator(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator for profiling async function calls."""

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            with self.profile(func.__name__):
                return await func(*args, **kwargs)

        return wrapper

    def get_results(self) -> list[ProfileResult]:
        """Get all profiling results."""
        return self._results.copy()

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics of all profiling sessions."""
        if not self._results:
            return {"total_sessions": 0}

        total_time = sum(r.total_time for r in self._results)
        total_memory = sum(r.memory_peak_mb for r in self._results)

        slow_count = sum(1 for r in self._results if r.total_time > self.slow_threshold_ms / 1000)
        high_memory_count = sum(1 for r in self._results if r.memory_peak_mb > self.memory_threshold_mb)

        return {
            "total_sessions": len(self._results),
            "total_time": total_time,
            "avg_time": total_time / len(self._results),
            "total_memory_peak_mb": total_memory,
            "avg_memory_peak_mb": total_memory / len(self._results),
            "slow_count": slow_count,
            "high_memory_count": high_memory_count,
            "slow_threshold_ms": self.slow_threshold_ms,
            "memory_threshold_mb": self.memory_threshold_mb,
        }

    def export_flamegraph(self, output_path: str) -> None:
        """Export profiles as flamegraph-compatible format."""
        # Placeholder for flamegraph generation
        # Would require additional tooling like py-spy or austin
        logger.info("Flamegraph export requires py-spy or austin profiler")


# Global profiler instance for easy access
_global_profiler: PerformanceProfiler | None = None


def get_profiler() -> PerformanceProfiler:
    """Get or create global profiler instance."""
    global _global_profiler
    if _global_profiler is None:
        _global_profiler = PerformanceProfiler()
    return _global_profiler


def profile_block(name: str):
    """Context manager for profiling a code block using global profiler."""
    return get_profiler().profile(name)


def profile_func(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator for profiling function calls using global profiler."""
    return get_profiler().decorator(func)


def profile_async(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for profiling async function calls using global profiler."""
    return get_profiler().async_decorator(func)
