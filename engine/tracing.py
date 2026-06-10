"""OpenTelemetry distributed tracing setup.

Addresses Issue #32: OpenTelemetry distributed tracing.
"""
import os
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    import httpx
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

def setup_tracing(app: Any = None, service_name: str = "comfyui-engine"):
    """Initialize OpenTelemetry tracing if available and enabled."""
    if not HAS_OTEL:
        logger.warning("OpenTelemetry not installed. Tracing disabled.")
        return
        
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set. Tracing disabled.")
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    
    if app:
        try:
            FastAPIInstrumentor.instrument_app(app)
            logger.info(f"Instrumented FastAPI app with OpenTelemetry: {service_name}")
        except Exception as e:
            logger.error(f"Failed to instrument FastAPI: {e}")
            
    try:
        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        logger.error(f"Failed to instrument HTTPX: {e}")

def get_tracer(name: str):
    """Get a tracer instance."""
    if HAS_OTEL:
        return trace.get_tracer(name)
    
    # Dummy tracer for when OTEL is missing
    class DummySpan:
        def set_attribute(self, *args, **kwargs): pass
        def set_status(self, *args, **kwargs): pass
        def record_exception(self, *args, **kwargs): pass
        def end(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args, **kwargs): pass

    class DummyTracer:
        def start_as_current_span(self, *args, **kwargs):
            return DummySpan()
            
    return DummyTracer()
