"""
ComfyUI Async Generation Engine v2.0 - Checkpoint Resume System
Automatic checkpointing and resumption for long-running batches.
"""

import asyncio
import json
import logging
import signal
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
    completed_jobs: List[str] = field(default_factory=list)
    failed_jobs: List[str] = field(default_factory=list)
    pending_configs: List[Dict] = field(default_factory=list)
    generation_params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "BatchCheckpoint":
        return cls(**data)


@dataclass
class ResumeState:
    """State needed to resume a batch from checkpoint."""
    can_resume: bool
    session_id: Optional[str] = None
    checkpoint_id: Optional[str] = None
    resume_from_index: int = 0
    completed_count: int = 0
    failed_count: int = 0
    remaining_configs: List[Dict] = field(default_factory=list)
    original_params: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class CheckpointResumeManager:
    """
    Manages automatic checkpointing and resumption for long batches.

    Features:
    - Periodic checkpoints during batch execution
    - Signal-based emergency checkpoint (SIGTERM, SIGINT)
    - Resume from last checkpoint on restart
    - Config preservation for exact reproduction
    - Progress tracking with ETA calculation
    """

    def __init__(
        self,
        session_manager: SessionManager,
        checkpoint_interval: int = 5,  # Every N jobs
        emergency_checkpoint: bool = True,
        checkpoints_dir: str = "checkpoints",
    ):
        self.session_manager = session_manager
        self.checkpoint_interval = checkpoint_interval
        self.emergency_checkpoint = emergency_checkpoint
        self.checkpoints_dir = Path(checkpoints_dir).resolve()
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        self._current_checkpoint: Optional[BatchCheckpoint] = None
        self._jobs_since_checkpoint: int = 0
        self._start_time: Optional[float] = None
        self._shutdown: bool = False

        if emergency_checkpoint:
            self._setup_signal_handlers()

        self.logger = logging.getLogger(__name__)

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for emergency checkpoint."""
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._emergency_checkpoint(s.name))
            )

    async def _emergency_checkpoint(self, signal_name: str) -> None:
        """Create emergency checkpoint on shutdown signal."""
        self.logger.warning(f"Emergency checkpoint due to {signal_name}")
        self._shutdown = True

        if self._current_checkpoint:
            await self._save_checkpoint(self._current_checkpoint, emergency=True)
            self.session_manager.pause_session()
            self.logger.info("Emergency checkpoint saved, session paused")

    def _checkpoint_path(self, session_id: str, checkpoint_id: str, emergency: bool = False) -> Path:
        """Get path for checkpoint file."""
        prefix = "emergency_" if emergency else ""
        return self.checkpoints_dir / f"{prefix}{session_id}_{checkpoint_id}.json"

    async def _save_checkpoint(
        self,
        checkpoint: BatchCheckpoint,
        emergency: bool = False,
    ) -> Path:
        """Save checkpoint to disk."""
        path = self._checkpoint_path(
            checkpoint.checkpoint_id,
            checkpoint.checkpoint_id,
            emergency,
        )

        path.write_text(
            json.dumps(checkpoint.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

        self.logger.info(
            f"Checkpoint saved: {path.name} "
            f"({len(checkpoint.completed_jobs)}/{checkpoint.total_batches} completed)"
        )
        return path

    def load_checkpoint(self, path: Path) -> Optional[BatchCheckpoint]:
        """Load checkpoint from disk."""
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BatchCheckpoint.from_dict(data)
        except Exception as e:
            self.logger.error(f"Failed to load checkpoint {path}: {e}")
            return None

    def find_latest_checkpoint(self, session_id: str) -> Optional[Path]:
        """Find latest checkpoint for session."""
        # Check emergency first
        emergency = list(self.checkpoints_dir.glob(f"emergency_{session_id}_*.json"))
        regular = list(self.checkpoints_dir.glob(f"{session_id}_*.json"))

        all_checkpoints = sorted(emergency + regular, key=lambda p: p.stat().st_mtime, reverse=True)

        return all_checkpoints[0] if all_checkpoints else None

    def start_batch(
        self,
        session_id: str,
        total_batches: int,
        generation_params: Dict[str, Any],
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

        self._current_checkpoint.batch_index = (
            len(self._current_checkpoint.completed_jobs) +
            len(self._current_checkpoint.failed_jobs)
        )

        # Auto-save checkpoint at interval
        if self._jobs_since_checkpoint >= self.checkpoint_interval:
            asyncio.create_task(self._save_checkpoint(self._current_checkpoint))
            self._jobs_since_checkpoint = 0

    def get_progress(self) -> Dict[str, Any]:
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
        """
        Check if batch can be resumed from checkpoint.

        Returns:
            ResumeState with resume information.
        """
        # Check for emergency checkpoint first
        emergency = list(self.checkpoints_dir.glob(f"emergency_{session_id}_*.json"))
        if emergency:
            latest = sorted(emergency, key=lambda p: p.stat().st_mtime, reverse=True)[0]
            checkpoint = self.load_checkpoint(latest)

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

        # Find latest checkpoint
        checkpoint_path = self.find_latest_checkpoint(session_id)
        if not checkpoint_path:
            return ResumeState(
                can_resume=True,
                session_id=session_id,
                resume_from_index=0,
                completed_count=session.completed_count,
                failed_count=session.failed_count,
            )

        checkpoint = self.load_checkpoint(checkpoint_path)
        if not checkpoint:
            return ResumeState(can_resume=False, error="Failed to load checkpoint")

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

    def prepare_resume_batch(
        self,
        resume_state: ResumeState,
        total_configs: List[Dict],
    ) -> List[Dict]:
        """
        Prepare configs for resumed batch.

        Args:
            resume_state: ResumeState from get_resume_state().
            total_configs: Full list of configs for the batch.

        Returns:
            List of remaining configs to process.
        """
        if not resume_state.can_resume:
            return total_configs

        # Skip already completed configs
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
        for checkpoint in self.checkpoints_dir.glob(f"*{session_id}_*.json"):
            try:
                checkpoint.unlink()
                self.logger.debug(f"Removed checkpoint: {checkpoint.name}")
            except Exception:
                pass

        self.logger.info(f"Batch finalized: {session_id}")

    def cleanup_old_checkpoints(self, max_age_hours: float = 24.0) -> int:
        """Remove old checkpoint files."""
        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0

        for checkpoint in self.checkpoints_dir.glob("*.json"):
            if checkpoint.stat().st_mtime < cutoff:
                try:
                    checkpoint.unlink()
                    removed += 1
                except Exception:
                    pass

        if removed > 0:
            self.logger.info(f"Cleaned up {removed} old checkpoints")
        return removed

    def get_checkpoint_stats(self) -> Dict[str, Any]:
        """Get checkpoint directory statistics."""
        checkpoints = list(self.checkpoints_dir.glob("*.json"))
        emergency = [c for c in checkpoints if c.name.startswith("emergency_")]
        regular = [c for c in checkpoints if not c.name.startswith("emergency_")]

        total_size = sum(c.stat().st_size for c in checkpoints)

        return {
            "checkpoints_dir": str(self.checkpoints_dir),
            "total_checkpoints": len(checkpoints),
            "emergency_checkpoints": len(emergency),
            "regular_checkpoints": len(regular),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }
