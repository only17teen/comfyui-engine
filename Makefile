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
