"""
ComfyUI Async Generation Engine v2.0 - Test Suite
pytest-compatible tests for core components.
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import yaml

from engine.config import ConfigLoader, EngineConfig, LoRAModelConfig, SamplingConfig
from engine.core import (
    CircuitBreaker,
    CircuitBreakerConfig,
    MetricsCollector,
    RetryConfig,
    with_retry,
    JobQueue,
    QueueFullError,
)
from engine.prompt_manager import PromptManager, GenerationConfig, SeedStrategy, PromptTemplate


# ───────────────────────────────────────────────────────────────
# Configuration Tests
# ───────────────────────────────────────────────────────────────
class TestConfigLoader:
    """Test configuration loading and validation."""

    def test_load_default_config(self):
        """Test loading with defaults when no file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.yaml"
            config = ConfigLoader.load(config_path)

        assert isinstance(config, EngineConfig)
        assert config.base_url == "http://127.0.0.1:8188"
        assert config.max_concurrent == 3

    def test_load_from_yaml(self):
        """Test loading from YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.yaml"
            data = {
                "prompts": {
                    "trigger_words": ["test1", "test2"],
                    "negative_prompt": "bad quality",
                },
                "lora": {
                    "models": [
                        {"name": "test_lora", "path": "test.pt", "weight_range": [0.5, 0.8]}
                    ],
                    "batch_size": 2,
                },
            }
            config_path.write_text(yaml.dump(data), encoding="utf-8")
            config = ConfigLoader.load(config_path)

        assert config.lora.batch_size == 2
        assert len(config.lora.models) == 1
        assert config.lora.models[0].name == "test_lora"

    def test_env_override(self, monkeypatch):
        """Test environment variable override."""
        monkeypatch.setenv("COMFYUI_URL", "http://192.168.1.100:8188")
        monkeypatch.setenv("COMFYUI_MAX_CONCURRENT", "8")

        config = ConfigLoader.load()
        assert config.base_url == "http://192.168.1.100:8188"
        assert config.max_concurrent == 8

    def test_invalid_url_raises(self):
        """Test invalid URL validation."""
        with pytest.raises(ValueError, match="Invalid URL"):
            EngineConfig(base_url="invalid-url")

    def test_invalid_lora_weight_range(self):
        """Test LoRA weight range validation."""
        with pytest.raises(ValueError, match="Invalid weight range"):
            LoRAModelConfig(name="test", path="test.pt", weight_range=(0.5, 3.0))


# ───────────────────────────────────────────────────────────────
# Circuit Breaker Tests
# ───────────────────────────────────────────────────────────────
class TestCircuitBreaker:
    """Test circuit breaker resilience pattern."""

    @pytest.fixture
    def metrics(self):
        return MetricsCollector()

    @pytest.fixture
    def cb(self, metrics):
        return CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(failure_threshold=3, recovery_timeout=0.1),
            metrics=metrics,
        )

    @pytest.mark.asyncio
    async def test_successful_call(self, cb):
        """Test normal operation."""
        async def success():
            return "ok"

        result = await cb.call(success)
        assert result == "ok"
        assert cb.state.name == "CLOSED"

    @pytest.mark.asyncio
    async def test_opens_after_failures(self, cb):
        """Test circuit opens after threshold failures."""
        async def fail():
            raise RuntimeError("fail")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state.name == "OPEN"

    @pytest.mark.asyncio
    async def test_rejects_when_open(self, cb):
        """Test requests rejected when circuit is open."""
        async def fail():
            raise RuntimeError("fail")

        # Trigger open
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        # Should reject immediately
        with pytest.raises(Exception, match="OPEN"):
            await cb.call(lambda: asyncio.sleep(0))

    @pytest.mark.asyncio
    async def test_half_open_recovery(self, cb):
        """Test recovery through half-open state."""
        async def fail():
            raise RuntimeError("fail")

        async def success():
            return "ok"

        # Trigger open
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state.name == "OPEN"

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # First call in half-open should succeed and close circuit
        result = await cb.call(success)
        assert result == "ok"
        # With success_threshold=2, need 2 successes to close
        assert cb.state.name == "HALF_OPEN"
        
        # Second success should close it
        result = await cb.call(success)
        assert result == "ok"
        assert cb.state.name == "CLOSED"


# ───────────────────────────────────────────────────────────────
# Retry Tests
# ───────────────────────────────────────────────────────────────
class TestRetry:
    """Test retry with exponential backoff."""

    @pytest.fixture
    def metrics(self):
        return MetricsCollector()

    @pytest.mark.asyncio
    async def test_success_no_retry(self, metrics):
        """Test successful call without retries."""
        async def success():
            return "ok"

        config = RetryConfig(max_retries=2, base_delay=0.01)
        result = await with_retry(success, config, metrics)
        assert result == "ok"

        snapshot = await metrics.snapshot()
        assert snapshot.retries_total == 0

    @pytest.mark.asyncio
    async def test_retry_then_success(self, metrics):
        """Test retry on failure then success."""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise aiohttp.ClientError("temporary fail")
            return "ok"

        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = await with_retry(flaky, config, metrics)
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, metrics):
        """Test failure after all retries exhausted."""
        async def always_fail():
            raise aiohttp.ClientError("permanent fail")

        config = RetryConfig(max_retries=2, base_delay=0.01)
        with pytest.raises(aiohttp.ClientError):
            await with_retry(always_fail, config, metrics)


# ───────────────────────────────────────────────────────────────
# Queue Tests
# ───────────────────────────────────────────────────────────────
class TestJobQueue:
    """Test priority queue with backpressure."""

    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self):
        """Test basic enqueue/dequeue."""
        queue = JobQueue(max_size=10)

        future = await queue.enqueue(
            payload={"test": "data"},
            meta={"seed": 123},
            priority=1,
        )

        item = await queue.dequeue()
        assert item.payload == {"test": "data"}
        assert item.priority == 1

    @pytest.mark.asyncio
    async def test_backpressure(self):
        """Test queue rejects when full."""
        queue = JobQueue(max_size=1)

        await queue.enqueue(payload={"a": 1}, meta={})

        with pytest.raises(QueueFullError):
            await queue.enqueue(payload={"b": 2}, meta={}, timeout=0.1)

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Test priority ordering (lower = higher priority)."""
        queue = JobQueue(max_size=10)

        await queue.enqueue(payload={"low": 1}, meta={}, priority=3)
        await queue.enqueue(payload={"high": 1}, meta={}, priority=1)
        await queue.enqueue(payload={"mid": 1}, meta={}, priority=2)

        items = []
        for _ in range(3):
            items.append(await queue.dequeue())

        priorities = [i.priority for i in items]
        assert priorities == [1, 2, 3]


