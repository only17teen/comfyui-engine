.PHONY: help install test lint format build docker-up docker-down clean docs benchmark

PYTHON := python3
PIP := pip3
PYTEST := pytest
BLACK := black
ISORT := isort
FLAKE8 := flake8
MYPY := mypy

help:
	@echo "ComfyUI Engine - Development Commands"
	@echo ""
	@echo "  make install       Install dependencies"
	@echo "  make install-dev   Install dev dependencies"
	@echo "  make test          Run all tests"
	@echo "  make test-fast     Run fast unit tests only"
	@echo "  make test-slow     Run slow integration tests"
	@echo "  make lint          Run all linters"
	@echo "  make format        Format code with black and isort"
	@echo "  make type-check    Run mypy type checking"
	@echo "  make build         Build Docker image"
	@echo "  make docker-up     Start local development stack"
	@echo "  make docker-down   Stop local development stack"
	@echo "  make docs          Build documentation"
	@echo "  make benchmark     Run performance benchmarks"
	@echo "  make clean         Clean build artifacts"
	@echo "  make ci            Run full CI pipeline locally"

install:
	$(PIP) install -r requirements.txt

install-dev:
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt
	$(PIP) install pre-commit
	pre-commit install

test:
	$(PYTEST) tests/ -v --tb=short --cov=engine --cov-report=term-missing --cov-report=html:htmlcov

test-fast:
	$(PYTEST) tests/ -v -m "not slow" --tb=short

test-slow:
	$(PYTEST) tests/ -v -m "slow" --tb=short

test-integration:
	$(PYTEST) tests/integration/ -v --tb=short

lint:
	$(FLAKE8) engine/ tests/ --max-line-length=100 --extend-ignore=E203,W503
	$(BLACK) --check engine/ tests/
	$(ISORT) --check-only engine/ tests/

format:
	$(BLACK) engine/ tests/
	$(ISORT) engine/ tests/

type-check:
	$(MYPY) engine/ --ignore-missing-imports --show-error-codes

build:
	docker build -t comfyui-engine:latest .

docker-up:
	docker-compose up -d --build

docker-down:
	docker-compose down -v

docker-logs:
	docker-compose logs -f comfyui-engine

docs:
	@echo "Building documentation..."
	python -m docs.generate

benchmark:
	$(PYTHON) benchmark.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true

ci: install-dev lint type-check test benchmark
	@echo "CI pipeline complete"

security-scan:
	@echo "Running security scans..."
	bandit -r engine/ -f json -o security-report.json || true
	safety check || true

k8s-deploy:
	kubectl apply -k k8s/base/
	kubectl apply -k k8s/overlays/production/

k8s-delete:
	kubectl delete -k k8s/overlays/production/
	kubectl delete -k k8s/base/

helm-install:
	helm install comfyui-engine ./helm/comfyui-engine --namespace comfyui --create-namespace

helm-upgrade:
	helm upgrade comfyui-engine ./helm/comfyui-engine --namespace comfyui

helm-uninstall:
	helm uninstall comfyui-engine --namespace comfyui

release-patch:
	bumpversion patch

release-minor:
	bumpversion minor

release-major:
	bumpversion major
