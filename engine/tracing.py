"""
ComfyUI Async Generation Engine v5.0 - OpenTelemetry Tracing
Distributed tracing integration for request observability.
"""

import asyncio
import functools
import logging
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION, DEPLOYMENT_ENVIRONMENT
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TracingConfig:
    """Configuration for OpenTelemetry tracing."""

    def __init__(
        self,
        service_name: str = "comfyui-engine",
        service_version: str = "5.0.0",
        environment: str = "production",
        otlp_endpoint: Optional[str] = None,
        sampler_ratio: float = 0.1,
        console_exporter: bool = False,
    ):
        self.service_name = service_name
        self.service_version = service_version
        self.environment = environment
        self.otlp_endpoint = otlp_endpoint
        self.sampler_ratio = sampler_ratio
        self.console_exporter = console_exporter


class TracingManager:
    """
    Manages OpenTelemetry tracing for the ComfyUI Engine.

    Features:
    - Automatic span creation for async operations
    - Context propagation across async boundaries
    - Custom attributes and events
    - Error recording with stack traces
    - Parent-child span relationships
    - Batch export to OTLP collector
    """

    def __init__(self, config: TracingConfig):
        self.config = config
        self._provider: Optional[TracerProvider] = None
        self._tracer = None
        self._propagator = TraceContextTextMapPropagator()
        self._initialized = False

    def initialize(self) -> None:
        """Initialize the tracing provider and exporter."""
        if self._initialized:
            return

        resource = Resource.create({
            SERVICE_NAME: self.config.service_name,
            SERVICE_VERSION: self.config.service_version,
            DEPLOYMENT_ENVIRONMENT: self.config.environment,
        })

        self._provider = TracerProvider(resource=resource)

        # OTLP exporter
        if self.config.otlp_endpoint:
            otlp_exporter = OTLPSpanExporter(
                endpoint=self.config.otlp_endpoint,
                insecure=True,
            )
            self._provider.add_span_processor(
                BatchSpanProcessor(otlp_exporter)
            )
            logger.info(f"OTLP exporter configured: {self.config.otlp_endpoint}")

        # Console exporter for debugging
        if self.config.console_exporter:
            console_exporter = ConsoleSpanExporter()
            self._provider.add_span_processor(
                BatchSpanProcessor(console_exporter)
            )
            logger.info("Console span exporter enabled")

        trace.set_tracer_provider(self._provider)
        self._tracer = trace.get_tracer(self.config.service_name)

        # Instrument aiohttp
        AioHttpClientInstrumentor().instrument()

        self._initialized = True
        logger.info("Tracing manager initialized")

    def shutdown(self) -> None:
        """Shutdown the tracing provider."""
        if self._provider:
            self._provider.shutdown()
            self._initialized = False
            logger.info("Tracing manager shutdown")

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """Context manager for creating a span."""
        if not self._initialized or not self._tracer:
            yield None
            return

        with self._tracer.start_as_current_span(
            name,
            kind=kind,
            attributes=attributes,
        ) as span:
            yield span

    async def async_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """Async context manager for creating a span."""
        if not self._initialized or not self._tracer:
            return _NullSpan()

        return self._tracer.start_as_current_span(
            name,
            kind=kind,
            attributes=attributes,
        )

    def trace_method(
        self,
        name: Optional[str] = None,
        kind: SpanKind = SpanKind.INTERNAL,
    ) -> Callable:
        """Decorator for tracing method calls."""
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            span_name = name or func.__qualname__

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> T:
                if not self._initialized:
                    return await func(*args, **kwargs)

                with self.span(span_name, kind) as span:
                    if span:
                        span.set_attribute("function.args_count", len(args))
                        span.set_attribute("function.kwargs_count", len(kwargs))

                    try:
                        result = await func(*args, **kwargs)
                        if span:
                            span.set_status(Status(StatusCode.OK))
                        return result
                    except Exception as e:
                        if span:
                            span.set_status(
                                Status(StatusCode.ERROR, str(e))
                            )
                            span.record_exception(e)
                        raise

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs) -> T:
                if not self._initialized:
                    return func(*args, **kwargs)

                with self.span(span_name, kind) as span:
                    if span:
                        span.set_attribute("function.args_count", len(args))
                        span.set_attribute("function.kwargs_count", len(kwargs))

                    try:
                        result = func(*args, **kwargs)
                        if span:
                            span.set_status(Status(StatusCode.OK))
                        return result
                    except Exception as e:
                        if span:
                            span.set_status(
                                Status(StatusCode.ERROR, str(e))
                            )
                            span.record_exception(e)
                        raise

            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator

    def extract_context(self, carrier: Dict[str, str]) -> trace.Context:
        """Extract trace context from carrier (e.g., HTTP headers)."""
        return self._propagator.extract(carrier)

    def inject_context(self, carrier: Dict[str, str]) -> Dict[str, str]:
        """Inject trace context into carrier (e.g., HTTP headers)."""
        self._propagator.inject(carrier)
        return carrier

    def get_current_span(self) -> Optional[trace.Span]:
        """Get the current active span."""
        if not self._initialized:
            return None
        return trace.get_current_span()

    def add_event(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add event to current span."""
        span = self.get_current_span()
        if span:
            span.add_event(name, attributes=attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set attribute on current span."""
        span = self.get_current_span()
        if span:
            span.set_attribute(key, value)

    def record_exception(self, exception: Exception) -> None:
        """Record exception on current span."""
        span = self.get_current_span()
        if span:
            span.record_exception(exception)
            span.set_status(Status(StatusCode.ERROR, str(exception)))


class _NullSpan:
    """Null object pattern for when tracing is disabled."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, *args):
        pass

    def add_event(self, *args):
        pass

    def record_exception(self, *args):
        pass

    def set_status(self, *args):
        pass


# Global tracing manager instance
_global_tracing_manager: Optional[TracingManager] = None


def get_tracing_manager() -> TracingManager:
    """Get or create global tracing manager."""
    global _global_tracing_manager
    if _global_tracing_manager is None:
        _global_tracing_manager = TracingManager(TracingConfig())
    return _global_tracing_manager


def initialize_tracing(
    service_name: str = "comfyui-engine",
    service_version: str = "5.0.0",
    environment: str = "production",
    otlp_endpoint: Optional[str] = None,
    sampler_ratio: float = 0.1,
    console_exporter: bool = False,
) -> TracingManager:
    """Initialize global tracing with configuration."""
    global _global_tracing_manager

    config = TracingConfig(
        service_name=service_name,
        service_version=service_version,
        environment=environment,
        otlp_endpoint=otlp_endpoint,
        sampler_ratio=sampler_ratio,
        console_exporter=console_exporter,
    )

    _global_tracing_manager = TracingManager(config)
    _global_tracing_manager.initialize()

    return _global_tracing_manager


def trace_span(name: str, kind: SpanKind = SpanKind.INTERNAL):
    """Decorator for tracing spans using global manager."""
    return get_tracing_manager().trace_method(name, kind)


def trace_async(name: str, kind: SpanKind = SpanKind.INTERNAL):
    """Decorator for tracing async functions."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            manager = get_tracing_manager()
            if not manager._initialized:
                return await func(*args, **kwargs)

            async with manager.async_span(name, kind) as span:
                if span and hasattr(span, 'set_attribute'):
                    span.set_attribute("function.name", func.__qualname__)
                return await func(*args, **kwargs)
        return wrapper
    return decorator
