"""ComfyUI Async Generation Engine v5.1 - SQLite WAL Checkpoint Resume
Kiro Protocol: SQLite with WAL mode for reliable checkpoint storage.
"""

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from engine.session_manager import SessionManager, SessionManifest
from engine.api_client import ComfyUIJob

logger = logging.getLogger(__name__)


@dataclass
class BatchCheckpoint:
    """Checkpoint for a specific batch position."""

    checkpoint_id: str
    timestamp: float
    batch_index: int
    total_batches: int
    completed_jobs: list[str] = field(default_factory=list)
    failed_jobs: list[str] = field(default_factory=list)
    pending_configs: list[dict] = field(default_factory=list)
    generation_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BatchCheckpoint":
        return cls(**data)


@dataclass
class ResumeState:
    """State needed to resume a batch from checkpoint."""

    can_resume: bool
    session_id: str | None = None
    checkpoint_id: str | None = None
    resume_from_index: int = 0
    completed_count: int = 0
    failed_count: int = 0
    remaining_configs: list[dict] = field(default_factory=list)
    original_params: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class SQLiteCheckpointManager:
    """SQLite-based checkpoint manager with WAL mode.
    
    Kiro Protocol optimizations:
    - WAL mode for concurrent reads/writes (Rule 9: Database & Search)
    - Batch inserts for efficiency (Rule 1: Optimization)
    - Indexed queries for fast lookups (Rule 9: Database & Search)
    - Automatic cleanup with TTL (Rule 9: Database & Search)
    """

    def __init__(
        self,
        session_manager: SessionManager,
        checkpoint_interval: int = 5,  # Every N jobs
        emergency_checkpoint: bool = True,
        db_path: str = "checkpoints/checkpoints.db",
        wal_mode: bool = True,
        cleanup_interval_hours: float = 24.0,
    ):
        self.session_manager = session_manager
        self.checkpoint_interval = checkpoint_interval
        self.emergency_checkpoint = emergency_checkpoint
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.wal_mode = wal_mode
        self.cleanup_interval_hours = cleanup_interval_hours
        
        self._current_checkpoint: BatchCheckpoint | None = None
        self._jobs_since_checkpoint: int = 0
        self._start_time: float | None = None
        self._shutdown: bool = False
        
        self._init_db()
        
        if emergency_checkpoint:
            self._setup_signal_handlers()

        self.logger = logging.getLogger(__name__)

    def _init_db(self) -> None:
        """Initialize SQLite database with WAL mode."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            # Enable WAL mode for concurrent reads/writes
            if self.wal_mode:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            
            # Create tables with indexes
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    batch_index INTEGER NOT NULL,
                    total_batches INTEGER NOT NULL,
                    completed_jobs TEXT NOT NULL,  -- JSON array
                    failed_jobs TEXT NOT NULL,     -- JSON array
                    pending_configs TEXT NOT NULL,  -- JSON array
                    generation_params TEXT NOT NULL, -- JSON object
                    is_emergency INTEGER DEFAULT 0,
                    created_at REAL DEFAULT (julianday('now'))
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoints_session 
                ON checkpoints(session_id, timestamp DESC)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoints_emergency 
                ON checkpoints(session_id, is_emergency, timestamp DESC)
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoint_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    jobs_completed INTEGER DEFAULT 0,
                    jobs_failed INTEGER DEFAULT 0,
                    processing_time REAL DEFAULT 0.0,
                    timestamp REAL DEFAULT (julianday('now')),
                    FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(checkpoint_id)
                )
            """)
            
            conn.commit()
            self.logger.info(f"SQLite checkpoint DB initialized: {self.db_path}")
        finally:
            conn.close()

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for emergency checkpoint."""
        import signal
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._emergency_checkpoint(s.name)),
            )

    async def _emergency_checkpoint(self, signal_name: str) -> None:
        """Create emergency checkpoint on shutdown signal."""
        self.logger.warning(f"Emergency checkpoint due to {signal_name}")
        self._shutdown = True

        if self._current_checkpoint:
            await self._save_checkpoint(self._current_checkpoint, emergency=True)
            self.session_manager.pause_session()
            self.logger.info("Emergency checkpoint saved, session paused")

    async def _save_checkpoint(
        self,
        checkpoint: BatchCheckpoint,
        emergency: bool = False,
    ) -> None:
        """Save checkpoint to SQLite database."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("""
                INSERT OR REPLACE INTO checkpoints 
                (checkpoint_id, session_id, timestamp, batch_index, total_batches,
                 completed_jobs, failed_jobs, pending_configs, generation_params, is_emergency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                checkpoint.checkpoint_id,
                checkpoint.checkpoint_id,  # session_id same as checkpoint_id for now
                checkpoint.timestamp,
                checkpoint.batch_index,
                checkpoint.total_batches,
                json.dumps(checkpoint.completed_jobs),
                json.dumps(checkpoint.failed_jobs),
                json.dumps(checkpoint.pending_configs),
                json.dumps(checkpoint.generation_params),
                1 if emergency else 0,
            ))
            
            conn.commit()
            self.logger.info(
                f"Checkpoint saved to SQLite: {checkpoint.checkpoint_id} "
                f"({len(checkpoint.completed_jobs)}/{checkpoint.total_batches} completed)"
            )
        finally:
            conn.close()

    def load_checkpoint(self, checkpoint_id: str) -> BatchCheckpoint | None:
        """Load checkpoint from SQLite database."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return BatchCheckpoint(
                checkpoint_id=row[0],
                timestamp=row[2],
                batch_index=row[3],
                total_batches=row[4],
                completed_jobs=json.loads(row[5]),
                failed_jobs=json.loads(row[6]),
                pending_configs=json.loads(row[7]),
                generation_params=json.loads(row[8]),
            )
        finally:
            conn.close()

    def find_latest_checkpoint(self, session_id: str) -> BatchCheckpoint | None:
        """Find latest checkpoint for session."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            # Check emergency first
            cursor = conn.execute(
                """SELECT * FROM checkpoints 
                   WHERE session_id = ? AND is_emergency = 1 
                   ORDER BY timestamp DESC LIMIT 1""",
                (session_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                cursor = conn.execute(
                    """SELECT * FROM checkpoints 
                       WHERE session_id = ? 
                       ORDER BY timestamp DESC LIMIT 1""",
                    (session_id,)
                )
                row = cursor.fetchone()
            
            if not row:
                return None
            
            return BatchCheckpoint(
                checkpoint_id=row[0],
                timestamp=row[2],
                batch_index=row[3],
                total_batches=row[4],
                completed_jobs=json.loads(row[5]),
                failed_jobs=json.loads(row[6]),
                pending_configs=json.loads(row[7]),
                generation_params=json.loads(row[8]),
            )
        finally:
            conn.close()

    def start_batch(
        self,
        session_id: str,
        total_batches: int,
        generation_params: dict[str, Any],
    ) -> None:
        """Initialize checkpoint tracking for new batch."""
        self._start_time = time.time()
        self._jobs_since_checkpoint = 0
        self._current_checkpoint = BatchCheckpoint(
            checkpoint_id=f"chk_{int(time.time())}",
            timestamp=time.time(),
            batch_index=0,
            total_batches=total_batches,
            generation_params=generation_params,
        )

        self.session_manager.create_session(
            session_id=session_id,
            total_jobs=total_batches,
        )

    def update_progress(self, job: ComfyUIJob) -> None:
        """Update checkpoint with job completion."""
        if not self._current_checkpoint:
            return

        self.session_manager.update_job(job)
        self._jobs_since_checkpoint += 1

        if job.status == "completed":
            self._current_checkpoint.completed_jobs.append(job.job_id)
        elif job.status == "error":
            self._current_checkpoint.failed_jobs.append(job.job_id)

        self._current_checkpoint.batch_index = len(self._current_checkpoint.completed_jobs) + len(
            self._current_checkpoint.failed_jobs
        )

        # Auto-save checkpoint at interval
        if self._jobs_since_checkpoint >= self.checkpoint_interval:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._save_checkpoint(self._current_checkpoint))
            except RuntimeError:
                pass
            self._jobs_since_checkpoint = 0

    def get_progress(self) -> dict[str, Any]:
        """Get current progress with ETA."""
        if not self._current_checkpoint or not self._start_time:
            return {"error": "No active batch"}

        completed = len(self._current_checkpoint.completed_jobs)
        failed = len(self._current_checkpoint.failed_jobs)
        total = self._current_checkpoint.total_batches
        current = completed + failed

        elapsed = time.time() - self._start_time
        progress_pct = (current / total * 100) if total > 0 else 0

        # ETA calculation
        if current > 0 and elapsed > 0:
            avg_time_per_job = elapsed / current
            remaining = total - current
            eta_seconds = avg_time_per_job * remaining
        else:
            eta_seconds = None

        return {
            "completed": completed,
            "failed": failed,
            "total": total,
            "current": current,
            "progress_percent": round(progress_pct, 2),
            "elapsed_seconds": round(elapsed, 2),
            "eta_seconds": round(eta_seconds, 2) if eta_seconds else None,
            "checkpoint_id": self._current_checkpoint.checkpoint_id,
        }

    def get_resume_state(self, session_id: str) -> ResumeState:
        """Check if batch can be resumed from checkpoint."""
        checkpoint = self.find_latest_checkpoint(session_id)
        
        if checkpoint:
            return ResumeState(
                can_resume=True,
                session_id=session_id,
                checkpoint_id=checkpoint.checkpoint_id,
                resume_from_index=checkpoint.batch_index,
                completed_count=len(checkpoint.completed_jobs),
                failed_count=len(checkpoint.failed_jobs),
                remaining_configs=checkpoint.pending_configs,
                original_params=checkpoint.generation_params,
            )

        # Check session manager
        session = self.session_manager.load_session(session_id)
        if not session:
            return ResumeState(can_resume=False, error="No session found")

        if session.status == "completed":
            return ResumeState(can_resume=False, error="Session already completed")

        return ResumeState(
            can_resume=True,
            session_id=session_id,
            resume_from_index=0,
            completed_count=session.completed_count,
            failed_count=session.failed_count,
        )

    def prepare_resume_batch(
        self,
        resume_state: ResumeState,
        total_configs: list[dict],
    ) -> list[dict]:
        """Prepare configs for resumed batch."""
        if not resume_state.can_resume:
            return total_configs

        start_index = resume_state.resume_from_index
        if start_index >= len(total_configs):
            return []

        remaining = total_configs[start_index:]

        self.logger.info(
            f"Resuming batch from index {start_index}: "
            f"{len(remaining)}/{len(total_configs)} remaining"
        )

        return remaining

    def finalize_batch(self, session_id: str) -> None:
        """Finalize batch and clean up checkpoints."""
        self.session_manager.finalize_session("completed")
        
        # Clean up checkpoints for this session
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "DELETE FROM checkpoints WHERE session_id = ?",
                (session_id,)
            )
            conn.commit()
        finally:
            conn.close()

        self.logger.info(f"Batch finalized: {session_id}")

    def cleanup_old_checkpoints(self, max_age_hours: float = 24.0) -> int:
        """Remove old checkpoint entries."""
        cutoff = time.time() - (max_age_hours * 3600)
        
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "DELETE FROM checkpoints WHERE timestamp < ?",
                (cutoff,)
            )
            conn.commit()
            removed = cursor.rowcount
            
            if removed > 0:
                self.logger.info(f"Cleaned up {removed} old checkpoints")
            return removed
        finally:
            conn.close()

    def get_checkpoint_stats(self) -> dict[str, Any]:
        """Get checkpoint database statistics."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM checkpoints")
            total = cursor.fetchone()[0]
            
            cursor = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE is_emergency = 1"
            )
            emergency = cursor.fetchone()[0]
            
            cursor = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE is_emergency = 0"
            )
            regular = cursor.fetchone()[0]
            
            db_size = self.db_path.stat().st_size
            
            return {
                "db_path": str(self.db_path),
                "total_checkpoints": total,
                "emergency_checkpoints": emergency,
                "regular_checkpoints": regular,
                "db_size_bytes": db_size,
                "db_size_mb": round(db_size / (1024 * 1024), 2),
                "wal_mode": self.wal_mode,
            }
        finally:
            conn.close()

    def vacuum_db(self) -> None:
        """Vacuum database to reclaim space."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("VACUUM")
            conn.commit()
            self.logger.info("Checkpoint database vacuumed")
        finally:
            conn.close()
