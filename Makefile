.PHONY: help install test test-unit test-integration benchmark lint format clean docker-build docker-run docker-compose-up docker-compose-down systemd-install run run-distributed run-ab-test docs

# Default target
help:
	@echo "ComfyUI Engine v2.0 - Makefile"
	@echo "================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install              Install dependencies and setup environment"
	@echo "  make systemd-install      Install systemd service (requires sudo)"
	@echo ""
	@echo "Testing:"
	@echo "  make test                 Run all tests"
	@echo "  make test-unit            Run unit tests only"
	@echo "  make test-integration     Run integration tests only"
	@echo "  make benchmark            Run performance benchmark suite"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint                 Run ruff linter"
	@echo "  make format               Run black formatter"
	@echo "  make type-check           Run mypy type checker"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build         Build Docker image"
	@echo "  make docker-run           Run Docker container"
	@echo "  make docker-compose-up    Start full stack (engine + monitoring)"
	@echo "  make docker-compose-down  Stop full stack"
	@echo ""
	@echo "Execution:"
	@echo "  make run                  Run basic batch generation"
	@echo "  make run-distributed      Run in distributed worker mode"
	@echo "  make run-ab-test          Run A/B testing framework"
	@echo "  make run-metrics          Start with metrics server"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean                Clean build artifacts and caches"
	@echo "  make docs                 Generate documentation"
	@echo ""

# ───────────────────────────────────────────────────────────────
# Setup
# ───────────────────────────────────────────────────────────────
install:
	@echo "Installing ComfyUI Engine v2.0..."
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e .
	@echo "Installation complete. Activate with: source .venv/bin/activate"

systemd-install:
	@echo "Installing systemd service..."
	sudo cp systemd/comfyui-engine@.service /etc/systemd/system/
	sudo systemctl daemon-reload
	@echo "Service installed. Start with: sudo systemctl start comfyui-engine@\$$USER"

# ───────────────────────────────────────────────────────────────
# Testing
# ───────────────────────────────────────────────────────────────
test: test-unit test-integration
	@echo "All tests complete"

test-unit:
	@echo "Running unit tests..."
	.venv/bin/pytest tests/test_engine.py -v --tb=short

test-integration:
	@echo "Running integration tests..."
	.venv/bin/pytest tests/test_integration.py -v --tb=short

test-coverage:
	@echo "Running tests with coverage..."
	.venv/bin/pytest tests/ --cov=engine --cov-report=term-missing --cov-report=html

benchmark:
	@echo "Running benchmark suite..."
	.venv/bin/python benchmark.py --iterations 1000

# ───────────────────────────────────────────────────────────────
# Code Quality
# ───────────────────────────────────────────────────────────────
lint:
	@echo "Running ruff linter..."
	.venv/bin/ruff check engine/ tests/ main.py benchmark.py

format:
	@echo "Running black formatter..."
	.venv/bin/black engine/ tests/ main.py benchmark.py

type-check:
	@echo "Running mypy type checker..."
	.venv/bin/mypy engine/ --ignore-missing-imports

lint-fix: format
	@echo "Auto-fixing lint issues..."
	.venv/bin/ruff check --fix engine/ tests/ main.py benchmark.py

# ───────────────────────────────────────────────────────────────
# Docker
# ───────────────────────────────────────────────────────────────
docker-build:
	@echo "Building Docker image..."
	docker build -t comfyui-engine:latest .

docker-run: docker-build
	@echo "Running Docker container..."
	docker run -it --rm \
		-v $$(pwd)/workflows:/app/workflows:ro \
		-v $$(pwd)/config:/app/config:ro \
		-v $$(pwd)/output_models:/app/output_models \
		-p 9090:9090 \
		comfyui-engine:latest \
		--batch 4 --workflow workflows/standard.json --metrics-port 9090

docker-compose-up:
	@echo "Starting full Docker stack..."
	docker-compose --profile full up -d

