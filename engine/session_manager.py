"""ComfyUI Async Generation Engine v2.0 - Session Manager
Crash recovery, job resumption, and checkpoint system.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from engine.api_client import ComfyUIJob

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Job status enumeration."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class Checkpoint:
    """Single checkpoint for resumable batch processing."""

    checkpoint_id: str
    timestamp: float
    batch_index: int
    total_batches: int
    completed_jobs: list[str] = field(default_factory=list)
    failed_jobs: list[str] = field(default_factory=list)
    pending_jobs: list[str] = field(default_factory=list)
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        return cls(**data)


@dataclass
class SessionManifest:
    """Complete session state for recovery."""

    session_id: str
    created_at: float
    updated_at: float
    status: str = "running"  # running | paused | completed | failed
    total_jobs: int = 0
    completed_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
    jobs: list[dict] = field(default_factory=list)
    checkpoints: list[dict] = field(default_factory=list)
    config_hash: str = ""
    workflow_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SessionManager:
    """Manages session persistence, crash recovery, and job resumption.

    Features:
    - Automatic checkpointing during batch execution
    - Session manifest recovery from disk
    - Job state reconstruction for incomplete sessions
    - Config/workflow change detection
    """

    def __init__(
        self,
        sessions_dir: str = "sessions",
        checkpoint_interval: int = 5,  # Save checkpoint every N jobs
        auto_resume: bool = True,
    ):
        self.sessions_dir = Path(sessions_dir).resolve()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_interval = checkpoint_interval
        self.auto_resume = auto_resume

        self._current_session: SessionManifest | None = None
        self._current_checkpoint: Checkpoint | None = None
        self._jobs_since_checkpoint: int = 0

        self.logger = logging.getLogger(__name__)

    def _session_path(self, session_id: str) -> Path:
        """Get path to session manifest file."""
        return self.sessions_dir / f"{session_id}.json"

    def _checkpoint_path(self, session_id: str, checkpoint_id: str) -> Path:
        """Get path to checkpoint file."""
        checkpoint_dir = self.sessions_dir / session_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        return checkpoint_dir / f"checkpoint_{checkpoint_id}.json"

    def _compute_hash(self, data: Any) -> str:
        """Compute simple hash for change detection."""
        import hashlib

        content = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def create_session(
        self,
        session_id: str | None = None,
        total_jobs: int = 0,
        config: dict | None = None,
        workflow: dict | None = None,
    ) -> SessionManifest:
        """Create new session with optional config/workflow hashes."""
        session = SessionManifest(
            session_id=session_id or f"session_{int(time.time())}",
            created_at=time.time(),
            updated_at=time.time(),
            status="running",
            total_jobs=total_jobs,
            config_hash=self._compute_hash(config) if config else "",
            workflow_hash=self._compute_hash(workflow) if workflow else "",
        )

        self._current_session = session
        self._save_session(session)
        self.logger.info(f"Created session: {session.session_id}")
        return session

    def _save_session(self, session: SessionManifest) -> None:
        """Persist session manifest to disk."""
        path = self._session_path(session.session_id)
        path.write_text(
            json.dumps(session.to_dict(), indent=2, default=str), encoding="utf-8"
        )

    def load_session(self, session_id: str) -> SessionManifest | None:
        """Load session from disk."""
        path = self._session_path(session_id)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            session = SessionManifest(**data)
            self._current_session = session
            self.logger.info(f"Loaded session: {session_id} ({session.status})")
            return session
        except Exception as e:
            self.logger.error(f"Failed to load session {session_id}: {e}")
            return None

    def find_incomplete_sessions(self) -> list[SessionManifest]:
        """Find all sessions that can be resumed."""
        incomplete = []

        for path in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                session = SessionManifest(**data)
                if session.status in ("running", "paused"):
                    incomplete.append(session)
            except Exception:
                continue

        # Sort by most recent first
        incomplete.sort(key=lambda s: s.updated_at, reverse=True)
        return incomplete

    def add_job(self, job: ComfyUIJob) -> None:
        """Add job to current session."""
        if not self._current_session:
            return

        self._current_session.jobs.append(job.to_dict())
        self._current_session.total_jobs = len(self._current_session.jobs)
        self._update_counts()
        self._current_session.updated_at = time.time()

        # Auto-checkpoint
        self._jobs_since_checkpoint += 1
        if self._jobs_since_checkpoint >= self.checkpoint_interval:
            self.create_checkpoint()
            self._jobs_since_checkpoint = 0

    def update_job(self, job: ComfyUIJob) -> None:
        """Update job status in session."""
        if not self._current_session:
            return

        # Find and update job
        for i, job_data in enumerate(self._current_session.jobs):
            if job_data.get("job_id") == job.job_id:
                self._current_session.jobs[i] = job.to_dict()
                break

        self._update_counts()
        self._current_session.updated_at = time.time()

    def _update_counts(self) -> None:
        """Recalculate job counts."""
        if not self._current_session:
            return

        completed = sum(
            1 for j in self._current_session.jobs if j.get("status") == "completed"
        )
        failed = sum(
            1 for j in self._current_session.jobs if j.get("status") == "error"
        )
        pending = self._current_session.total_jobs - completed - failed

        self._current_session.completed_count = completed
        self._current_session.failed_count = failed
        self._current_session.pending_count = pending

    def create_checkpoint(self) -> Checkpoint:
        """Create checkpoint for current session."""
        if not self._current_session:
            raise RuntimeError("No active session")

        checkpoint = Checkpoint(
            checkpoint_id=f"chk_{int(time.time())}",
            timestamp=time.time(),
            batch_index=self._current_session.completed_count,
            total_batches=self._current_session.total_jobs,
            completed_jobs=[
                j["job_id"]
                for j in self._current_session.jobs
                if j.get("status") == "completed"
            ],
            failed_jobs=[
                j["job_id"]
                for j in self._current_session.jobs
                if j.get("status") == "error"
            ],
            pending_jobs=[
                j["job_id"]
                for j in self._current_session.jobs
                if j.get("status") not in ("completed", "error")
            ],
            config_snapshot=self._current_session.to_dict(),
        )

        # Save checkpoint
        path = self._checkpoint_path(
            self._current_session.session_id,
            checkpoint.checkpoint_id,
        )
        path.write_text(
            json.dumps(checkpoint.to_dict(), indent=2, default=str), encoding="utf-8"
        )

        # Add to session
        self._current_session.checkpoints.append(checkpoint.to_dict())
        self._save_session(self._current_session)

        self._current_checkpoint = checkpoint
        self.logger.info(
            f"Checkpoint {checkpoint.checkpoint_id}: "
            f"{len(checkpoint.completed_jobs)} completed, "
            f"{len(checkpoint.pending_jobs)} pending"
        )

        return checkpoint

    def load_checkpoint(self, session_id: str, checkpoint_id: str) -> Checkpoint | None:
        """Load specific checkpoint."""
        path = self._checkpoint_path(session_id, checkpoint_id)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Checkpoint.from_dict(data)
        except Exception as e:
            self.logger.error(f"Failed to load checkpoint: {e}")
            return None

    def get_latest_checkpoint(self, session_id: str) -> Checkpoint | None:
        """Get most recent checkpoint for session."""
        checkpoint_dir = self.sessions_dir / session_id
        if not checkpoint_dir.exists():
            return None

        checkpoints = sorted(
            checkpoint_dir.glob("checkpoint_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not checkpoints:
            return None

        try:
            data = json.loads(checkpoints[0].read_text(encoding="utf-8"))
            return Checkpoint.from_dict(data)
        except Exception:
            return None

    def get_resume_state(self, session_id: str) -> dict | None:
        """Get state needed to resume a session.

        Returns:
            Dict with pending_jobs, completed_jobs, and last config
            or None if session cannot be resumed.
        """
        session = self.load_session(session_id)
        if not session:
            return None

        if session.status == "completed":
            self.logger.info(f"Session {session_id} already completed")
            return None

        checkpoint = self.get_latest_checkpoint(session_id)
        if checkpoint:
            self.logger.info(f"Resuming from checkpoint: {checkpoint.checkpoint_id}")
            return {
                "session_id": session_id,
                "checkpoint_id": checkpoint.checkpoint_id,
                "pending_jobs": checkpoint.pending_jobs,
                "completed_jobs": checkpoint.completed_jobs,
                "failed_jobs": checkpoint.failed_jobs,
                "config_snapshot": checkpoint.config_snapshot,
                "resume_from_index": len(checkpoint.completed_jobs)
                + len(checkpoint.failed_jobs),
            }

        # No checkpoint, resume from session manifest
        pending = [
            j["job_id"]
            for j in session.jobs
            if j.get("status") not in ("completed", "error")
        ]

        return {
            "session_id": session_id,
            "checkpoint_id": None,
            "pending_jobs": pending,
            "completed_jobs": [
                j["job_id"] for j in session.jobs if j.get("status") == "completed"
            ],
            "failed_jobs": [
                j["job_id"] for j in session.jobs if j.get("status") == "error"
            ],
            "config_snapshot": session.to_dict(),
            "resume_from_index": 0,
        }

    def finalize_session(self, status: str = "completed") -> None:
        """Mark session as complete and save final state."""
        if not self._current_session:
            return

        self._current_session.status = status
        self._current_session.updated_at = time.time()
        self._update_counts()

        self._save_session(self._current_session)
        self.logger.info(
            f"Session {self._current_session.session_id} finalized: {status} "
            f"({self._current_session.completed_count}/"
            f"{self._current_session.total_jobs} completed)"
        )

    def pause_session(self) -> None:
        """Pause current session for later resumption."""
        if self._current_session:
            self._current_session.status = "paused"
            self._current_session.updated_at = time.time()
            self.create_checkpoint()
            self._save_session(self._current_session)
            self.logger.info(f"Session {self._current_session.session_id} paused")

    def cleanup_old_sessions(self, keep_last: int = 10) -> None:
        """Remove old completed sessions."""
        sessions = sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for old in sessions[keep_last:]:
            try:
                data = json.loads(old.read_text(encoding="utf-8"))
                if data.get("status") == "completed":
                    # Remove session and its checkpoints
                    session_id = data.get("session_id", old.stem)
                    checkpoint_dir = self.sessions_dir / session_id
                    if checkpoint_dir.exists():
                        import shutil

                        shutil.rmtree(checkpoint_dir)
                    old.unlink()
                    self.logger.info(f"Cleaned up old session: {session_id}")
            except Exception:
                continue

    def get_session_stats(self) -> dict:
        """Get statistics about all sessions."""
        total = 0
        completed = 0
        running = 0
        failed = 0

        for path in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                total += 1
                if data.get("status") == "completed":
                    completed += 1
                elif data.get("status") == "running":
                    running += 1
                elif data.get("status") == "failed":
                    failed += 1
            except Exception:
                continue

        return {
            "total_sessions": total,
            "completed": completed,
            "running": running,
            "failed": failed,
            "sessions_dir": str(self.sessions_dir),
        }
