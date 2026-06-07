"""ComfyUI Async Generation Engine v4.0 - Dead Letter Queue
Handles failed jobs that exceed retry limits for later analysis and replay.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from enum import Enum, auto

logger = logging.getLogger(__name__)


class DLQReason(Enum):
    """Reason for dead letter queue entry."""

    MAX_RETRIES_EXCEEDED = "max_retries"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker"
    TIMEOUT = "timeout"
    VALIDATION_ERROR = "validation"
    UNKNOWN_ERROR = "unknown"


@dataclass
class DLQEntry:
    """Single dead letter queue entry."""

    job_id: str
    payload: dict[str, Any]
    meta: dict[str, Any]
    reason: DLQReason
    error_message: str
    retry_count: int
    created_at: float = field(default_factory=time.time)
    last_retry_at: float | None = None
    replay_count: int = 0

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "reason": self.reason.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DLQEntry":
        data = data.copy()
        data["reason"] = DLQReason(data["reason"])
        return cls(**data)


class DeadLetterQueue:
    """Manages failed jobs for later analysis and replay.

    Features:
    - Persistent storage of failed jobs
    - Categorized by failure reason
    - Replay with modified parameters
    - Metrics and alerting
    - Automatic cleanup of old entries
    """

    def __init__(
        self,
        storage_dir: str = "dead_letter_queue",
        max_entries: int = 10000,
        retention_days: int = 30,
    ):
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self.retention_days = retention_days
        self._entries: list[DLQEntry] = []
        self._lock = asyncio.Lock()
        self._replay_handlers: dict[DLQReason, Callable] = {}

    async def add(
        self,
        job_id: str,
        payload: dict[str, Any],
        meta: dict[str, Any],
        reason: DLQReason,
        error_message: str,
        retry_count: int = 0,
    ) -> None:
        """Add a failed job to the dead letter queue."""
        entry = DLQEntry(
            job_id=job_id,
            payload=payload,
            meta=meta,
            reason=reason,
            error_message=error_message,
            retry_count=retry_count,
        )

        async with self._lock:
            self._entries.append(entry)

            # Persist to disk
            await self._persist_entry(entry)

            # Enforce max entries limit
            if len(self._entries) > self.max_entries:
                removed = self._entries.pop(0)
                await self._remove_persisted(removed)

        logger.warning(f"Job {job_id} added to DLQ: {reason.value} - {error_message}")

    async def _persist_entry(self, entry: DLQEntry) -> None:
        """Save entry to disk."""
        path = self.storage_dir / f"{entry.job_id}.json"
        path.write_text(
            json.dumps(entry.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

    async def _remove_persisted(self, entry: DLQEntry) -> None:
        """Remove persisted entry from disk."""
        path = self.storage_dir / f"{entry.job_id}.json"
        if path.exists():
            path.unlink()

    async def list_entries(
        self,
        reason: DLQReason | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DLQEntry]:
        """List DLQ entries with optional filtering."""
        async with self._lock:
            entries = self._entries
            if reason:
                entries = [e for e in entries if e.reason == reason]
            return entries[offset : offset + limit]

    async def get_entry(self, job_id: str) -> DLQEntry | None:
        """Get specific DLQ entry by job ID."""
        async with self._lock:
            for entry in self._entries:
                if entry.job_id == job_id:
                    return entry
        return None

    async def replay(
        self,
        job_id: str,
        handler: Callable | None = None,
        modified_payload: dict | None = None,
    ) -> bool:
        """Replay a failed job from DLQ.

        Args:
            job_id: Job to replay.
            handler: Optional custom handler. Uses registered handler if None.
            modified_payload: Optional modified payload for replay.

        Returns:
            True if replay initiated successfully.
        """
        entry = await self.get_entry(job_id)
        if not entry:
            logger.error(f"Cannot replay: job {job_id} not found in DLQ")
            return False

        # Use custom or registered handler
        replay_handler = handler or self._replay_handlers.get(entry.reason)
        if not replay_handler:
            logger.error(f"No replay handler for reason: {entry.reason.value}")
            return False

        payload = modified_payload or entry.payload
        entry.replay_count += 1
        entry.last_retry_at = time.time()

        try:
            await replay_handler(payload, entry.meta)
            logger.info(f"Job {job_id} replay initiated successfully")
            return True
        except Exception as e:
            logger.error(f"Job {job_id} replay failed: {e}")
            return False

    def register_replay_handler(
        self,
        reason: DLQReason,
        handler: Callable,
    ) -> None:
        """Register a handler for replaying jobs of a specific reason."""
        self._replay_handlers[reason] = handler

    async def cleanup(self) -> int:
        """Remove old entries beyond retention period."""
        cutoff = time.time() - (self.retention_days * 86400)
        removed = 0

        async with self._lock:
            to_remove = [e for e in self._entries if e.created_at < cutoff]
            for entry in to_remove:
                self._entries.remove(entry)
                await self._remove_persisted(entry)
                removed += 1

        if removed > 0:
            logger.info(f"DLQ cleanup: removed {removed} old entries")

        return removed

    async def get_stats(self) -> dict[str, Any]:
        """Get DLQ statistics."""
        async with self._lock:
            total = len(self._entries)
            by_reason = {}
            for entry in self._entries:
                reason = entry.reason.value
                by_reason[reason] = by_reason.get(reason, 0) + 1

            replayable = sum(1 for e in self._entries if e.reason in self._replay_handlers)

            return {
                "total_entries": total,
                "by_reason": by_reason,
                "replayable": replayable,
                "storage_dir": str(self.storage_dir),
                "max_entries": self.max_entries,
                "retention_days": self.retention_days,
            }

    async def load_from_disk(self) -> None:
        """Load persisted entries from disk."""
        async with self._lock:
            self._entries.clear()
            for path in sorted(self.storage_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    entry = DLQEntry.from_dict(data)
                    self._entries.append(entry)
                except Exception as e:
                    logger.warning(f"Failed to load DLQ entry {path}: {e}")

            # Sort by creation time
            self._entries.sort(key=lambda e: e.created_at)

        logger.info(f"Loaded {len(self._entries)} entries from DLQ")
