#!/bin/bash
set -euo pipefail

COMFYUI_ENGINE_URL="${COMFYUI_ENGINE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-test-api-key}"
CHAOS_TYPE="${CHAOS_TYPE:-all}"
DURATION="${DURATION:-10m}"

echo "=== ComfyUI Engine Chaos Engineering Runner ==="
echo "Target: $COMFYUI_ENGINE_URL"
echo "Chaos Type: $CHAOS_TYPE"
echo "Duration: $DURATION"
echo ""

if ! command -v k6 &> /dev/null; then
    echo "k6 not found. Please install k6: https://k6.io/docs/get-started/installation/"
    exit 1
fi

echo "Running chaos engineering test..."
k6 run --env BASE_URL="$COMFYUI_ENGINE_URL" --env API_KEY="$API_KEY" \
    --env CHAOS_TYPE="$CHAOS_TYPE" \
    --duration "$DURATION" \
    tests/load/k6_chaos_test.js

echo ""
echo "Chaos test completed!"
