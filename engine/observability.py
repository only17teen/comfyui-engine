from __future__ import annotations
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
__all__ = ["observe","Span"]
try: from opentelemetry import trace as _otel_trace; _OTEL=True
except ImportError: _OTEL=False
try: import structlog as _structlog; _STRUCTLOG=True
except ImportError: _STRUCTLOG=False
try: from engine.metrics import get_registry as _get_registry; _METRICS=True
except ImportError: _METRICS=False

class _N:
    def set_attribute(self,*a:Any,**k:Any)->None: pass
    def set_status(self,*a:Any,**k:Any)->None: pass
    def record_exception(self,*a:Any)->None: pass
    def get_span_context(self)->"_NC": return _NC()
    def __enter__(self)->"_N": return self
    def __exit__(self,*_:Any)->None: pass
class _NC:
    trace_id=0; is_valid=False
class _NL:
    def bind(self,**k:Any)->"_NL": return self
    def info(self,*a:Any,**k:Any)->None: pass
    def error(self,*a:Any,**k:Any)->None: pass

@dataclass
class Span:
    name: str
    _otel_span: Any = field(repr=False)
    _log: Any = field(repr=False)
    _start_ns: float = field(repr=False)
    _error: Exception|None = field(default=None, repr=False)
    def set_attribute(self, key: str, value: Any) -> None:
        try: self._otel_span.set_attribute(key, value)
        except Exception: pass
        try: self._log=self._log.bind(**{key:value})
        except Exception: pass
    def record_metric(self, name: str, value: float, attrs: dict[str,Any]|None=None) -> None:
        if not _METRICS: return
        try:
            r=_get_registry()
            if value==int(value) and value>=0: r.request_counter().add(int(value), attrs or {})
            else: r.request_duration().record(value, attrs or {})
        except Exception: pass
    def set_error(self, exc: Exception) -> None:
        self._error=exc
        try:
            from opentelemetry.trace import StatusCode
            self._otel_span.set_status(StatusCode.ERROR, str(exc)); self._otel_span.record_exception(exc)
        except Exception: pass
        try: self._log.error(f"{self.name}.error", error=str(exc))
        except Exception: pass
    @property
    def elapsed_ms(self) -> float: return (time.perf_counter()-self._start_ns)*1000

@asynccontextmanager
async def observe(name: str, *, attributes: dict[str,Any]|None=None, record_duration: bool=True):
    otel_span: Any=_N(); otel_ctx=None; trace_id="noop"
    if _OTEL:
        try:
            tracer=_otel_trace.get_tracer("comfyui_engine"); otel_ctx=tracer.start_as_current_span(name)
            otel_span=otel_ctx.__enter__()
            if attributes:
                for k,v in attributes.items(): otel_span.set_attribute(k,v)
            sc=otel_span.get_span_context(); trace_id=format(sc.trace_id,"032x") if sc.is_valid else "noop"
        except Exception: otel_span=_N(); otel_ctx=None
    log: Any=_NL()
    if _STRUCTLOG:
        try: log=_structlog.get_logger().bind(span=name,trace_id=trace_id,**(attributes or {}))
        except Exception: pass
    span=Span(name=name,_otel_span=otel_span,_log=log,_start_ns=time.perf_counter())
    try:
        yield span
        try:
            from opentelemetry.trace import StatusCode
            otel_span.set_status(StatusCode.OK)
        except Exception: pass
        try: log.info(f"{name}.done",duration_ms=round(span.elapsed_ms,2))
        except Exception: pass
    except Exception as exc:
        span.set_error(exc); raise
    finally:
        if record_duration and _METRICS:
            try: _get_registry().request_duration().record(span.elapsed_ms,{"span":name})
            except Exception: pass
        if otel_ctx is not None:
            try: otel_ctx.__exit__(None,None,None)
            except Exception: pass
