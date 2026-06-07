"""
ComfyUI Async Generation Engine v2.0 - Output Handler
Enhanced with structured metadata, EXIF tagging, and session management.
"""

import asyncio
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from engine.api_client import ComfyUIJob, ComfyUIAsyncClient


logger = logging.getLogger(__name__)


class OutputHandler:
    """
    Production-grade output handler:
    - Concurrent downloads with retry
    - Structured metadata (JSON + human-readable TXT)
    - EXIF-style metadata embedding (via sidecar)
    - Session management with rotation
    - Duplicate detection
    """

    def __init__(
        self,
        output_dir: str = "output_models",
        client: Optional[ComfyUIAsyncClient] = None,
        keep_sessions: int = 5,
        metrics=None,
    ):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.keep_sessions = keep_sessions
        self.metrics = metrics

        # Session tracking
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.output_dir / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Download tracking
        self._downloaded: Set[str] = set()
        self._download_semaphore = asyncio.Semaphore(5)  # Max concurrent downloads

        logger.info(f"Output session: {self.session_dir}")

    def _build_metadata(self, job: ComfyUIJob, output_idx: int = 0) -> str:
        """Build human-readable metadata sidecar."""
        meta = job.config_meta
        lines = [
            "=" * 60,
            "  ComfyUI Generation Metadata",
            "=" * 60,
            f"Job ID:        {job.job_id}",
            f"Prompt ID:     {job.prompt_id}",
            f"Status:        {job.status.upper()}",
            "",
            "--- Generation Parameters ---",
            f"Seed:          {meta.get('seed', 'N/A')}",
            f"Resolution:    {meta.get('width', 'N/A')} x {meta.get('height', 'N/A')}",
            f"Steps:         {meta.get('steps', 'N/A')}",
            f"CFG Scale:     {meta.get('cfg_scale', 'N/A')}",
            f"Sampler:       {meta.get('sampler_name', 'N/A')}",
            f"Scheduler:     {meta.get('scheduler', 'N/A')}",
            f"Batch Size:    {meta.get('batch_size', 1)}",
            "",
            "--- Prompts ---",
            f"Positive:      {meta.get('positive_prompt', 'N/A')}",
            f"Negative:      {meta.get('negative_prompt', 'N/A')}",
            f"Template:      {meta.get('prompt_template', 'N/A')}",
            "",
        ]

        # LoRA stack
        loras = meta.get("lora_stack", [])
        if loras:
            lines.append("--- LoRA Stack ---")
            for i, lora in enumerate(loras, 1):
                lines.append(
                    f"  [{i}] {lora['name']}")
                lines.append(
                    f"      Path:   {lora['path']}")
                lines.append(
                    f"      Weight: {lora['weight']} (clip: {lora.get('weight_clip', 'N/A')})")
            lines.append("")

        # Tags
        tags = meta.get("tags", [])
        if tags:
            lines.append(f"Tags:          {', '.join(tags)}")
            lines.append("")

        # Timing
        lines.extend([
            "--- Timing ---",
            f"Created:       {self._fmt_time(job.created_at)}",
            f"Queued:        {self._fmt_time(job.queued_at)}",
            f"Started:       {self._fmt_time(job.started_at)}",
            f"Completed:     {self._fmt_time(job.completed_at)}",
            f"Wait Time:     {job.wait_time:.2f}s",
            f"Process Time:  {job.processing_time:.2f}s",
            f"Total Time:    {job.total_time:.2f}s",
            f"Retries:       {job.retry_count}",
            "",
            f"Output Index:  {output_idx}",
            f"Generated:     {datetime.now().isoformat()}",
            "=" * 60,
        ])

        return "\n".join(lines)

    @staticmethod
    def _fmt_time(ts: Optional[float]) -> str:
        if ts is None:
            return "N/A"
        return datetime.fromtimestamp(ts).isoformat()

    def _save_metadata_file(self, image_path: Path, job: ComfyUIJob, output_idx: int) -> Path:
        """Write .txt sidecar next to image."""
        meta_path = image_path.with_suffix(image_path.suffix + ".txt")
        content = self._build_metadata(job, output_idx)
        meta_path.write_text(content, encoding="utf-8")
        logger.info(f"Metadata saved: {meta_path.name}")
        return meta_path

    def _save_json_metadata(self, job: ComfyUIJob) -> Path:
        """Save structured JSON for programmatic access."""
        json_path = self.session_dir / f"{job.job_id}.json"
        data = {
            "job_id": job.job_id,
            "prompt_id": job.prompt_id,
            "status": job.status,
            "config": job.config_meta,
            "outputs": job.outputs,
            "timing": {
                "created_at": job.created_at,
                "queued_at": job.queued_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "wait_time": job.wait_time,
                "processing_time": job.processing_time,
                "total_time": job.total_time,
            },
            "retry_count": job.retry_count,
            "error": job.error_msg,
            "session_id": self.session_id,
            "downloaded_files": [str(p) for p in job.downloaded_files],
        }
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return json_path

    def _save_session_manifest(self, jobs: List[ComfyUIJob]) -> Path:
        """Save session-level manifest with all jobs."""
        manifest_path = self.session_dir / "_session_manifest.json"
        manifest = {
            "session_id": self.session_id,
            "created_at": datetime.now().isoformat(),
            "output_dir": str(self.session_dir),
            "total_jobs": len(jobs),
            "jobs": [job.to_dict() for job in jobs],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logger.info(f"Session manifest saved: {manifest_path}")
        return manifest_path

    async def _download_single(
        self,
        output: Dict,
        job: ComfyUIJob,
        client: ComfyUIAsyncClient,
        idx: int,
    ) -> Optional[Path]:
        """Download single output with semaphore-guarded concurrency."""
        filename = output.get("filename")
        if not filename:
            return None

        # Duplicate detection
        file_key = f"{job.prompt_id}:{filename}"
        if file_key in self._downloaded:
            logger.debug(f"Skipping duplicate: {filename}")
            return None

        async with self._download_semaphore:
            try:
                img_path = await client.download_output(
                    filename=filename,
                    subfolder=output.get("subfolder", ""),
                    output_type=output.get("type", "output"),
                    save_path=self.session_dir,
                )
                self._downloaded.add(file_key)
                job.downloaded_files.append(img_path)

                # Save sidecar metadata
                self._save_metadata_file(img_path, job, idx)
                return img_path

            except Exception as e:
                logger.error(f"Download failed for {filename}: {e}")
                if self.metrics:
                    await self.metrics.inc("download_errors")
                return None

    async def process_job(
        self,
        job: ComfyUIJob,
        client: Optional[ComfyUIAsyncClient] = None,
    ) -> List[Path]:
        """Process single completed job."""
        if job.status != "completed":
            logger.warning(f"Job {job.job_id} not completed ({job.status}), skipping")
            return []

        api = client or self.client
        if api is None:
            raise RuntimeError("No client provided for download")

        downloaded: List[Path] = []

        # Concurrent downloads for this job's outputs
        tasks = [
            self._download_single(output, job, api, idx)
            for idx, output in enumerate(job.outputs)
        ]
        results = await asyncio.gather(*tasks)

        for path in results:
            if path:
                downloaded.append(path)

        # Save JSON metadata
        self._save_json_metadata(job)

        logger.info(f"Job {job.job_id}: downloaded {len(downloaded)}/{len(job.outputs)} files")
        return downloaded

    async def process_batch(
        self,
        jobs: List[ComfyUIJob],
        client: Optional[ComfyUIAsyncClient] = None,
    ) -> Dict[str, List[Path]]:
        """Process multiple jobs concurrently."""
        results: Dict[str, List[Path]] = {}

        tasks = [self.process_job(job, client) for job in jobs]
        downloaded_lists = await asyncio.gather(*tasks, return_exceptions=True)

        for job, dl_list in zip(jobs, downloaded_lists):
            if isinstance(dl_list, Exception):
                logger.error(f"Batch processing failed for {job.job_id}: {dl_list}")
                results[job.job_id] = []
            else:
                results[job.job_id] = dl_list

        # Save session manifest
        self._save_session_manifest(jobs)

        return results

    def get_session_summary(self) -> Dict:
        """Return session summary statistics."""
        images = list(self.session_dir.glob("*.png")) + list(self.session_dir.glob("*.jpg")) + list(self.session_dir.glob("*.webp"))
        txt_files = list(self.session_dir.glob("*.txt"))
        json_files = list(self.session_dir.glob("*.json"))

        total_size = sum(f.stat().st_size for f in images)

        return {
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "total_images": len(images),
            "total_metadata_txt": len(txt_files),
            "total_metadata_json": len(json_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "images": [str(p) for p in images],
        }

    def cleanup_old_sessions(self) -> None:
        """Remove old sessions, keeping only N most recent."""
        sessions = sorted(
            [d for d in self.output_dir.iterdir() if d.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for old in sessions[self.keep_sessions:]:
            try:
                shutil.rmtree(old)
                logger.info(f"Cleaned up old session: {old.name}")
            except Exception as e:
                logger.warning(f"Failed to remove {old}: {e}")

    def get_disk_usage(self) -> Dict:
        """Get disk usage statistics for output directory."""
        total_size = 0
        file_count = 0
        for f in self.output_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
                file_count += 1

        return {
            "output_dir": str(self.output_dir),
            "total_files": file_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "total_size_gb": round(total_size / (1024 * 1024 * 1024), 3),
        }
