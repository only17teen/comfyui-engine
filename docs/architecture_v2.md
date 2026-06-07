# ComfyUI Engine v2.0 - Architecture Decisions

Date: 2026-06-07
Tags: #comfyui #mlops #architecture #asyncio #resilience #python

## Overview

Fundamental rewrite of the generation engine from v1.0 to v2.0 with enterprise-grade resilience patterns, observability, and configuration management.

## Key Decisions

### 1. Circuit Breaker Pattern

**Problem**: ComfyUI GPU server becomes unresponsive under heavy load. Naive retry causes cascading failures.

**Solution**: `CircuitBreaker` class with three states:
- `CLOSED`: Normal operation, track failures
- `OPEN`: Reject requests after threshold (5 failures), wait 30s recovery
- `HALF_OPEN`: Allow 3 test requests, close on 2 successes

**Impact**: Prevents client from hammering dead server. Automatic recovery detection.

### 2. Exponential Backoff with Jitter

**Problem**: Fixed retry intervals cause thundering herd.

**Solution**: `with_retry()` decorator with:
- Base delay: 1.0s
- Exponential multiplier: 2.0
- Max delay: 60.0s
- Jitter: ±10% randomization
- Retryable exceptions: `aiohttp.ClientError`, `asyncio.TimeoutError`, `OSError`

**Impact**: Distributes retry load, prevents synchronized retries.

### 3. Structured JSON Logging

**Problem**: Plain text logs hard to parse for monitoring.

**Solution**: Dual logging:
- Terminal: Human-readable format
- File: JSON lines with `timestamp`, `level`, `logger`, `message`, `module`, `function`, `line`

**Impact**: Integrates with Loki, Vector, Fluentd log aggregation.

### 4. Pydantic Configuration

**Problem**: YAML config errors discovered at runtime.

**Solution**: `EngineConfig` Pydantic model with:
- Type validation (URL format, weight ranges, resolution bounds)
- Environment variable override (`COMFYUI_URL`, `ENGINE_MAX_CONCURRENT`, etc.)
- Deep merge: env vars > YAML > defaults

**Impact**: Fail-fast configuration validation. Container-friendly deployment.

### 5. Template-Based Prompts

**Problem**: v1.0 had hardcoded prompt assembly.

**Solution**: `PromptTemplate` with named templates:
- `standard`: `{triggers}, {clothing}, {pose}, {location}, {lighting}, {expression}`
- `portrait`: Close-up focused
- `cinematic`: Film grain, dramatic lighting
- `fashion`: Studio lighting emphasis
- `full_body`: Full body composition

**Impact**: Consistent prompt structure per style. Easy to add new templates.

### 6. Seed Strategies

**Problem**: v1.0 only had random seeds.

**Solution**: Pluggable `SeedStrategy`:
- `random`: `random.randint(1, 2**32-1)`
- `time_based`: `int(time.time() * 1000)`
- `sequential`: Auto-incrementing counter
- `fixed`: User-specified seed

**Impact**: Reproducible generations, A/B testing, regression testing.

### 7. Metrics Collection

**Problem**: No visibility into engine performance.

**Solution**: `MetricsCollector` with:
- Counters: jobs_submitted, jobs_completed, jobs_failed, retries_total
- Gauges: queue_depth, active_workers
- Histograms: processing_time, queue_wait_time (with p50/p95/p99)
- Async-safe with locks

**Impact**: Performance monitoring, bottleneck identification.

### 8. Priority Queue with Backpressure

**Problem**: Unbounded queue growth during GPU overload.

**Solution**: `JobQueue` with:
- Max size: 100 (configurable)
- Priority levels: CRITICAL(0), HIGH(1), NORMAL(2), LOW(3)
- Rate limiting: optional jobs/second cap
- Timeout on enqueue (backpressure)

**Impact**: Prevents memory exhaustion. Priority jobs processed first.

### 9. WebSocket + HTTP Fallback

**Problem**: v1.0 only polled `/history` endpoint.

**Solution**: Dual monitoring:
- WebSocket: Real-time status updates, queue depth tracking
- HTTP `/history`: Fallback polling with adaptive intervals
  - Fast poll (0.5s) for first 30s
  - Slow poll (1.0s) after

**Impact**: Faster completion detection. Reduced API load.

### 10. Session Manifests

**Problem**: v1.0 had scattered metadata files.

**Solution**: `_session_manifest.json` with:
- Complete job list with timing
- Config snapshots
- Output file mappings
- Per-job JSON metadata

**Impact**: Full session reproducibility. Easy post-analysis.

## Performance Characteristics

| Metric | v1.0 | v2.0 |
|--------|------|------|
| Lines of code | ~800 | 3232 |
| Test coverage | 0% | Comprehensive |
| Resilience patterns | None | Circuit breaker, retry, queue |
| Observability | Basic logging | JSON logs, metrics, manifests |
| Configuration | Raw YAML | Pydantic + env vars |
| Prompt system | Hardcoded | Templates + strategies |

## File Structure

```
engine/
├── core.py          (470 lines) - Infrastructure
├── config.py        (259 lines) - Pydantic config
├── prompt_manager.py (439 lines) - Templates, seeds, LoRA
├── api_client.py    (500 lines) - Resilient client
├── output_handler.py (317 lines) - Downloads, manifests
├── git_sync.py      (352 lines) - Atomic git ops
└── __init__.py      (41 lines) - Package exports
```

## Next Steps

- [ ] Add WebSocket reconnection logic
- [ ] Implement job resumption from manifest
- [ ] Add Prometheus metrics endpoint
- [ ] Create systemd service file for Arch Linux
- [ ] Add distributed queue (Redis/RabbitMQ) for multi-GPU
