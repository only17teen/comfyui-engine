"""ComfyUI Async Generation Engine v6.0 - Distributed Tracing Dashboard
Jaeger UI integration with custom trace analysis and visualization.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class TraceSpan:
    """Represents a single span in a distributed trace."""

    trace_id: str
    span_id: str
    parent_id: str | None
    operation_name: str
    service_name: str
    start_time: float
    duration_ms: float
    tags: dict[str, str] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    status: str = "ok"


@dataclass
class TraceSummary:
    """Summary of a complete distributed trace."""

    trace_id: str
    root_service: str
    root_operation: str
    total_spans: int
    total_duration_ms: float
    services: list[str]
    errors: int
    warnings: int
    start_time: float
    end_time: float


@dataclass
class ServiceMetrics:
    """Metrics for a specific service."""

    service_name: str
    total_requests: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    throughput_per_sec: float = 0.0


class TracingDashboard:
    """Distributed tracing dashboard with Jaeger UI integration.

    Features:
    - Trace search and filtering
    - Service dependency graph
    - Latency distribution analysis
    - Error rate tracking
    - Custom trace aggregations
    - Real-time trace streaming
    - Performance bottleneck detection
    """

    def __init__(
        self,
        jaeger_url: str = "http://localhost:16686",
        api_timeout: float = 30.0,
    ):
        self.jaeger_url = jaeger_url
        self.api_timeout = api_timeout
        self._session: aiohttp.ClientSession | None = None
        self._trace_cache: dict[str, list[TraceSpan]] = {}
        self._service_metrics: dict[str, ServiceMetrics] = {}
        self._running = False

    async def start(self) -> None:
        """Start the tracing dashboard."""
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.api_timeout))
        self._running = True
        logger.info(f"Tracing dashboard started (Jaeger: {self.jaeger_url})")

    async def stop(self) -> None:
        """Stop the tracing dashboard."""
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Tracing dashboard stopped")

    async def search_traces(
        self,
        service: str | None = None,
        operation: str | None = None,
        tags: dict[str, str] | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
        limit: int = 100,
    ) -> list[TraceSummary]:
        """Search for traces in Jaeger.

        Args:
            service: Service name to filter by
            operation: Operation name to filter by
            tags: Tags to filter by
            start_time: Start time (Unix timestamp)
            end_time: End time (Unix timestamp)
            limit: Maximum number of traces to return

        Returns:
            List of trace summaries.
        """
        if not self._session:
            raise RuntimeError("Dashboard not started")

        params = {"limit": limit}
        if service:
            params["service"] = service
        if operation:
            params["operation"] = operation
        if tags:
            for key, value in tags.items():
                params[f"tags.{key}"] = value
        if start_time:
            params["start"] = int(start_time * 1000000)  # Microseconds
        if end_time:
            params["end"] = int(end_time * 1000000)

        try:
            async with self._session.get(
                f"{self.jaeger_url}/api/traces",
                params=params,
            ) as response:
                if response.status != 200:
                    logger.warning(f"Jaeger search failed: {response.status}")
                    return []

                data = await response.json()
                traces = data.get("data", [])

                return [self._parse_trace_summary(t) for t in traces]

        except Exception as e:
            logger.error(f"Trace search error: {e}")
            return []

    async def get_trace(self, trace_id: str) -> list[TraceSpan] | None:
        """Get full trace details by ID.

        Args:
            trace_id: Trace ID to retrieve

        Returns:
            List of spans or None if not found.
        """
        if not self._session:
            raise RuntimeError("Dashboard not started")

        # Check cache
        if trace_id in self._trace_cache:
            return self._trace_cache[trace_id]

        try:
            async with self._session.get(
                f"{self.jaeger_url}/api/traces/{trace_id}",
            ) as response:
                if response.status != 200:
                    return None

                data = await response.json()
                trace_data = data.get("data", [])

                if not trace_data:
                    return None

                spans = self._parse_trace_spans(trace_data[0])
                self._trace_cache[trace_id] = spans
                return spans

        except Exception as e:
            logger.error(f"Trace retrieval error: {e}")
            return None

    async def get_service_dependencies(self) -> dict[str, list[str]]:
        """Get service dependency graph.

        Returns:
            Dictionary mapping service names to their dependencies.
        """
        if not self._session:
            raise RuntimeError("Dashboard not started")

        try:
            async with self._session.get(
                f"{self.jaeger_url}/api/dependencies",
            ) as response:
                if response.status != 200:
                    return {}

                data = await response.json()
                dependencies = {}

                for dep in data.get("data", []):
                    parent = dep.get("parent")
                    child = dep.get("child")
                    if parent and child:
                        if parent not in dependencies:
                            dependencies[parent] = []
                        dependencies[parent].append(child)

                return dependencies

        except Exception as e:
            logger.error(f"Dependency retrieval error: {e}")
            return {}

    async def get_service_metrics(
        self,
        service: str,
        lookback_hours: float = 1.0,
    ) -> ServiceMetrics | None:
        """Get metrics for a specific service.

        Args:
            service: Service name
            lookback_hours: Hours to look back for metrics

        Returns:
            ServiceMetrics or None.
        """
        if not self._session:
            raise RuntimeError("Dashboard not started")

        end_time = time.time()
        start_time = end_time - (lookback_hours * 3600)

        traces = await self.search_traces(
            service=service,
            start_time=start_time,
            end_time=end_time,
            limit=1000,
        )

        if not traces:
            return None

        # Calculate metrics
        latencies = [t.total_duration_ms for t in traces]
        errors = sum(t.errors for t in traces)
        total = len(traces)

        if not latencies:
            return None

        latencies.sort()
        p95_idx = int(len(latencies) * 0.95)
        p99_idx = int(len(latencies) * 0.99)

        metrics = ServiceMetrics(
            service_name=service,
            total_requests=total,
            error_count=errors,
            avg_latency_ms=sum(latencies) / len(latencies),
            p95_latency_ms=latencies[min(p95_idx, len(latencies) - 1)],
            p99_latency_ms=latencies[min(p99_idx, len(latencies) - 1)],
            throughput_per_sec=total / (lookback_hours * 3600),
        )

        self._service_metrics[service] = metrics
        return metrics

    async def find_bottlenecks(
        self,
        trace_id: str,
        threshold_ms: float = 100.0,
    ) -> list[dict[str, Any]]:
        """Find performance bottlenecks in a trace.

        Args:
            trace_id: Trace ID to analyze
            threshold_ms: Latency threshold for bottleneck detection

        Returns:
            List of bottleneck spans with details.
        """
        spans = await self.get_trace(trace_id)
        if not spans:
            return []

        bottlenecks = []
        for span in spans:
            if span.duration_ms > threshold_ms:
                bottlenecks.append(
                    {
                        "span_id": span.span_id,
                        "operation": span.operation_name,
                        "service": span.service_name,
                        "duration_ms": span.duration_ms,
                        "tags": span.tags,
                        "percentage_of_total": 0.0,  # Would need trace total duration
                    }
                )

        # Sort by duration (descending)
        bottlenecks.sort(key=lambda x: x["duration_ms"], reverse=True)
        return bottlenecks

    async def analyze_latency_distribution(
        self,
        service: str,
        operation: str | None = None,
        lookback_hours: float = 1.0,
    ) -> dict[str, Any]:
        """Analyze latency distribution for a service/operation.

        Returns:
            Dictionary with latency distribution statistics.
        """
        end_time = time.time()
        start_time = end_time - (lookback_hours * 3600)

        traces = await self.search_traces(
            service=service,
            operation=operation,
            start_time=start_time,
            end_time=end_time,
            limit=1000,
        )

        if not traces:
            return {"error": "No traces found"}

        durations = [t.total_duration_ms for t in traces]
        durations.sort()

        n = len(durations)
        return {
            "service": service,
            "operation": operation,
            "sample_size": n,
            "min_ms": durations[0],
            "max_ms": durations[-1],
            "mean_ms": sum(durations) / n,
            "median_ms": durations[n // 2],
            "p50_ms": durations[n // 2],
            "p90_ms": durations[int(n * 0.9)],
            "p95_ms": durations[int(n * 0.95)],
            "p99_ms": durations[int(n * 0.99)],
            "stddev_ms": (sum((d - sum(durations) / n) ** 2 for d in durations) / n) ** 0.5,
            "histogram": self._create_histogram(durations),
        }

    async def get_error_analysis(
        self,
        service: str | None = None,
        lookback_hours: float = 1.0,
    ) -> dict[str, Any]:
        """Analyze error patterns across traces.

        Returns:
            Dictionary with error analysis.
        """
        end_time = time.time()
        start_time = end_time - (lookback_hours * 3600)

        traces = await self.search_traces(
            service=service,
            start_time=start_time,
            end_time=end_time,
            limit=1000,
        )

        if not traces:
            return {"error": "No traces found"}

        total = len(traces)
        errors = sum(t.errors for t in traces)
        warnings = sum(t.warnings for t in traces)

        # Group by service
        service_errors = {}
        for trace in traces:
            for svc in trace.services:
                if svc not in service_errors:
                    service_errors[svc] = {"errors": 0, "total": 0}
                service_errors[svc]["total"] += 1
                service_errors[svc]["errors"] += trace.errors

        return {
            "total_traces": total,
            "total_errors": errors,
            "total_warnings": warnings,
            "error_rate": errors / total if total > 0 else 0.0,
            "warning_rate": warnings / total if total > 0 else 0.0,
            "service_breakdown": {
                svc: {
                    "errors": data["errors"],
                    "total": data["total"],
                    "error_rate": (data["errors"] / data["total"] if data["total"] > 0 else 0.0),
                }
                for svc, data in service_errors.items()
            },
        }

    def _parse_trace_summary(self, trace_data: dict[str, Any]) -> TraceSummary:
        """Parse trace data into TraceSummary."""
        spans = trace_data.get("spans", [])
        if not spans:
            return TraceSummary(
                trace_id=trace_data.get("traceID", ""),
                root_service="unknown",
                root_operation="unknown",
                total_spans=0,
                total_duration_ms=0.0,
                services=[],
                errors=0,
                warnings=0,
                start_time=0.0,
                end_time=0.0,
            )

        # Find root span (no parent or parent not in trace)
        root_span = spans[0]
        for span in spans:
            if not span.get("references") or all(
                ref.get("refType") != "CHILD_OF" for ref in span.get("references", [])
            ):
                root_span = span
                break

        services = list({span.get("process", {}).get("serviceName", "unknown") for span in spans})

        start_times = [span.get("startTime", 0) for span in spans]
        durations = [span.get("duration", 0) for span in spans]

        errors = sum(
            1 for span in spans if any(tag.get("key") == "error" and tag.get("value") for tag in span.get("tags", []))
        )

        warnings = sum(
            1 for span in spans if any(tag.get("key") == "warning" and tag.get("value") for tag in span.get("tags", []))
        )

        return TraceSummary(
            trace_id=trace_data.get("traceID", ""),
            root_service=root_span.get("process", {}).get("serviceName", "unknown"),
            root_operation=root_span.get("operationName", "unknown"),
            total_spans=len(spans),
            total_duration_ms=max(durations) if durations else 0.0,
            services=services,
            errors=errors,
            warnings=warnings,
            start_time=min(start_times) / 1000000 if start_times else 0.0,
            end_time=((max(start_times) + max(durations)) / 1000000 if start_times else 0.0),
        )

    def _parse_trace_spans(self, trace_data: dict[str, Any]) -> list[TraceSpan]:
        """Parse trace data into list of TraceSpan objects."""
        spans = []
        for span_data in trace_data.get("spans", []):
            process = span_data.get("process", {})
            tags = {tag.get("key", ""): str(tag.get("value", "")) for tag in span_data.get("tags", [])}

            parent_id = None
            for ref in span_data.get("references", []):
                if ref.get("refType") == "CHILD_OF":
                    parent_id = ref.get("spanID")
                    break

            spans.append(
                TraceSpan(
                    trace_id=trace_data.get("traceID", ""),
                    span_id=span_data.get("spanID", ""),
                    parent_id=parent_id,
                    operation_name=span_data.get("operationName", ""),
                    service_name=process.get("serviceName", "unknown"),
                    start_time=span_data.get("startTime", 0) / 1000000,
                    duration_ms=span_data.get("duration", 0),
                    tags=tags,
                    logs=span_data.get("logs", []),
                    references=span_data.get("references", []),
                    status="error" if tags.get("error") == "true" else "ok",
                )
            )

        return spans

    def _create_histogram(self, data: list[float], bins: int = 10) -> list[dict[str, float]]:
        """Create histogram from latency data."""
        if not data:
            return []

        min_val = min(data)
        max_val = max(data)
        bin_width = (max_val - min_val) / bins if max_val > min_val else 1

        histogram = []
        for i in range(bins):
            lower = min_val + i * bin_width
            upper = min_val + (i + 1) * bin_width
            count = sum(1 for d in data if lower <= d < upper)
            histogram.append(
                {
                    "bin_start": lower,
                    "bin_end": upper,
                    "count": count,
                    "percentage": count / len(data) * 100,
                }
            )

        return histogram

    def get_stats(self) -> dict[str, Any]:
        """Get dashboard statistics."""
        return {
            "jaeger_url": self.jaeger_url,
            "trace_cache_size": len(self._trace_cache),
            "service_metrics_count": len(self._service_metrics),
            "running": self._running,
        }


# Global dashboard instance
_global_tracing_dashboard: TracingDashboard | None = None


def get_tracing_dashboard() -> TracingDashboard | None:
    """Get global tracing dashboard instance."""
    return _global_tracing_dashboard


async def initialize_tracing_dashboard(
    jaeger_url: str = "http://localhost:16686",
) -> TracingDashboard:
    """Initialize global tracing dashboard."""
    global _global_tracing_dashboard
    _global_tracing_dashboard = TracingDashboard(jaeger_url)
    await _global_tracing_dashboard.start()
    return _global_tracing_dashboard


if __name__ == "__main__":

    async def main():
        dashboard = await initialize_tracing_dashboard()

        # Get stats
        stats = dashboard.get_stats()
        print(f"Dashboard stats: {stats}")

        # Note: Actual Jaeger queries require a running Jaeger instance
        # This is a demonstration of the API

        await dashboard.stop()

    asyncio.run(main())