docker-compose-down:
	@echo "Stopping Docker stack..."
	docker-compose --profile full down

docker-compose-logs:
	@echo "Viewing Docker logs..."
	docker-compose logs -f engine

# ───────────────────────────────────────────────────────────────
# Execution
# ───────────────────────────────────────────────────────────────
run:
	@echo "Running basic batch generation..."
	.venv/bin/python -m main \
		--batch 8 \
		--workflow workflows/standard.json \
		--verbose

run-cinematic:
	@echo "Running cinematic template batch..."
	.venv/bin/python -m main \
		--batch 16 \
		--template cinematic \
		--workflow workflows/standard.json \
		--max-concurrent 4 \
		--verbose

run-distributed:
	@echo "Running in distributed worker mode..."
	.venv/bin/python -m main \
		--distributed \
		--redis-url redis://localhost:6379/0 \
		--workflow workflows/standard.json

run-ab-test:
	@echo "Running A/B testing framework..."
	.venv/bin/python -c "
import asyncio
from engine.config import ConfigLoader
from engine.ab_testing import ABTestRunner
from engine.main import UnifiedGenerationEngine

config = ConfigLoader.load()
engine = UnifiedGenerationEngine(config)
runner = ABTestRunner(engine)
asyncio.run(runner.run_test('templates', generations_per_variant=10))
"

run-metrics:
	@echo "Running with metrics server..."
	.venv/bin/python -m main \
		--batch 8 \
		--workflow workflows/standard.json \
		--metrics-port 9090 \
		--verbose

run-resume:
	@echo "Resuming previous session..."
	.venv/bin/python -m main \
		--batch 16 \
		--workflow workflows/standard.json \
		--resume-session $$(ls -t sessions/*.json 2>/dev/null | head -1 | sed 's/\.json//' | sed 's/sessions\///') \
		--verbose

# ───────────────────────────────────────────────────────────────
# Maintenance
# ───────────────────────────────────────────────────────────────
clean:
	@echo "Cleaning build artifacts..."
	rm -rf __pycache__ .pytest_cache .mypy_cache htmlcov
	rm -rf build/ dist/ *.egg-info
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	@echo "Clean complete"

clean-all: clean
	@echo "Removing all generated data..."
	rm -rf output_models/ logs/ sessions/ checkpoints/
	@echo "All data cleaned"

docs:
	@echo "Documentation available in:"
	@echo "  - README.md"
	@echo "  - docs/architecture_v2.md"
	@echo "  - engine/ module docstrings"

# ───────────────────────────────────────────────────────────────
# Development
# ───────────────────────────────────────────────────────────────
dev-setup: install
	@echo "Installing development dependencies..."
	.venv/bin/pip install pytest pytest-asyncio black ruff mypy coverage
	@echo "Development setup complete"

watch:
	@echo "Watching for changes..."
	@while true; do \
		inotifywait -e modify -r engine/ tests/ main.py 2>/dev/null || sleep 2; \
		clear; \
		make test-unit; \
	done

# ───────────────────────────────────────────────────────────────
# Utilities
# ───────────────────────────────────────────────────────────────
health-check:
	@echo "Checking ComfyUI health..."
	@curl -sf http://127.0.0.1:8188/system_stats && echo "ComfyUI is healthy" || echo "ComfyUI is not responding"

metrics:
	@echo "Fetching metrics..."
	@curl -sf http://localhost:9090/metrics | head -20

validate-workflow:
	@echo "Validating workflow..."
	.venv/bin/python -m main \
		--workflow workflows/standard.json \
		--validate-workflow

# ───────────────────────────────────────────────────────────────
# Git
# ───────────────────────────────────────────────────────────────
git-sync:
	@echo "Syncing to git..."
	.venv/bin/python -c "
import asyncio
from engine.git_sync import sync_to_git
asyncio.run(sync_to_git('.', 'Makefile sync: $(date)'))
"
