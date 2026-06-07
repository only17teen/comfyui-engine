# ComfyUI Async Generation Engine v2.0
# Multi-stage Docker build for production deployment
# Optimized for: security, size, performance, observability

# ───────────────────────────────────────────────────────────────
# Stage 1: Builder
# ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY pyproject.toml ./
COPY environment.yml ./

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
    aiohttp>=3.9.0 \
    pyyaml>=6.0.1 \
    pydantic>=2.5.0 \
    pydantic-settings>=2.1.0 \
    prometheus-client>=0.19.0 \
    structlog>=23.0.0 \
    orjson>=3.9.0 \
    uvloop>=0.19.0

# Copy application code
COPY engine/ ./engine/
COPY main.py ./
COPY api_server.py ./
COPY dashboard.py ./
COPY benchmark.py ./
COPY config/ ./config/
COPY tests/ ./tests/
COPY systemd/ ./systemd/
COPY monitoring/ ./monitoring/
COPY docs/ ./docs/
COPY Makefile ./
COPY README.md ./

# Install engine package
RUN pip install --no-cache-dir -e .

# ───────────────────────────────────────────────────────────────
# Stage 2: Production
# ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS production

LABEL maintainer="Senior MLOps Engineer"
LABEL version="4.0.0"
LABEL description="ComfyUI Async Generation Engine v4.0 - Production"
LABEL org.opencontainers.image.source="https://github.com/user/comfyui-engine"
LABEL org.opencontainers.image.licenses="MIT"

# Security: Create non-root user with minimal privileges
RUN groupadd -r engine && useradd -r -g engine -s /bin/false engine \
    && mkdir -p /app /home/engine \
    && chown -R engine:engine /home/engine

# Install runtime dependencies only (curl for health checks, git for sync)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
WORKDIR /app
COPY --from=builder /build/engine/ ./engine/
COPY --from=builder /build/main.py ./
COPY --from=builder /build/api_server.py ./
COPY --from=builder /build/dashboard.py ./
COPY --from=builder /build/benchmark.py ./
COPY --from=builder /build/config/ ./config/
COPY --from=builder /build/systemd/ ./systemd/
COPY --from=builder /build/monitoring/ ./monitoring/
COPY --from=builder /build/docs/ ./docs/
COPY --from=builder /build/Makefile ./
COPY --from=builder /build/README.md ./
COPY --from=builder /build/pyproject.toml ./

# Create directories with proper permissions
RUN mkdir -p /app/output_models /app/logs /app/sessions /app/checkpoints \
    /app/workflows /app/profiles /app/dead_letter_queue \
    && chown -R engine:engine /app

# Security: Switch to non-root user
USER engine

# Health checks (liveness + readiness)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:8000/live || exit 1

# Expose ports
EXPOSE 8000  # API server
EXPOSE 9090  # Prometheus metrics

# Environment variables with sensible defaults
ENV COMFYUI_URL=http://host.docker.internal:8188
ENV COMFYUI_MAX_CONCURRENT=3
ENV ENGINE_OUTPUT_DIR=/app/output_models
ENV ENGINE_LOG_LEVEL=INFO
ENV ENGINE_JSON_LOGGING=true
ENV ENGINE_METRICS_PORT=9090
ENV ENGINE_QUEUE_MAX_SIZE=100
ENV ENGINE_TIMEOUT=300
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Use uvloop for better async performance on Linux
ENV UVLOOP=1

# Default command: API server with health checks
ENTRYPOINT ["python", "-m", "api_server"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--metrics-port", "9090"]

# ───────────────────────────────────────────────────────────────
# Stage 3: Development
# ───────────────────────────────────────────────────────────────
FROM production AS development

USER root

# Install dev tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    htop \
    jq \
    strace \
    tcpdump \
    && rm -rf /var/lib/apt/lists/*

# Install dev Python packages
RUN pip install --no-cache-dir \
    pytest>=7.4.0 \
    pytest-asyncio>=0.21.0 \
    black>=23.0.0 \
    ruff>=0.1.0 \
    mypy>=1.7.0 \
    coverage>=7.3.0 \
    pre-commit>=3.5.0

USER engine

# Mount points for development
VOLUME ["/app/workflows", "/app/config", "/app/output_models", "/app/logs", "/app/profiles"]

# Default dev command: run tests
CMD ["--help"]

# ───────────────────────────────────────────────────────────────
# Stage 4: Minimal (distroless-inspired)
# ───────────────────────────────────────────────────────────────
FROM python:3.11-alpine AS minimal

LABEL maintainer="Senior MLOps Engineer"
LABEL version="4.0.0"
LABEL description="ComfyUI Async Generation Engine v4.0 - Minimal"

# Install minimal runtime dependencies
RUN apk add --no-cache curl ca-certificates git

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only essential application code
WORKDIR /app
COPY --from=builder /build/engine/ ./engine/
COPY --from=builder /build/main.py ./
COPY --from=builder /build/api_server.py ./
COPY --from=builder /build/config/ ./config/
COPY --from=builder /build/pyproject.toml ./

# Create directories
RUN mkdir -p /app/output_models /app/logs /app/sessions /app/checkpoints

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:8000/live || exit 1

EXPOSE 8000 9090

ENV COMFYUI_URL=http://host.docker.internal:8188
ENV ENGINE_OUTPUT_DIR=/app/output_models
ENV ENGINE_LOG_LEVEL=INFO
ENV ENGINE_JSON_LOGGING=true

ENTRYPOINT ["python", "-m", "api_server"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
