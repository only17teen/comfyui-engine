"""ComfyUI Async Generation Engine v2.0 - Unified Main Orchestrator
Full end-to-end pipeline integrating all modules.
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from engine.config import ConfigLoader, EngineConfig
from engine.core import setup_logging, MetricsCollector
from engine.prompt_manager import PromptManager, GenerationConfig
from engine.api_client import ComfyUIAsyncClient, ComfyUIJob
from engine.output_handler import OutputHandler
from engine.git_sync import sync_to_git, init_repo, get_git_status
from engine.session_manager import SessionManager
from engine.checkpoint_resume import CheckpointResumeManager, ResumeState
from engine.websocket_manager import WebSocketManager, WSConfig
from engine.metrics_server import MetricsServer
from engine.workflow_validator import WorkflowValidator
from engine.distributed_queue import DistributedWorker, RedisQueue


# ───────────────────────────────────────────────────────────────
# Progress Display
# ───────────────────────────────────────────────────────────────
def print_progress(total: int, completed: int, status: str) -> None:
    pct = (completed / total) * 100 if total > 0 else 0
    bar_len = 40
    filled = int(bar_len * completed / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    status_icon = "✓" if status == "completed" else "✗" if status == "error" else "○"
    print(
        f"\r  [{bar}] {completed}/{total} ({pct:.0f}%) {status_icon} {status[:20]}",
        end="",
        flush=True,
    )
    if completed == total:
        print()


# ───────────────────────────────────────────────────────────────
# Unified Generation Engine
# ───────────────────────────────────────────────────────────────
class UnifiedGenerationEngine:
    """Production-grade orchestrator integrating all v2.0 modules:
    - Config validation (Pydantic)
    - Prompt generation (templates, seeds, weighted random)
    - API client (circuit breaker, retry, WebSocket)
    - Output handling (concurrent downloads, manifests)
    - Session management (crash recovery)
    - Checkpoint/resume (long batches)
    - Metrics server (Prometheus)
    - Git sync (version control)
    - Distributed queue (Redis, multi-GPU)
    - Workflow validation (auto node mapping)
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.metrics = MetricsCollector(window_size=config.metrics_window)
        self.prompt_manager = PromptManager(config)
        self.client = ComfyUIAsyncClient(
            base_url=config.base_url,
            max_concurrent=config.max_concurrent,
            poll_interval=config.poll_interval,
            timeout=config.timeout,
            metrics=self.metrics,
            use_websocket=True,
        )
        self.output_handler: OutputHandler | None = None
        self.session_manager = SessionManager(
            sessions_dir="sessions",
            checkpoint_interval=5,
        )
        self.checkpoint_manager = CheckpointResumeManager(
            session_manager=self.session_manager,
            checkpoint_interval=5,
            emergency_checkpoint=True,
        )
        self.ws_manager: WebSocketManager | None = None
        self.metrics_server: MetricsServer | None = None
        self.validator = WorkflowValidator()

        self._shutdown_event = asyncio.Event()
        self._current_jobs: list[ComfyUIJob] = []
        self._session_id: str | None = None

        self.logger = logging.getLogger(self.__class__.__name__)
        self._setup_signals()

    def _setup_signals(self) -> None:
        # FIX: use get_running_loop() - get_event_loop() is deprecated in Python 3.10+
        # and raises DeprecationWarning.  _setup_signals() is called from __init__
        # which is always called from inside an async context (asyncio.run), so
        # get_running_loop() is always safe here.
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._signal_handler)
        except RuntimeError:
            # Not inside a running event loop (e.g. during tests); skip.
            pass

    def _signal_handler(self) -> None:
        self.logger.warning("Shutdown signal received, initiating graceful shutdown...")
        self._shutdown_event.set()

    async def health_check(self) -> bool:
        healthy = await self.client.health_check()
        if not healthy:
            self.logger.error(f"ComfyUI server unreachable at {self.config.base_url}")
        return healthy

    async def start_metrics_server(self, port: int | None = None) -> None:
        """Start Prometheus metrics server."""
        port = port or getattr(self.config, "metrics_port", 9090)
        self.metrics_server = MetricsServer(self.metrics, port=port)
        await self.metrics_server.start()

    async def stop_metrics_server(self) -> None:
        if self.metrics_server:
            await self.metrics_server.stop()

    async def start_websocket(self) -> None:
        """Start WebSocket connection for real-time updates."""
        ws_url = self.config.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.ws_manager = WebSocketManager(
            config=WSConfig(url=f"{ws_url}/ws"),
            metrics=self.metrics,
        )
        await self.ws_manager.connect()

    async def validate_workflow(self, workflow: dict) -> bool:
        """Validate workflow and log results."""
        result = self.validator.validate(workflow)

        if not result.is_valid:
            for error in result.errors:
                self.logger.error(f"Workflow validation: {error}")
            return False

        for warning in result.warnings:
            self.logger.warning(f"Workflow validation: {warning}")

        if result.suggested_mappings:
            self.logger.info(f"Auto-detected node mappings: {result.suggested_mappings}")

        return True

    async def run_batch(
        self,
        workflow_template: dict,
        batch_size: int = 4,
        num_lora: int = 2,
        template: str | None = None,
        seed_strategy: str = "random",
        fixed_seed: int | None = None,
        tags: list[str] | None = None,
        progress: bool = True,
        resume_session: str | None = None,
    ) -> list[ComfyUIJob]:
        """Execute full generation batch with all v2.0 features."""
        # Validate workflow
        if not await self.validate_workflow(workflow_template):
            raise RuntimeError("Workflow validation failed")

        # Check for resume state
        resume_state: ResumeState | None = None
        if resume_session:
            resume_state = self.checkpoint_manager.get_resume_state(resume_session)
            if resume_state and resume_state.can_resume:
                self.logger.info(f"Resuming session {resume_session} from index {resume_state.resume_from_index}")

        # Generate configurations
        if resume_state and resume_state.can_resume and resume_state.remaining_configs:
            configs = [GenerationConfig(**c) for c in resume_state.remaining_configs]
            self.logger.info(f"Resuming with {len(configs)} remaining configs")
        else:
            configs = self.prompt_manager.generate_batch(
                count=batch_size,
                seed_strategy=seed_strategy,
                num_lora=num_lora,
                template=template,
            )
            self.logger.info(f"Generated {len(configs)} new configurations")

        # Start session tracking
        self._session_id = resume_session or f"session_{int(time.time())}"
        self.checkpoint_manager.start_batch(
            session_id=self._session_id,
            total_batches=len(configs),
            generation_params={
                "batch_size": batch_size,
                "num_lora": num_lora,
                "template": template,
                "seed_strategy": seed_strategy,
                "tags": tags,
            },
        )

        # Build payloads
        payloads: list[dict] = []
        metas: list[dict] = []

        for cfg in configs:
            payload = self.prompt_manager.to_comfy_payload(cfg, workflow_template)
            meta = cfg.to_dict()
            if tags:
                meta["tags"] = list(set(meta.get("tags", []) + tags))
            payloads.append(payload)
            metas.append(meta)

        # Start WebSocket
        await self.start_websocket()

        # Execute batch
        cb = print_progress if progress else None
        self._current_jobs = await self.client.run_batch(payloads, metas, progress_callback=cb)

        # Update checkpoint manager with results
        for job in self._current_jobs:
            self.checkpoint_manager.update_progress(job)
            self.session_manager.update_job(job)

        # Process outputs
        completed = [j for j in self._current_jobs if j.status == "completed"]
        failed = [j for j in self._current_jobs if j.status == "error"]

        self.logger.info(
            f"Batch complete: {len(completed)} OK, {len(failed)} FAILED, " f"{len(self._current_jobs)} TOTAL"
        )

        if completed:
            self.output_handler = OutputHandler(
                output_dir=self.config.output_dir,
                client=self.client,
                keep_sessions=5,
                metrics=self.metrics,
            )
            await self.output_handler.process_batch(completed, client=self.client)

            summary = self.output_handler.get_session_summary()
            self.logger.info(
                f"Session: {summary['session_id']} | "
                f"Images: {summary['total_images']} | "
                f"Size: {summary['total_size_mb']} MB"
            )

        # Print progress stats
        progress_stats = self.checkpoint_manager.get_progress()
        self.logger.info(
            f"Progress: {progress_stats.get('progress_percent', 0)}% | "
            f"ETA: {progress_stats.get('eta_seconds', 'N/A')}s"
        )

        # Finalize
        self.checkpoint_manager.finalize_batch(self._session_id)

        return self._current_jobs

    async def run_distributed(
        self,
        workflow_template: dict,
        redis_url: str = "redis://localhost:6379/0",
        batch_size: int = 4,
        **kwargs,
    ) -> list[ComfyUIJob]:
        """Run as distributed worker consuming from Redis queue."""
        try:
            from engine.distributed_queue import DistributedWorker as _DistributedWorker
        except ImportError:
            self.logger.error("Redis not available. Install: pip install redis")
            return []

        worker = _DistributedWorker(
            redis_url=redis_url,
            max_concurrent=self.config.max_concurrent,
        )

        async def process_job(payload: dict, meta: dict) -> dict:
            job = await self.client.run_job(payload, meta)
            return job.to_dict()

        await worker.connect()
        self.logger.info("Starting distributed worker mode...")
        await worker.start(process_job)
        return []

    async def sync_git(self, repo_path: str = ".", commit_msg: str | None = None) -> dict:
        """Sync with git repository."""
        self.logger.info(f"Git sync: {repo_path}")
        try:
            result = await sync_to_git(repo_path, commit_message=commit_msg)
            self.logger.info(f"Git sync: {result.get('status', 'unknown')}")
            return result
        except Exception as e:
            self.logger.error(f"Git sync failed: {e}")
            return {"status": "error", "error": str(e)}

    async def close(self) -> None:
        """Graceful shutdown of all resources."""
        await self.stop_metrics_server()
        if self.ws_manager:
            await self.ws_manager.disconnect()
        await self.client.close()
        self.logger.info("Engine shutdown complete")

    def get_report(self) -> dict:
        return {
            "config": self.config.model_dump(),
            "jobs": [j.to_dict() for j in self._current_jobs],
            "session_id": self._session_id,
        }


