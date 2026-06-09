"""
ComfyUI Async Generation Engine v4.0 - Makefile
Complete automation for development, testing, and deployment.
"""

.PHONY: help install install-dev test test-unit test-integration lint format type-check benchmark docker-build docker-run docker-push clean docs profile

# Default target
help:
	@echo "ComfyUI Engine v4.0 - Available commands:"
	@echo ""
	@echo "  Development:"
	@echo "    make install       - Install production dependencies"
	@echo "    make install-dev   - Install development dependencies"
	@echo "    make install-conda - Install via conda environment"
	@echo ""
	@echo "  Testing:"
	@echo "    make test          - Run all tests"
	@echo "    make test-unit     - Run unit tests only"
	@echo "    make test-integration - Run integration tests"
	@echo "    make test-coverage - Run tests with coverage report"
	@echo ""
	@echo "  Code Quality:"
	@echo "    make lint          - Run ruff linter"
	@echo "    make format        - Run ruff formatter"
	@echo "    make type-check    - Run mypy type checker"
	@echo "    make pre-commit    - Run pre-commit hooks"
	@echo ""
	@echo "  Performance:"
	@echo "    make benchmark     - Run performance benchmarks"
	@echo "    make profile       - Run profiler on sample workload"
	@echo ""
	@echo "  Docker:"
	@echo "    make docker-build  - Build Docker image"
	@echo "    make docker-run    - Run Docker container"
	@echo "    make docker-push   - Push to registry"
	@echo "    make docker-dev    - Run development container"
	@echo ""
	@echo "  Documentation:"
	@echo "    make docs          - Generate API documentation"
	@echo "    make docs-serve    - Serve documentation locally"
	@echo ""
	@echo "  Operations:"
	@echo "    make systemd-install - Install systemd service"
	@echo "    make clean         - Clean generated files"
	@echo "    make purge         - Deep clean including Docker"

# ───────────────────────────────────────────────────────────────
# Installation
# ───────────────────────────────────────────────────────────────
install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"
	pre-commit install

install-conda:
	conda env create -f environment.yml
	conda activate comfyui-engine

# ───────────────────────────────────────────────────────────────
# Testing
# ───────────────────────────────────────────────────────────────
test:
	pytest tests/ -v --tb=short

test-unit:
	pytest tests/test_engine.py -v --tb=short

test-integration:
	pytest tests/test_integration.py -v --tb=short

test-coverage:
	pytest tests/ -v --tb=short --cov=engine --cov-report=html --cov-report=term

# ───────────────────────────────────────────────────────────────
# Code Quality
# ───────────────────────────────────────────────────────────────
lint:
	ruff check engine/ tests/ main.py api_server.py dashboard.py benchmark.py

format:
	ruff format engine/ tests/ main.py api_server.py dashboard.py benchmark.py

type-check:
	mypy engine/ --ignore-missing-imports --show-error-codes

pre-commit:
	pre-commit run --all-files

# ───────────────────────────────────────────────────────────────
# Performance
# ───────────────────────────────────────────────────────────────
benchmark:
	python benchmark.py --iterations 1000 --output benchmark_results.json

profile:
	python -m cProfile -o profile.stats main.py --batch 10 --workflow workflows/standard.json
	python -c "import pstats; p = pstats.Stats('profile.stats'); p.sort_stats('cumulative'); p.print_stats(20)"

# ───────────────────────────────────────────────────────────────
# Docker
# ───────────────────────────────────────────────────────────────
DOCKER_IMAGE ?= comfyui-engine
DOCKER_TAG ?= latest
DOCKER_REGISTRY ?= ghcr.io/user

docker-build:
	docker build --target production -t $(DOCKER_IMAGE):$(DOCKER_TAG) .

docker-build-dev:
	docker build --target development -t $(DOCKER_IMAGE):dev .

docker-build-minimal:
	docker build --target minimal -t $(DOCKER_IMAGE):minimal .

docker-run:
	docker run -d \
		--name comfyui-engine \
		-p 8000:8000 \
		-p 9090:9090 \
		-v $(PWD)/output_models:/app/output_models \
		-v $(PWD)/config:/app/config \
		-v $(PWD)/logs:/app/logs \
		--env COMFYUI_URL=http://host.docker.internal:8188 \
		$(DOCKER_IMAGE):$(DOCKER_TAG)

docker-run-dev:
	docker run -it --rm \
		--name comfyui-engine-dev \
		-p 8000:8000 \
		-p 9090:9090 \
		-v $(PWD):/app \
		$(DOCKER_IMAGE):dev bash

docker-push:
	docker tag $(DOCKER_IMAGE):$(DOCKER_TAG) $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG)
	docker push $(DOCKER_REGISTRY)/$(DOCKER_IMAGE):$(DOCKER_TAG)

docker-stop:
	docker stop comfyui-engine || true
	docker rm comfyui-engine || true

# ───────────────────────────────────────────────────────────────
# Documentation
# ───────────────────────────────────────────────────────────────
docs:
	python -c "from api_server import create_api_server; from engine.api_docs import APIDocGenerator; import asyncio; s = asyncio.run(create_api_server()); g = APIDocGenerator(); g.generate_all(s.app)"

docs-serve:
	cd docs && mkdocs serve

docs-build:
	cd docs && mkdocs build

# ───────────────────────────────────────────────────────────────
# Enhanced Features
# ───────────────────────────────────────────────────────────────
gc-tuner:
	@echo "Testing GC Tuner configuration..."
	python -c "
