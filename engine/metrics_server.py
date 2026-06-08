"""ComfyUI Async Generation Engine v5.1 - SLI/SLO Metrics Server
Kiro Protocol: Service Level Indicators and Objectives with alerting.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

import aiohttp
from aiohttp import web

from engine.core import MetricsCollector

logger = logging.getLogger(__name__)


class SLOStatus(Enum):
    """SLO compliance status."""

    HEALTHY = auto()      # Well within SLO
    WARNING = auto()      # Approaching SLO boundary
    BREACHED = auto()     # SLO breached
    UNKNOWN = auto()      # No data available


@dataclass
class SLI:
    """Service Level Indicator definition."""

    name: str
    description: str
    metric_type: str  # counter, gauge, histogram
    metric_name: str
    unit: str
    aggregation: str  # sum, avg, p50, p95, p99, rate


@dataclass
class SLO:
    """Service Level Objective definition."""

    name: str
    description: str
    sli: SLI
    target: float
    warning_threshold: float  # Percentage of target (e.g., 0.9 for 90%)
    window_minutes: int = 5
    alert_channels: list[str] = field(default_factory=list)


@dataclass
class SLOMeasurement:
    """Single SLO measurement."""

    slo_name: str
    timestamp: float
    value: float
    target: float
    status: SLOStatus
    window_start: float
    window_end: float
    details: dict[str, Any] = field(default_factory=dict)


class SLIDefinitions:
    """Standard SLI definitions for ComfyUI Engine."""

    JOB_SUBMISSION_RATE = SLI(
        name="job_submission_rate",
        description="Rate of job submissions per second",
        metric_type="counter",
        metric_name="jobs_submitted",
        unit="jobs/sec",
        aggregation="rate",
    )

    JOB_COMPLETION_RATE = SLI(
        name="job_completion_rate",
        description="Rate of job completions per second",
        metric_type="counter",
        metric_name="jobs_completed",
        unit="jobs/sec",
        aggregation="rate",
    )

    JOB_FAILURE_RATE = SLI(
        name="job_failure_rate",
        description="Percentage of jobs that fail",
        metric_type="counter",
        metric_name="jobs_failed",
        unit="percent",
        aggregation="rate",
    )

    JOB_PROCESSING_LATENCY = SLI(
        name="job_processing_latency",
        description="Time from job start to completion",
        metric_type="histogram",
        metric_name="processing_time",
        unit="seconds",
        aggregation="p95",
    )

    QUEUE_WAIT_TIME = SLI(
        name="queue_wait_time",
        description="Time jobs spend waiting in queue",
        metric_type="histogram",
        metric_name="queue_wait_time",
        unit="seconds",
        aggregation="p95",
    )

    API_ERROR_RATE = SLI(
        name="api_error_rate",
        description="Rate of API errors per minute",
        metric_type="counter",
        metric_name="api_errors",
        unit="errors/min",
        aggregation="rate",
    )

    CIRCUIT_BREAKER_TRIPS = SLI(
        name="circuit_breaker_trips",
        description="Rate of circuit breaker trips per hour",
        metric_type="counter",
        metric_name="circuit_breaker_trips",
        unit="trips/hour",
        aggregation="rate",
    )

    DOWNLOAD_THROUGHPUT = SLI(
        name="download_throughput",
        description="Download throughput in MB/s",
        metric_type="counter",
        metric_name="download_bytes",
        unit="MB/s",
        aggregation="rate",
    )


class SLODefinitions:
    """Standard SLO definitions for ComfyUI Engine."""

    @staticmethod
    def defaults() -> list[SLO]:
        """Get default SLO definitions."""
        return [
            SLO(
                name="job_completion_rate",
                description="95% of jobs should complete successfully",
                sli=SLIDefinitions.JOB_COMPLETION_RATE,
                target=0.95,
                warning_threshold=0.90,
                window_minutes=5,
                alert_channels=["webhook", "log"],
            ),
            SLO(
                name="job_processing_latency",
                description="95% of jobs should complete within 60 seconds",
                sli=SLIDefinitions.JOB_PROCESSING_LATENCY,
                target=60.0,
                warning_threshold=0.80,  # 80% of target = 48s
                window_minutes=5,
                alert_channels=["webhook", "log"],
            ),
            SLO(
                name="queue_wait_time",
                description="95% of jobs should wait less than 10 seconds in queue",
                sli=SLIDefinitions.QUEUE_WAIT_TIME,
                target=10.0,
                warning_threshold=0.70,  # 70% of target = 7s
                window_minutes=5,
                alert_channels=["webhook", "log"],
            ),
            SLO(
                name="api_error_rate",
                description="Less than 1% of API calls should fail",
                sli=SLIDefinitions.API_ERROR_RATE,
                target=0.01,
                warning_threshold=0.50,  # 50% of target = 0.5%
                window_minutes=5,
                alert_channels=["webhook", "log", "pagerduty"],
            ),
            SLO(
                name="circuit_breaker_trips",
                description="Less than 1 circuit breaker trip per hour",
                sli=SLIDefinitions.CIRCUIT_BREAKER_TRIPS,
                target=1.0,
                warning_threshold=0.50,
                window_minutes=60,
                alert_channels=["webhook", "log", "pagerduty"],
            ),
        ]


class SLICalculator:
    """Calculate SLI values from metrics."""

    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics = metrics_collector
        self._history: dict[str, list[tuple[float, float]]] = {}  # metric -> [(timestamp, value)]
        self._lock = asyncio.Lock()

    async def record_metric(self, metric_name: str, value: float) -> None:
        """Record a metric value for historical tracking."""
        async with self._lock:
            if metric_name not in self._history:
                self._history[metric_name] = []
            self._history[metric_name].append((time.time(), value))
            
            # Keep only last 24 hours of data
            cutoff = time.time() - 86400
            self._history[metric_name] = [
                (t, v) for t, v in self._history[metric_name] if t > cutoff
            ]

    async def calculate_sli(self, sli: SLI, window_minutes: int = 5) -> float:
        """Calculate SLI value over a time window."""
        async with self._lock:
            history = self._history.get(sli.metric_name, [])
            if not history:
                return 0.0

            window_start = time.time() - (window_minutes * 60)
            window_data = [v for t, v in history if t >= window_start]

            if not window_data:
                return 0.0

            if sli.aggregation == "sum":
                return sum(window_data)
            elif sli.aggregation == "avg":
                return sum(window_data) / len(window_data)
            elif sli.aggregation == "p50":
                sorted_data = sorted(window_data)
                return sorted_data[len(sorted_data) // 2]
            elif sli.aggregation == "p95":
                sorted_data = sorted(window_data)
                idx = int(len(sorted_data) * 0.95)
                return sorted_data[min(idx, len(sorted_data) - 1)]
            elif sli.aggregation == "p99":
                sorted_data = sorted(window_data)
                idx = int(len(sorted_data) * 0.99)
                return sorted_data[min(idx, len(sorted_data) - 1)]
            elif sli.aggregation == "rate":
                # Calculate rate per unit time
                if len(window_data) < 2:
                    return window_data[0] if window_data else 0.0
                total = sum(window_data)
                return total / window_minutes
            else:
                return sum(window_data)

    async def get_history(self, metric_name: str, window_minutes: int = 5) -> list[tuple[float, float]]:
        """Get metric history for a time window."""
        async with self._lock:
            history = self._history.get(metric_name, [])
            window_start = time.time() - (window_minutes * 60)
            return [(t, v) for t, v in history if t >= window_start]


class SLOEvaluator:
    """Evaluate SLO compliance and trigger alerts."""

    def __init__(
        self,
        metrics_collector: MetricsCollector,
        slos: list[SLO] | None = None,
    ):
        self.metrics = metrics_collector
        self.sli_calculator = SLICalculator(metrics_collector)
        self.slos = slos or SLODefinitions.defaults()
        self._measurements: list[SLOMeasurement] = []
        self._alert_handlers: dict[str, Callable] = {}
        self._lock = asyncio.Lock()

    def register_alert_handler(self, channel: str, handler: Callable) -> None:
        """Register an alert handler for a channel."""
        self._alert_handlers[channel] = handler

    async def evaluate_all(self) -> list[SLOMeasurement]:
        """Evaluate all SLOs and return measurements."""
        measurements = []
        
        for slo in self.slos:
            measurement = await self.evaluate_slo(slo)
            measurements.append(measurement)
            
            # Store measurement
            async with self._lock:
                self._measurements.append(measurement)
                # Keep only last 1000 measurements
                if len(self._measurements) > 1000:
                    self._measurements = self._measurements[-1000:]
            
            # Trigger alerts if breached or warning
            if measurement.status in (SLOStatus.BREACHED, SLOStatus.WARNING):
                await self._trigger_alert(slo, measurement)
        
        return measurements

    async def evaluate_slo(self, slo: SLO) -> SLOMeasurement:
        """Evaluate a single SLO."""
        window_start = time.time() - (slo.window_minutes * 60)
        window_end = time.time()
        
        try:
            value = await self.sli_calculator.calculate_sli(
                slo.sli,
                slo.window_minutes,
            )
            
            # Determine status
            if slo.sli.aggregation in ("rate", "sum"):
                # For rate/sum: lower is better (error rates, etc.)
                if value <= slo.target:
                    status = SLOStatus.HEALTHY
                elif value <= slo.target / slo.warning_threshold:
                    status = SLOStatus.WARNING
                else:
                    status = SLOStatus.BREACHED
            else:
                # For latency: lower is better
                if value <= slo.target:
                    status = SLOStatus.HEALTHY
                elif value <= slo.target / slo.warning_threshold:
                    status = SLOStatus.WARNING
                else:
                    status = SLOStatus.BREACHED
            
            return SLOMeasurement(
                slo_name=slo.name,
                timestamp=time.time(),
                value=value,
                target=slo.target,
                status=status,
                window_start=window_start,
                window_end=window_end,
                details={
                    "sli_name": slo.sli.name,
                    "sli_description": slo.sli.description,
                    "window_minutes": slo.window_minutes,
                    "warning_threshold": slo.warning_threshold,
                },
            )
        except Exception as e:
            logger.error(f"Error evaluating SLO {slo.name}: {e}")
            return SLOMeasurement(
                slo_name=slo.name,
                timestamp=time.time(),
                value=0.0,
                target=slo.target,
                status=SLOStatus.UNKNOWN,
                window_start=window_start,
                window_end=window_end,
                details={"error": str(e)},
            )

    async def _trigger_alert(self, slo: SLO, measurement: SLOMeasurement) -> None:
        """Trigger alerts for SLO breach or warning."""
        for channel in slo.alert_channels:
            handler = self._alert_handlers.get(channel)
            if handler:
                try:
                    await handler(slo, measurement)
                except Exception as e:
                    logger.error(f"Alert handler failed for {channel}: {e}")
            else:
                # Default: log alert
                logger.warning(
                    f"SLO ALERT [{channel}]: {slo.name} is {measurement.status.name} "
                    f"(value={measurement.value:.4f}, target={slo.target:.4f})"
                )

    async def get_measurements(
        self,
        slo_name: str | None = None,
        window_minutes: int = 60,
    ) -> list[SLOMeasurement]:
        """Get recent measurements."""
        async with self._lock:
            cutoff = time.time() - (window_minutes * 60)
            measurements = [m for m in self._measurements if m.timestamp >= cutoff]
            
            if slo_name:
                measurements = [m for m in measurements if m.slo_name == slo_name]
            
            return measurements

    def get_summary(self) -> dict[str, Any]:
        """Get SLO summary statistics."""
        total = len(self.slos)
        healthy = sum(1 for m in self._measurements[-total:] if m.status == SLOStatus.HEALTHY)
        warning = sum(1 for m in self._measurements[-total:] if m.status == SLOStatus.WARNING)
        breached = sum(1 for m in self._measurements[-total:] if m.status == SLOStatus.BREACHED)
        unknown = sum(1 for m in self._measurements[-total:] if m.status == SLOStatus.UNKNOWN)
        
        return {
            "total_slos": total,
            "healthy": healthy,
            "warning": warning,
            "breached": breached,
            "unknown": unknown,
            "compliance_rate": healthy / total if total > 0 else 0.0,
            "measurements_count": len(self._measurements),
        }


class MetricsServer:
    """Enhanced metrics server with SLI/SLO support.
    
    Kiro Protocol optimizations:
    - SLI/SLO metrics with alerting (Rule 11: Observability)
    - Health check endpoints (Rule 4: Reliability)
    - Composite status endpoint (Rule 11: Observability)
    """

    def __init__(
        self,
        metrics_collector: MetricsCollector,
        slo_evaluator: SLOEvaluator | None = None,
        port: int = 9090,
        host: str = "0.0.0.0",
    ):
        self.metrics = metrics_collector
        self.slo_evaluator = slo_evaluator or SLOEvaluator(metrics_collector)
        self.port = port
        self.host = host
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._start_time = time.time()
        self._health_checker: Any | None = None

    def set_health_checker(self, health_checker: Any) -> None:
        """Set health checker for composite health endpoint."""
        self._health_checker = health_checker

    async def start(self) -> None:
        """Start metrics HTTP server with all endpoints."""
        self._app = web.Application()

        # Routes
        self._app.router.add_get("/metrics", self._handle_metrics)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/health/detailed", self._handle_detailed_health)
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_get("/api/stats", self._handle_api_stats)
        self._app.router.add_get("/api/slos", self._handle_slos)
        self._app.router.add_get("/api/slos/{slo_name}", self._handle_slo_detail)
        self._app.router.add_get("/api/sli/history/{metric_name}", self._handle_sli_history)

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
        lines.append(f'comfyui_engine_info{{version="5.1.0"}} {time.time() - self._start_time}')

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
            lines.append(f'comfyui_engine_{safe_name}_sum {stats.get("mean", 0) * stats.get("count", 0)}')
            lines.append(f'comfyui_engine_{safe_name}_p50 {stats.get("p50", 0)}')
            lines.append(f'comfyui_engine_{safe_name}_p95 {stats.get("p95", 0)}')
            lines.append(f'comfyui_engine_{safe_name}_p99 {stats.get("p99", 0)}')

        # SLO metrics
        lines.append("")
        lines.append("# HELP comfyui_engine_slo_status SLO compliance status")
        lines.append("# TYPE comfyui_engine_slo_status gauge")
        
        measurements = await self.slo_evaluator.evaluate_all()
        for m in measurements:
            status_value = {
                SLOStatus.HEALTHY: 0,
                SLOStatus.WARNING: 1,
                SLOStatus.BREACHED: 2,
                SLOStatus.UNKNOWN: -1,
            }.get(m.status, -1)
            lines.append(
                f'comfyui_engine_slo_status{{slo="{m.slo_name}"}} {status_value}'
            )
            lines.append(
                f'comfyui_engine_slo_value{{slo="{m.slo_name}"}} {m.value}'
            )
            lines.append(
                f'comfyui_engine_slo_target{{slo="{m.slo_name}"}} {m.target}'
            )

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

    async def _handle_detailed_health(self, request: web.Request) -> web.Response:
        """Detailed health check with component status."""
        if self._health_checker:
            statuses = await self._health_checker.check_all()
            overall = self._health_checker.get_overall_status(statuses)
            
            return web.json_response(
                {
                    "status": overall,
                    "uptime": time.time() - self._start_time,
                    "timestamp": time.time(),
                    "components": {
                        name: {
                            "status": s.status,
                            "latency_ms": s.latency_ms,
                            "last_check": s.last_check,
                            "details": s.details,
                        }
                        for name, s in statuses.items()
                    },
                }
            )
        
        return web.json_response(
            {
                "status": "healthy",
                "uptime": time.time() - self._start_time,
                "timestamp": time.time(),
                "components": {},
            }
        )

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Full engine status with SLOs."""
        report = await self.metrics.report()
        snapshot = await self.metrics.snapshot()
        measurements = await self.slo_evaluator.evaluate_all()
        slo_summary = self.slo_evaluator.get_summary()

        return web.json_response(
            {
                "engine": {
                    "version": "5.1.0",
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
                "slos": {
                    "summary": slo_summary,
                    "measurements": [
                        {
                            "slo_name": m.slo_name,
                            "value": m.value,
                            "target": m.target,
                            "status": m.status.name,
                            "timestamp": m.timestamp,
                            "window_minutes": m.details.get("window_minutes", 5),
                        }
                        for m in measurements
                    ],
                },
            }
        )

    async def _handle_api_stats(self, request: web.Request) -> web.Response:
        """JSON API for programmatic access."""
        report = await self.metrics.report()
        return web.json_response(report)

    async def _handle_slos(self, request: web.Request) -> web.Response:
        """Get all SLO measurements."""
        measurements = await self.slo_evaluator.evaluate_all()
        return web.json_response(
            {
                "measurements": [
                    {
                        "slo_name": m.slo_name,
                        "value": m.value,
                        "target": m.target,
                        "status": m.status.name,
                        "timestamp": m.timestamp,
                        "window_start": m.window_start,
                        "window_end": m.window_end,
                        "details": m.details,
                    }
                    for m in measurements
                ],
                "summary": self.slo_evaluator.get_summary(),
            }
        )

    async def _handle_slo_detail(self, request: web.Request) -> web.Response:
        """Get specific SLO detail."""
        slo_name = request.match_info["slo_name"]
        measurements = await self.slo_evaluator.get_measurements(slo_name)
        
        if not measurements:
            return web.json_response(
                {"error": f"SLO {slo_name} not found"},
                status=404,
            )
        
        latest = measurements[-1]
        return web.json_response(
            {
                "slo_name": latest.slo_name,
                "current_value": latest.value,
                "target": latest.target,
                "status": latest.status.name,
                "history": [
                    {
                        "timestamp": m.timestamp,
                        "value": m.value,
                        "status": m.status.name,
                    }
                    for m in measurements[-100:]  # Last 100 measurements
                ],
            }
        )

    async def _handle_sli_history(self, request: web.Request) -> web.Response:
        """Get SLI metric history."""
        metric_name = request.match_info["metric_name"]
        window = int(request.query.get("window", 60))  # Default 60 minutes
        
        history = await self.slo_evaluator.sli_calculator.get_history(metric_name, window)
        
        return web.json_response(
            {
                "metric_name": metric_name,
                "window_minutes": window,
                "data_points": len(history),
                "history": [
                    {"timestamp": t, "value": v}
                    for t, v in history
                ],
            }
        )