# ───────────────────────────────────────────────────────────────
# CLI Entrypoint
# ───────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(
        description="ComfyUI Async Generation Engine v2.0 - Unified",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic batch
  python main.py --batch 8 --workflow workflows/standard.json

  # High-performance with metrics server
  python main.py --batch 32 --max-concurrent 8 --template cinematic --metrics-port 9090

  # Resume interrupted batch
  python main.py --batch 16 --workflow workflows/standard.json --resume-session session_123456

  # Distributed worker mode
  python main.py --distributed --redis-url redis://localhost:6379/0 --workflow workflows/standard.json

  # Full pipeline with all features
  python main.py --batch 64 --max-concurrent 8 --template cinematic --workflow workflows/cinematic.json --git-sync --metrics-port 9090 --verbose --tags "production,v2.0"

Environment Variables:
  COMFYUI_URL           ComfyUI API endpoint
  COMFYUI_MAX_CONCURRENT  Parallel jobs
  ENGINE_LOG_LEVEL      Logging level
  ENGINE_TIMEOUT        Job timeout seconds
  ENGINE_METRICS_PORT   Metrics server port (default: 9090)
        """,
    )

    # Batch configuration
    parser.add_argument("--batch", type=int, default=4, help="Number of generations")
    parser.add_argument("--lora", type=int, default=2, help="LoRA models per generation")
    parser.add_argument(
        "--template",
        type=str,
        default=None,
        choices=["standard", "portrait", "full_body", "cinematic", "fashion"],
        help="Prompt template",
    )
    parser.add_argument(
        "--seed-strategy",
        type=str,
        default="random",
        choices=["random", "time_based", "sequential", "fixed"],
        help="Seed generation strategy",
    )
    parser.add_argument("--seed", type=int, default=None, help="Fixed seed")
    parser.add_argument("--tags", type=str, default=None, help="Comma-separated tags")

    # Workflow and connection
    parser.add_argument("--workflow", type=str, required=True, help="Path to ComfyUI workflow JSON")
    parser.add_argument("--max-concurrent", type=int, default=None, help="Override parallel jobs")
    parser.add_argument("--base-url", type=str, default=None, help="Override ComfyUI URL")

    # Output and logging
    parser.add_argument("--output-dir", type=str, default=None, help="Override output directory")
    parser.add_argument("--config", type=str, default="config/prompts.yaml", help="Config YAML")
    parser.add_argument("--timeout", type=float, default=None, help="Override job timeout")
    parser.add_argument("--poll-interval", type=float, default=None, help="Override poll interval")

    # Resume
    parser.add_argument("--resume-session", type=str, default=None, help="Resume from session ID")

    # Metrics server
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=None,
        help="Start Prometheus metrics server on port",
    )

    # Distributed mode
    parser.add_argument("--distributed", action="store_true", help="Run as distributed worker (Redis)")
    parser.add_argument(
        "--redis-url",
        type=str,
        default="redis://localhost:6379/0",
        help="Redis URL for distributed mode",
    )

    # Git integration
    parser.add_argument("--git-sync", action="store_true", help="Sync to git after batch")
    parser.add_argument("--repo-path", type=str, default=".", help="Git repository path")
    parser.add_argument("--init-repo", action="store_true", help="Initialize git repo")
    parser.add_argument("--remote", type=str, default=None, help="Git remote URL")
    parser.add_argument("--commit-msg", type=str, default=None, help="Custom commit message")

    # Control
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress bar")
    parser.add_argument("--health-check-only", action="store_true", help="Check ComfyUI health and exit")
    parser.add_argument("--validate-workflow", action="store_true", help="Validate workflow and exit")

    args = parser.parse_args()

    # Load configuration
    engine_config = ConfigLoader.load(args.config)

    # CLI overrides
    if args.base_url:
        engine_config.base_url = args.base_url
    if args.max_concurrent is not None:
        engine_config.max_concurrent = args.max_concurrent
    if args.output_dir:
        engine_config.output_dir = args.output_dir
    if args.timeout is not None:
        engine_config.timeout = args.timeout
    if args.poll_interval is not None:
        engine_config.poll_interval = args.poll_interval
    if args.metrics_port is not None:
        engine_config.metrics_port = args.metrics_port

    # Setup logging
    log_level = logging.DEBUG if args.verbose else getattr(logging, engine_config.log_level)
    setup_logging(level=log_level, json_format=engine_config.json_logging)
    logger = logging.getLogger("main")

    from engine import __version__ as _ver
    logger.info(f"ComfyUI Engine v{_ver} | Config: {args.config}")

    # Health check only
    if args.health_check_only:
        engine = UnifiedGenerationEngine(engine_config)
        healthy = await engine.health_check()
        sys.exit(0 if healthy else 1)

    # Validate workflow only
    if args.validate_workflow:
        try:
            with open(args.workflow, encoding="utf-8") as f:
                workflow = json.load(f)
            engine = UnifiedGenerationEngine(engine_config)
            valid = await engine.validate_workflow(workflow)
            sys.exit(0 if valid else 1)
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            sys.exit(1)

    # Git init
    if args.init_repo:
        await init_repo(args.repo_path, remote_url=args.remote)
        logger.info("Git repository initialized")

    # Load workflow
    try:
        workflow_path = Path(args.workflow)
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow not found: {workflow_path}")
        with open(workflow_path, encoding="utf-8") as f:
            workflow = json.load(f)
        logger.info(f"Workflow loaded: {workflow_path}")
    except Exception as e:
        logger.error(f"Failed to load workflow: {e}")
        sys.exit(1)

    # Initialize engine
    engine = UnifiedGenerationEngine(engine_config)

    # Pre-flight health check
    healthy = await engine.health_check()
    if not healthy:
        logger.error("ComfyUI server not responding. Exiting.")
        sys.exit(1)

    # Start metrics server if requested
    if args.metrics_port:
        await engine.start_metrics_server(args.metrics_port)

    try:
        # Parse tags
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else None

        # Run batch or distributed
        if args.distributed:
            jobs = await engine.run_distributed(
                workflow_template=workflow,
                redis_url=args.redis_url,
                batch_size=args.batch,
            )
        else:
            jobs = await engine.run_batch(
                workflow_template=workflow,
                batch_size=args.batch,
                num_lora=args.lora,
                template=args.template,
                seed_strategy=args.seed_strategy,
                fixed_seed=args.seed,
                tags=tags,
                progress=not args.no_progress,
                resume_session=args.resume_session,
            )

        # Summary
        success = sum(1 for j in jobs if j.status == "completed")
        failed = sum(1 for j in jobs if j.status == "error")
        print(f"\n{'='*60}")
        print(f"  BATCH COMPLETE: {success} OK | {failed} FAILED | {len(jobs)} TOTAL")
        print(f"{'='*60}")

        # Print metrics
        if not args.no_progress:
            from engine.core import MetricsSnapshot

            snapshot = await engine.metrics.snapshot()
            print(f"  Submitted: {snapshot.jobs_submitted}")
            print(f"  Completed: {snapshot.jobs_completed}")
            print(f"  Failed:    {snapshot.jobs_failed}")
            print(f"  Timeout:   {snapshot.jobs_timeout}")
            print(f"  Retries:   {snapshot.retries_total}")
            print(f"{'='*60}")

        # Git sync
        if args.git_sync:
            result = await engine.sync_git(args.repo_path, args.commit_msg)
            if result.get("status") == "committed":
                print(f"  Git: committed and pushed")
            elif result.get("status") == "clean":
                print(f"  Git: working tree clean")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
    except Exception as e:
        logger.exception(f"Engine error: {e}")
        sys.exit(1)
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
