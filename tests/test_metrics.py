from __future__ import annotations
import pytest
from engine.metrics import MetricsRegistry, get_registry

@pytest.fixture
def reg() -> MetricsRegistry: return MetricsRegistry(meter_name="test")

def test_counters_callable(reg: MetricsRegistry) -> None:
    assert hasattr(reg.request_counter(), "add")
    assert hasattr(reg.error_counter(), "add")

def test_histograms_callable(reg: MetricsRegistry) -> None:
    assert hasattr(reg.request_duration(), "record")
    assert hasattr(reg.queue_processing_time(), "record")

def test_add_does_not_raise(reg: MetricsRegistry) -> None:
    reg.request_counter().add(1, {"method": "GET"})
    reg.request_duration().record(42.5, {"path": "/api"})

def test_gauge_snapshot(reg: MetricsRegistry) -> None:
    reg.set_queue_depth(7.0); reg.set_active_connections(3.0)
    snap = reg.get_gauge_snapshot(); assert 7.0 in snap.values()

def test_circuit_breaker_state(reg: MetricsRegistry) -> None:
    reg.set_circuit_breaker_state("payments", is_open=True)
    assert reg.get_gauge_snapshot().get("circuit_breaker:payments") == 1.0
    reg.set_circuit_breaker_state("payments", is_open=False)
    assert reg.get_gauge_snapshot().get("circuit_breaker:payments") == 0.0

def test_singleton() -> None:
    assert get_registry() is get_registry()
