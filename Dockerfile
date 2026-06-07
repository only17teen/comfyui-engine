# ComfyUI Async Generation Engine v2.0
# Multi-stage Docker build for production deployment

# ───────────────────────────────────────────────────────────────
# Stage 1: Builder
# ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY pyproject.toml ./
COPY engine/ ./engine/
COPY main.py ./
COPY config/ ./config/

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir aiohttp pyyaml pydantic

# Install engine package
RUN pip install --no-cache-dir -e .

# ───────────────────────────────────────────────────────────────
# Stage 2: Production
# ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS production

LABEL maintainer="Senior MLOps Engineer"
LABEL version="2.0.0"
LABEL description="ComfyUI Async Generation Engine v2.0"

# Create non-root user
RUN groupadd -r engine && useradd -r -g engine -s /bin/false engine

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
WORKDIR /app
COPY --from=builder /build/engine/ ./engine/
COPY --from=builder /build/main.py ./
COPY --from=builder /build/config/ ./config/
COPY --from=builder /build/pyproject.toml ./

# Create directories
RUN mkdir -p /app/output_models /app/logs /app/sessions /app/checkpoints /app/workflows \
    && chown -R engine:engine /app

# Switch to non-root user
USER engine

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:9090/health || exit 1

# Expose metrics port
EXPOSE 9090

# Environment variables
ENV COMFYUI_URL=http://host.docker.internal:8188
ENV ENGINE_LOG_LEVEL=INFO
ENV ENGINE_JSON_LOGGING=true
ENV ENGINE_OUTPUT_DIR=/app/output_models
ENV ENGINE_METRICS_PORT=9090

# Default command
ENTRYPOINT ["python", "-m", "main"]
CMD ["--batch", "4", "--workflow", "workflows/standard.json", "--metrics-port", "9090"]

# ───────────────────────────────────────────────────────────────
# Stage 3: Development
# ───────────────────────────────────────────────────────────────
FROM production AS development

USER root

# Install dev dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    htop \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install dev Python packages
RUN pip install --no-cache-dir pytest pytest-asyncio black ruff mypy coverage

USER engine

# Mount points for development
VOLUME ["/app/workflows", "/app/config", "/app/output_models", "/app/logs"]

# Default dev command
CMD ["--batch", "2", "--workflow", "workflows/standard.json", "--verbose", "--metrics-port", "9090"]
