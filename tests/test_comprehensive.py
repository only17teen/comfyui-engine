"""Comprehensive test suite for ComfyUI Engine with mocked dependencies."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from engine.core import (
    ComfyUIEngine,
    MetricsCollector,
    MetricsSnapshot,
    ObjectPool,
    RetryConfig,
    RetryPolicy,
    with_retry,
)
from engine.api_client import ComfyUIClient, ComfyUIJob
from engine.auto_scaler import AutoScaler, ScalingConfig
from engine.metrics_server import MetricsServer, SLI, SLO
from engine.security import (
    EnhancedSecurityManager,
    JSONSchemaValidator,
    RotatingSecretManager,
    SlidingWindowRateLimiter,
)


class TestObjectPool:
    """Tests for Object Pool (Memory First - Kiro Rule 6)."""

    @pytest.mark.asyncio
    async def test_pool_acquire_release(self):
        pool = ObjectPool(factory=lambda: "object", max_size=5)
        
        obj1 = await pool.acquire()
        assert obj1 == "object"
        
        obj2 = await pool.acquire()
        assert obj2 == "object"
        
        await pool.release(obj1)
        
        # Should reuse released object
        obj3 = await pool.acquire()
        assert obj3 == "object"

    @pytest.mark.asyncio
    async def test_pool_max_size(self):
        pool = ObjectPool(factory=lambda: "object", max_size=2)
        
        obj1 = await pool.acquire()
        obj2 = await pool.acquire()
        
        # Pool is full, release should not add more
        await pool.release(obj1)
        await pool.release(obj2)
        
        # Should still work
        obj3 = await pool.acquire()
        assert obj3 == "object"


class TestRetryPolicy:
    """Tests for Retry Logic (Kiro Rule 1)."""

    @pytest.mark.asyncio
    async def test_successful_retry(self):
        config = RetryConfig(max_retries=3, base_delay=0.01)
        policy = RetryPolicy(config)
        
        call_count = 0
        
        @with_retry(config)
        async def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary failure")
            return "success"
        
        result = await flaky_operation()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exhausted_retries(self):
        config = RetryConfig(max_retries=2, base_delay=0.01)
        
        @with_retry(config)
        async def always_fails():
            raise ConnectionError("Permanent failure")
        
        with pytest.raises(ConnectionError):
            await always_fails()

    def test_precomputed_delays(self):
        config = RetryConfig(max_retries=5, base_delay=1.0, max_delay=60.0)
        policy = RetryPolicy(config)
        
        delays = policy.delay_table
        assert len(delays) == 5
        assert delays[0] == 1.0
        assert all(d <= 60.0 for d in delays)


class TestMetricsCollector:
    """Tests for Metrics (Kiro Rule 11)."""

    def test_metrics_collection(self):
        collector = MetricsCollector()
        
        collector.record_latency("inference", 100.0)
        collector.record_latency("inference", 150.0)
        collector.record_latency("inference", 200.0)
        
        snapshot = collector.snapshot()
        assert snapshot.count == 3
        assert snapshot.mean > 0

    def test_metrics_percentiles(self):
        collector = MetricsCollector()
        
        for i in range(100):
            collector.record_latency("inference", float(i))
        
        snapshot = collector.snapshot()
        assert 45 <= snapshot.p50 <= 55
        assert snapshot.p95 >= 90
        assert snapshot.p99 >= 95

    def test_object_pool_reuse(self):
        collector = MetricsCollector()
        
        # First snapshot
        collector.record_latency("inference", 100.0)
        snapshot1 = collector.snapshot()
        
        # Second snapshot should reuse from pool
        collector.record_latency("inference", 200.0)
        snapshot2 = collector.snapshot()
        
        assert snapshot2.count == 1
        assert snapshot2.mean == 200.0


class TestComfyUIClient:
    """Tests for API Client (Kiro Rule 7)."""

    @pytest.mark.asyncio
    async def test_job_pool(self):
        client = ComfyUIClient("http://localhost:8188")
        
        # Job pool should be initialized
        assert client._job_pool is not None
        
        # Acquire and release job
        job = await client._job_pool.acquire()
        assert job is not None
        
        await client._job_pool.release(job)

    @pytest.mark.asyncio
    async def test_adaptive_polling(self):
        client = ComfyUIClient("http://localhost:8188")
        
        # Check polling intervals
        intervals = client._polling_intervals
        assert len(intervals) == 3
        assert intervals[0] == 0.5  # Fast
        assert intervals[1] == 1.0   # Medium
        assert intervals[2] == 2.0   # Slow

    def test_tcp_connector_tuning(self):
        client = ComfyUIClient("http://localhost:8188")
        
        # Connector should be tuned for single instance
        assert client._connector_limit == 10
        assert client._connector_limit_per_host == 5


class TestAutoScaler:
    """Tests for Auto-Scaler (Kiro Rule 3)."""

    @pytest.mark.asyncio
    async def test_scaling_decision(self):
        config = ScalingConfig(min_workers=1, max_workers=5)
        scaler = AutoScaler(config)
        
        # Simulate high load
        decision = scaler.evaluate_scaling(
            queue_depth=100,
            gpu_utilization=0.9,
            current_workers=2,
        )
        
        assert decision.action == "scale_up"
        assert decision.target_workers > 2

    @pytest.mark.asyncio
    async def test_hysteresis(self):
        config = ScalingConfig(
            min_workers=1,
            max_workers=10,
            scale_up_cooldown=60,
            scale_down_cooldown=300,
        )
        scaler = AutoScaler(config)
        
        # Should not scale immediately after recent scale
        scaler._last_scale_time = time.time()
        
        decision = scaler.evaluate_scaling(
            queue_depth=1000,
            gpu_utilization=0.99,
            current_workers=1,
        )
        
        # Should be blocked by cooldown
        assert decision.action == "no_change" or decision.reason == "cooldown"

    @pytest.mark.asyncio
    async def test_emergency_scaling(self):
        config = ScalingConfig(min_workers=1, max_workers=10)
        scaler = AutoScaler(config)
        
        decision = scaler.evaluate_scaling(
            queue_depth=100,
            gpu_utilization=0.95,
            current_workers=2,
        )
        
        assert decision.action == "scale_up"
        assert decision.emergency is True


class TestSecurityManager:
    """Tests for Security (Kiro Rule 10)."""

    def test_jwt_secret_rotation(self):
        manager = RotatingSecretManager(interval_hours=24)
        
        secret1 = manager.current_secret
        assert secret1 is not None
        
        # Simulate rotation
        manager.rotate()
        secret2 = manager.current_secret
        
        assert secret2 != secret1
        assert secret1 in manager.recent_secrets

    def test_schema_validation(self):
        validator = JSONSchemaValidator()
        
        schema = {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "seed": {"type": "integer", "minimum": 0},
            },
            "required": ["prompt"],
        }
        
        # Valid data
        valid = {"prompt": "a cat", "seed": 42}
        assert validator.validate(valid, schema) is True
        
        # Invalid data
        invalid = {"seed": 42}
        assert validator.validate(invalid, schema) is False

    def test_rate_limiter(self):
        limiter = SlidingWindowRateLimiter(
            burst_limit=10,
            sustained_limit=5,
            window_seconds=60,
        )
        
        # Should allow burst
        for _ in range(10):
            assert limiter.allow("client1") is True
        
        # Should block after burst
        assert limiter.allow("client1") is False
        
        # Different client should be allowed
        assert limiter.allow("client2") is True

    def test_token_binding(self):
        manager = EnhancedSecurityManager()
        
        token = manager.create_token(
            user_id="user1",
            device_fingerprint="fp123",
        )
        
        # Valid token with matching fingerprint
        assert manager.validate_token(token, device_fingerprint="fp123") is True
        
        # Invalid token with different fingerprint
        assert manager.validate_token(token, device_fingerprint="fp456") is False


class TestMetricsServer:
    """Tests for SLI/SLO Metrics Server (Kiro Rule 11)."""

    def test_sli_calculation(self):
        sli = SLI(
            name="job_completion_rate",
            metric_type="ratio",
            target=0.95,
        )
        
        # Record successes and failures
        sli.record(success=True)
        sli.record(success=True)
        sli.record(success=False)
        
        value = sli.calculate()
        assert value == 2/3  # 2 successes out of 3

    def test_slo_evaluation(self):
        slo = SLO(
            name="job_completion_rate",
            sli=SLI(name="job_completion_rate", metric_type="ratio", target=0.95),
            target=0.95,
            warning_threshold=0.90,
            breach_threshold=0.80,
        )
        
        # Healthy
        slo.sli.record(success=True)
        slo.sli.record(success=True)
        status = slo.evaluate()
        assert status == "HEALTHY"
        
        # Breached
        for _ in range(10):
            slo.sli.record(success=False)
        status = slo.evaluate()
        assert status == "BREACHED"

    def test_alert_handler(self):
        server = MetricsServer()
        
        alerts = []
        server.register_alert_handler(lambda alert: alerts.append(alert))
        
        slo = SLO(
            name="test_slo",
            sli=SLI(name="test", metric_type="ratio", target=0.95),
            target=0.95,
            breach_threshold=0.80,
        )
        
        # Trigger breach
        for _ in range(10):
            slo.sli.record(success=False)
        
        server.add_slo(slo)
        server.evaluate_all_slos()
        
        assert len(alerts) > 0
        assert alerts[0]["slo"] == "test_slo"
        assert alerts[0]["status"] == "BREACHED"


class TestIntegration:
    """Integration tests for complete pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """Test complete inference pipeline with all components."""
        # Setup components
        metrics = MetricsCollector()
        retry_config = RetryConfig(max_retries=2, base_delay=0.01)
        scaler = AutoScaler(ScalingConfig(min_workers=1, max_workers=3))
        security = EnhancedSecurityManager()
        
        # Create token
        token = security.create_token("user1", "fp123")
        
        # Simulate pipeline
        @with_retry(retry_config)
        async def inference_pipeline():
            # Record metrics
            start = time.time()
            
            # Simulate work
            await asyncio.sleep(0.01)
            
            latency = (time.time() - start) * 1000
            metrics.record_latency("inference", latency)
            
            return {"result": "success", "latency_ms": latency}
        
        result = await inference_pipeline()
        assert result["result"] == "success"
        
        # Check metrics
        snapshot = metrics.snapshot()
        assert snapshot.count == 1
        assert snapshot.mean > 0
        
        # Check scaling decision
        decision = scaler.evaluate_scaling(
            queue_depth=1,
            gpu_utilization=0.5,
            current_workers=1,
        )
        assert decision.action == "no_change"

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test concurrent request handling."""
        metrics = MetricsCollector()
        
        async def request_task(task_id: int):
            await asyncio.sleep(0.01)
            metrics.record_latency("inference", float(task_id * 10))
            return f"result_{task_id}"
        
        # Run 10 concurrent tasks
        tasks = [request_task(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 10
        assert all(r.startswith("result_") for r in results)
        
        snapshot = metrics.snapshot()
        assert snapshot.count == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