# ───────────────────────────────────────────────────────────────
# Prompt Manager Tests
# ───────────────────────────────────────────────────────────────
class TestPromptManager:
    """Test prompt generation and randomization."""

    @pytest.fixture
    def config(self):
        return EngineConfig(
            prompts={
                "trigger_words": ["masterpiece", "best quality", "1girl"],
                "clothing": {
                    "tops": ["shirt", "jacket"],
                    "bottoms": ["jeans", "skirt"],
                    "accessories": ["watch"],
                    "full_body": ["dress"],
                },
                "poses": ["standing", "sitting"],
                "locations": ["city", "forest"],
                "expressions": ["smile", "serious"],
                "lighting": ["daylight", "night"],
                "negative_prompt": "bad quality",
            },
            lora={
                "models": [
                    LoRAModelConfig(name="test", path="test.pt", weight_range=(0.5, 0.8))
                ],
                "sampling": SamplingConfig(),
                "resolutions": [(512, 768)],
                "batch_size": 1,
                "max_concurrent": 2,
            },
        )

    @pytest.fixture
    def manager(self, config):
        return PromptManager(config)

    def test_seed_strategies(self):
        """Test different seed generation strategies."""
        s1 = SeedStrategy.random()
        s2 = SeedStrategy.random()
        assert s1 != s2  # Highly likely

        s3 = SeedStrategy.time_based()
        time.sleep(0.01)  # Ensure time difference
        s4 = SeedStrategy.time_based()
        assert s3 != s4  # Time-based should differ

        s5 = SeedStrategy.sequential(1000)
        s6 = SeedStrategy.sequential()
        assert s6 == 1002  # Counter increments by 1, then returns incremented value

    def test_template_rendering(self):
        """Test prompt template rendering."""
        template = PromptTemplate("standard")
        result = template.render(
            triggers="masterpiece, best quality",
            clothing="shirt, jeans",
            pose="standing",
            location="city",
            expression="smile",
            lighting="daylight",
        )
        assert "masterpiece" in result
        assert "standing" in result
        assert "city" in result

    def test_generate_config(self, manager):
        """Test configuration generation."""
        config = manager.generate_config(seed=12345, num_lora=1)

        assert isinstance(config, GenerationConfig)
        assert config.seed == 12345
        assert config.positive_prompt
        assert config.negative_prompt == "bad quality"
        assert config.width == 512
        assert config.height == 768
        assert len(config.lora_stack) == 1
        assert 0.5 <= config.lora_stack[0].weight <= 0.8

    def test_prompt_deduplication(self, manager):
        """Test prompt deduplication with history."""
        # Fill history
        for _ in range(10):
            manager.generate_config()

        assert len(manager.prompt_history) > 0
        assert len(manager.prompt_history) <= manager.max_history

    def test_lora_stack_weights(self, manager):
        """Test LoRA weight generation within bounds."""
        stack = manager.build_lora_stack(num_lora=1)
        assert len(stack) == 1
        assert 0.5 <= stack[0].weight <= 0.8
        assert stack[0].weight_clip <= stack[0].weight

    def test_batch_generation(self, manager):
        """Test batch generation."""
        configs = manager.generate_batch(count=5, num_lora=1)
        assert len(configs) == 5
        seeds = [c.seed for c in configs]
        assert len(set(seeds)) == 5  # All unique with random strategy

    def test_to_comfy_payload(self, manager):
        """Test workflow payload injection."""
        config = manager.generate_config(seed=42)
        workflow = {
            "1": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20}},
            "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        }

        payload = manager.to_comfy_payload(config, workflow)

        assert payload["1"]["inputs"]["seed"] == 42
        assert payload["2"]["inputs"]["width"] == 512
        assert payload["6"]["inputs"]["text"]  # Should have positive prompt
        assert payload["7"]["inputs"]["text"] == "bad quality"


