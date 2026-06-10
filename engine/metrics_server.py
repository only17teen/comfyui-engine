"""ComfyUI Async Generation Engine v2.0 - Metrics Server
Prometheus-compatible HTTP endpoint for external monitoring.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import web

from engine.core import MetricsCollector

logger = logging.getLogger(__name__)


class MetricsServer:
    """Lightweight HTTP server exposing Prometheus-compatible metrics.

    Endpoints:
    - GET /metrics    - Prometheus text format
    - GET /health     - Health check
    - GET /status     - Full engine status
    - GET /api/stats  - JSON metrics

    Usage:
        metrics = MetricsServer(metrics_collector, port=9090)
        await metrics.start()
        # ... run engine ...
        await metrics.stop()
    """

    def __init__(
        self,
        metrics_collector: MetricsCollector,
        port: int = 9090,
        host: str = "0.0.0.0",
    ):
        self.metrics = metrics_collector
        self.port = port
        self.host = host
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._start_time = time.time()

    async def start(self) -> None:
        """Start metrics HTTP server."""
        self._app = web.Application()

        # Routes
        self._app.router.add_get("/metrics", self._handle_metrics)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_get("/api/stats", self._handle_api_stats)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        logger.info(f"Metrics server started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop metrics server."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Metrics server stopped")

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Prometheus text format metrics."""
        report = await self.metrics.report()
        lines = []

        # Engine info
        lines.append("# HELP comfyui_engine_info Engine version and uptime")
        lines.append("# TYPE comfyui_engine_info gauge")
        lines.append(
            f'comfyui_engine_info{{version="2.0.0"}} {time.time() - self._start_time}'
        )

        # Counters
        lines.append("")
        lines.append("# HELP comfyui_engine_jobs_total Total jobs by status")
        lines.append("# TYPE comfyui_engine_jobs_total counter")

        counters = report.get("counters", {})
        for name, value in counters.items():
            safe_name = name.replace("-", "_").replace(".", "_")
            lines.append(f"comfyui_engine_{safe_name} {value}")

        # Gauges
        lines.append("")
        lines.append("# HELP comfyui_engine_gauges Current gauge values")
        lines.append("# TYPE comfyui_engine_gauges gauge")

        gauges = report.get("gauges", {})
        for name, value in gauges.items():
            safe_name = name.replace("-", "_").replace(".", "_")
            lines.append(f"comfyui_engine_{safe_name} {value}")

        # Histograms
        lines.append("")
        lines.append("# HELP comfyui_engine_histograms Histogram statistics")
        lines.append("# TYPE comfyui_engine_histograms summary")

        histograms = report.get("histograms", {})
        for name, stats in histograms.items():
            safe_name = name.replace("-", "_").replace(".", "_")
            lines.append(f'comfyui_engine_{safe_name}_count {stats.get("count", 0)}')
            lines.append(
                f'comfyui_engine_{safe_name}_sum {stats.get("mean", 0) * stats.get("count", 0)}'
            )
            lines.append(f'comfyui_engine_{safe_name}_p50 {stats.get("p50", 0)}')
            lines.append(f'comfyui_engine_{safe_name}_p95 {stats.get("p95", 0)}')
            lines.append(f'comfyui_engine_{safe_name}_p99 {stats.get("p99", 0)}')

        # Uptime
        lines.append("")
        lines.append("# HELP comfyui_engine_uptime_seconds Engine uptime")
        lines.append("# TYPE comfyui_engine_uptime_seconds gauge")
        lines.append(f"comfyui_engine_uptime_seconds {time.time() - self._start_time}")

        body = "\n".join(lines) + "\n"
        return web.Response(text=body, content_type="text/plain; version=0.0.4")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Simple health check."""
        return web.json_response(
            {
                "status": "healthy",
                "uptime": time.time() - self._start_time,
                "timestamp": time.time(),
            }
        )

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Full engine status."""
        report = await self.metrics.report()
        snapshot = await self.metrics.snapshot()

        return web.json_response(
            {
                "engine": {
                    "version": "2.0.0",
                    "uptime_seconds": time.time() - self._start_time,
                    "status": "running",
                },
                "metrics": {
                    "counters": report.get("counters", {}),
                    "gauges": report.get("gauges", {}),
                    "histograms": report.get("histograms", {}),
                },
                "snapshot": {
                    "jobs_submitted": snapshot.jobs_submitted,
                    "jobs_completed": snapshot.jobs_completed,
                    "jobs_failed": snapshot.jobs_failed,
                    "jobs_timeout": snapshot.jobs_timeout,
                    "queue_depth": snapshot.queue_depth,
                    "active_workers": snapshot.active_workers,
                },
            }
        )

    async def _handle_api_stats(self, request: web.Request) -> web.Response:
        """JSON API for programmatic access."""
        report = await self.metrics.report()
        return web.json_response(report)
