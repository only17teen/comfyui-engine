# ComfyUI Engine - Kiro Protocol v3.0 Optimization Guidelines

This document provides optimization guidelines for improving the comfyui-engine project based on the Kiro Protocol v3.0 optimization framework from https://github.com/only17teen/kiro-v3-optimizations.

## Optimization Phases Implementation

### Phase 1: Actor Model - Concurrency Architecture
**Current Status**: The engine uses asyncio but could benefit from actor model patterns.

**Implementation Guidelines**:
- Replace direct asyncio task creation with actor-based message passing
- Implement sharded routing (16-lock sharding inspired by DashMap) for concurrent component access
- Add priority mailbox system with 5-level priority queue for job processing
- Implement supervisor pattern with hierarchical restart policies (ONE_FOR_ONE, ONE_FOR_ALL, REST_FOR_ONE)
- Add message pool with pre-allocated objects for zero-allocation hot path

**Files to Modify**:
- `engine/core.py` - Core actor system implementation
- `engine/session_manager.py` - Actor-based session management
- `engine/workflow_validator.py` - Actor-based validation workflows

### Phase 2: Resource Management - GPU/LLM Limits
**Current Status**: Basic GPU usage but lacks advanced resource management.

**Implementation Guidelines**:
- Implement token-bucket semaphore for GPU rate limiting with multi-device load balancing
- Add circuit breaker pattern (CLOSED/OPEN/HALF_OPEN states) with automatic recovery
- Implement adaptive timeout based on p95 latency measurements
- Add GPU memory fraction configuration and memory pool utilization

**Files to Modify**:
- `engine/gpu_autoscaler.py` - Enhance GPU resource management
- `engine/core.py` - Add circuit breaker and adaptive timeout
- `engine/config.py` - Add GPU resource management configuration options

### Phase 3: Resilience - Fault Tolerance
**Current Status**: Has some retry mechanisms but can be improved.

**Implementation Guidelines**:
- Replace current retry with full jitter backoff to prevent thundering herd
- Implement status code discrimination (503 retryable, 4xx not retryable)
- Add 5 retry strategies: FIXED, LINEAR, EXPONENTIAL, FULL_JITTER, DECORRELATED_JITTER
- Enhance dead letter queue with better failure analysis

**Files to Modify**:
- `engine/protocols.py` - Enhanced retry mechanisms
- `engine/dead_letter_queue.py` - Improved failure handling
- `engine/api_client.py` - Status code-aware retry logic

### Phase 4: Strategy Optimization - Dynamic Routing
**Current Status**: Static workflow routing.

**Implementation Guidelines**:
- Implement UCB1 bandit algorithm for GPU strategy selection
- Add contextual bandit feature-based arm selection for workload optimization
- Create composite reward system combining latency, throughput, success, cost, and quality
- Add dynamic workflow routing based on performance metrics

**Files to Modify**:
- `engine/strategy_optimizer.py` (new file) - UCB1 and contextual bandit implementation
- `engine/workflow_validator.py` - Dynamic routing integration
- `engine/cost_optimizer.py` - Enhanced cost modeling for rewards

### Phase 5: Learning & Adaptation - Continuous Improvement
**Current Status**: Limited learning capabilities.

**Implementation Guidelines**:
- Implement precognition cache with Markov chain prefetching and semantic similarity
- Add LoRA trainer daemon for offline training with checkpoint resume
- Create reward feedback loop for real-time strategy adjustment
- Add automatic model cleanup based on usage patterns

**Files to Modify**:
- `engine/cache/precognition.py` (new file) - Precognition cache implementation
- `engine/mlflow_tracker.py` - Enhanced LoRA training capabilities
- `engine/model_cache.py` - Learning-based cache eviction

### Phase 6: Observability - Monitoring & Testing
**Current Status**: Has metrics and tracing but can be enhanced.

**Implementation Guidelines**:
- Enhance Prometheus metrics with cardinality explosion protection
- Implement chaos engineering monkey with 8 failure types
- Add safe hours automatic chaos suspension during low-traffic periods
- Improve distributed tracing with better context propagation
- Add low-cardinality labeling for metrics

**Files to Modify**:
- `engine/metrics.py` - Enhanced metrics with cardinality protection
- `engine/chaos_monkey.py` (new file) - Chaos engineering implementation
- `engine/tracing.py` - Improved trace context propagation
- `engine/notifications.py` - Chaos event notifications

### Phase 7: Native Optimization - Maximum Performance
**Current Status**: Pure Python implementation.

**Implementation Guidelines**:
- Add Rust FFI for ActorRegistry with lock-free reads via RwLock
- Implement CUDA kernels for Flash Attention, fused layer norm
- Add INT8/INT4 quantization support for faster inference
- Implement CUDA graphs for static workload capture and replay
- Optimize critical paths with zero-copy data structures

**Files to Modify**:
- `rust/` directory - Rust FFI implementation
- `engine/cuda/` directory - CUDA kernel implementations
- `engine/core.py` - Rust FFI integration points
- `engine/gpu_autoscaler.py` - CUDA graphs integration

## Immediate Action Items

Based on code review, here are specific improvements that can be made immediately:

### 1. Enhance Existing Retry Mechanism
- Replace simple exponential backoff with full jitter retry in `engine/protocols.py`
- Add status code discrimination logic

### 2. Implement Actor Model Foundation
- Create basic actor system in `engine/actor/` directory
- Add sharded router for concurrent access to shared resources

### 3. Add Resource Management
- Implement token-bucket semaphore for GPU limiting
- Add circuit breaker to API client

### 4. Enhance Observability
- Add Prometheus histogram metrics for job latency
- Implement structured logging with trace IDs

## Development Guidelines

### Code Style
- Follow existing PEP 8 style in the codebase
- Use type hints consistently
- Add docstrings for all public classes and methods
- Keep functions focused and under 50 lines when possible

### Testing
- Write unit tests for all new components
- Add integration tests for actor interactions
- Include chaos engineering tests for failure scenarios
- Benchmark performance improvements

### Configuration
- Add new optimization features to `config.py` with sensible defaults
- Use environment variables for production tuning
- Maintain backward compatibility with existing configurations

## Monitoring Success

Track these metrics to validate optimization effectiveness:

1. **Actor message routing**: Target 500K msg/s (10x improvement)
2. **GPU inference latency (p95)**: Target 45ms (4.4x improvement)
3. **Cache hit rate**: Target 35% (+35% improvement)
4. **GC pause time**: Target <5ms (10x improvement)
5. **Retry success rate**: Target 95% (+58% improvement)
6. **Memory allocations (hot path)**: Target 100/s (100x improvement)

## References

- Kiro Protocol v3.0: https://github.com/only17teen/kiro-v3-optimizations
- Actor Model patterns: Akka, Orleans
- Resource Management: Token Bucket, Circuit Breaker
- Resilience Patterns: Retry with jitter, Bulkhead
- Strategy Optimization: Multi-armed bandits, UCB1
- Learning Systems: Precaching, Online learning
- Observability: Prometheus, OpenTelemetry, Chaos Engineering
- Native Optimization: Rust FFI, CUDA, Quantization

---
*Last updated: $(date)*
*Optimization target: Kiro Protocol v3.0 7-phase implementation*