# ───────────────────────────────────────────────────────────────
# Metrics Tests
# ───────────────────────────────────────────────────────────────
class TestMetrics:
    """Test metrics collection."""

    @pytest.mark.asyncio
    async def test_counter_increment(self):
        """Test counter increment."""
        metrics = MetricsCollector()
        await metrics.inc("test_counter", 5)
        await metrics.inc("test_counter", 3)

        snapshot = await metrics.snapshot()
        assert snapshot.jobs_submitted == 0  # Not the same counter

        report = await metrics.report()
        assert report["counters"]["test_counter"] == 8

    @pytest.mark.asyncio
    async def test_histogram_percentiles(self):
        """Test histogram percentile calculation."""
        metrics = MetricsCollector()

        for i in range(100):
            await metrics.observe("latency", float(i))

        report = await metrics.report()
        hist = report["histograms"]["latency"]
        assert hist["count"] == 100
        assert hist["min"] == 0.0
        assert hist["max"] == 99.0
        assert hist["p50"] == 50.0
        assert hist["p95"] == 95.0


# ───────────────────────────────────────────────────────────────
# Integration Tests
# ───────────────────────────────────────────────────────────────
class TestIntegration:
    """Integration tests for full pipeline."""

    @pytest.mark.asyncio
    async def test_end_to_end_mock(self):
        """Test full pipeline with mocked API."""
        from engine.api_client import ComfyUIAsyncClient

        config = EngineConfig()
        manager = PromptManager(config)
        client = ComfyUIAsyncClient(
            base_url="http://test",
            max_concurrent=1,
            metrics=MetricsCollector(),
        )

        # Mock the session
        client._session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"prompt_id": "test-123"})
        client._session.post = AsyncMock(return_value=mock_response)

        # Generate and submit
        gen_config = manager.generate_config()
        workflow = {"1": {"class_type": "KSampler", "inputs": {}}}
        payload = manager.to_comfy_payload(gen_config, workflow)

        assert payload["1"]["inputs"]["seed"] == gen_config.seed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
