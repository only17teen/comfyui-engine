"""ComfyUI Async Generation Engine v5.1 - Core Infrastructure.

This module is now a thin facade that re-exports from the individual
focused modules extracted in v5.1.  Import from the specific module
for new code; import from here for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# ── Re-exports for backward compatibility ─────────────────────────────────────
from engine.metrics import MetricsCollector, MetricsSnapshot  # noqa: F401
from engine.circuit_breaker import (  # noqa: F401
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
)
from engine.retry import RetryConfig, with_retry  # noqa: F401
from engine.queue import JobQueue, PrioritizedJob, QueueFullError  # noqa: F401

# ── Structured Logging ─────────────────────────────────────────────────────────


class JSONFormatter(logging.Formatter):
    """Emit log records as JSON lines for Loki / Vector / jq."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if hasattr(record, "extra"):
            obj.update(record.extra)
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False, default=str)


def setup_logging(
    level: int = logging.INFO,
    log_dir: str = "logs",
    json_format: bool = True,
) -> None:
    """Configure dual logging: human-readable to terminal, JSON to file."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_dir) / f"engine_{ts}.log"

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    if json_format:
        file_handler.setFormatter(JSONFormatter())
    else:
        file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [console_handler, file_handler]
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


# ── Session state (kept here for backward compat) ─────────────────────────────

import json as _json
from dataclasses import asdict, dataclass, field


@dataclass
class SessionState:
    """Persistent session state for resumable operations."""

    session_id: str
    started_at: float
    completed_jobs: list[str] = field(default_factory=list)
    failed_jobs: list[str] = field(default_factory=list)
    pending_jobs: list[str] = field(default_factory=list)
    total_images: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.write_text(_json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> SessionState:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)