import asyncio
from engine import UnifiedGenerationEngine
from engine.config import ConfigLoader
async def test():
    config = ConfigLoader.load()
    engine = UnifiedGenerationEngine(config)
    # Test GC tuner configuration
    gc_config = {
        'freeze_on_boot': True,
        'freeze_duration': 300.0,
        'background_interval': 60.0,
        'generation_thresholds': (700, 10, 10),
        'max_latency_ms': 50.0,
        'emergency_threshold': 0.85
    }
    await engine.configure_gc_tuner(gc_config)
    stats = await engine.get_gc_stats()
    print('GC Tuner configured successfully')
    print('GC Stats:', stats)
asyncio.run(test())
	"

retry-policy:
	@echo "Testing Retry Policy configuration..."
	python -c "
import asyncio
from engine import UnifiedGenerationEngine
from engine.config import ConfigLoader
async def test():
    config = ConfigLoader.load()
    engine = UnifiedGenerationEngine(config)
    # Test retry policy configuration
    policy = {
        'max_retries': 5,
        'base_delay': 0.5,
        'max_delay': 30.0,
        'strategy': 'FULL_JITTER',
        'jitter_factor': 0.2
    }
    await engine.configure_retry_policy(policy)
    print('Retry Policy configured successfully')
asyncio.run(test())
	"

tracing:
	@echo "Testing OpenTelemetry Tracing configuration..."
	python -c "
import asyncio
from engine import UnifiedGenerationEngine
from engine.config import ConfigLoader
async def test():
    config = ConfigLoader.load()
    engine = UnifiedGenerationEngine(config)
    # Test tracing configuration
    tracing_config = {
        'service_name': 'comfyui-engine-enhanced',
        'service_version': '5.0.0',
        'environment': 'development',
        'sampler_ratio': 0.2,
        'enable_debug': True
    }
    await engine.initialize_tracing(tracing_config)
    context = await engine.get_trace_context()
    print('Tracing initialized successfully')
    print('Trace Context:', context)
asyncio.run(test())
	"

gpu-optimization:
	@echo "Testing GPU Optimization configuration..."
	python -c "
import asyncio
from engine import UnifiedGenerationEngine
from engine.config import ConfigLoader
async def test():
    config = ConfigLoader.load()
    engine = UnifiedGenerationEngine(config)
    # Test GPU optimization configuration
    gpu_config = {
        'memory_fraction': 0.85,
        'enable_memory_pool': True,
        'enable_stream_prioritization': True,
        'stream_priority_high': 1,
        'stream_priority_low': 0,
        'enable_tensor_core': True,
        'enable_cuda_graphs': False,
        'max_batch_size': 16
    }
    await engine.configure_gpu_optimization(gpu_config)
    stats = await engine.get_gpu_stats()
    print('GPU Optimization configured successfully')
    print('GPU Stats:', stats)
asyncio.run(test())
	"

batching:
	@echo "Testing Advanced Batching configuration..."
	python -c "
import asyncio
from engine import UnifiedGenerationEngine
from engine.config import ConfigLoader
async def test():
    config = ConfigLoader.load()
    engine = UnifiedGenerationEngine(config)
    # Test enabling advanced batching
    await engine.enable_advanced_batching(True)
    stats = await engine.get_batch_stats()
    print('Advanced Batching enabled successfully')
    print('Batch Stats:', stats)
    # Test disabling
    await engine.enable_advanced_batching(False)
    print('Advanced Batching disabled')
asyncio.run(test())

# ───────────────────────────────────────────────────────────────
# Systemd
# ───────────────────────────────────────────────────────────────
systemd-install:
	cd systemd && sudo ./install.sh

systemd-start:
	sudo systemctl start comfyui-engine@production

systemd-stop:
	sudo systemctl stop comfyui-engine@production

systemd-status:
	sudo systemctl status comfyui-engine@production

systemd-logs:
	sudo journalctl -u comfyui-engine@production -f

# ───────────────────────────────────────────────────────────────
# Monitoring
# ───────────────────────────────────────────────────────────────
monitoring-up:
	docker-compose -f docker-compose.yml up -d prometheus grafana loki promtail

monitoring-down:
	docker-compose -f docker-compose.yml down

monitoring-logs:
	docker-compose -f docker-compose.yml logs -f

# ───────────────────────────────────────────────────────────────
# Cleanup
# ───────────────────────────────────────────────────────────────
clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	rm -rf htmlcov .coverage coverage.xml
	rm -rf profiles/*.prof profiles/*.json
	rm -rf dead_letter_queue/*.json checkpoints/*.json sessions/*.json
	rm -f benchmark_results.json profile.stats
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

purge: clean
	docker system prune -f
	docker volume prune -f

# ───────────────────────────────────────────────────────────────
# Development Utilities
# ───────────────────────────────────────────────────────────────
run:
	python main.py --batch 4 --workflow workflows/standard.json

run-api:
	python -m api_server --host 0.0.0.0 --port 8000

run-dashboard:
	python dashboard.py

run-wizard:
	python setup_wizard.py

redis-up:
	docker run -d --name comfyui-redis -p 6379:6379 redis:7-alpine

redis-down:
	docker stop comfyui-redis || true
	docker rm comfyui-redis || true

# ───────────────────────────────────────────────────────────────
# Release
# ───────────────────────────────────────────────────────────────
version:
	python -c "import engine; print(engine.__version__)"

tag-release:
	git tag -a v$(shell python -c "import engine; print(engine.__version__)") -m "Release v$(shell python -c "import engine; print(engine.__version__)")"
	git push origin --tags
