#!/bin/bash
set -euo pipefail

COMFYUI_ENGINE_URL="${COMFYUI_ENGINE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-test-api-key}"
DURATION="${DURATION:-10m}"
VUS="${VUS:-10}"

echo "=== ComfyUI Engine Load Test Runner ==="
echo "Target: $COMFYUI_ENGINE_URL"
echo "Duration: $DURATION"
echo "VUs: $VUS"
echo ""

if ! command -v k6 &> /dev/null; then
    echo "Installing k6..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo gpg -k
        sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1E69
        echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
        sudo apt-get update
        sudo apt-get install k6
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install k6
    else
        echo "Please install k6 manually: https://k6.io/docs/get-started/installation/"
        exit 1
    fi
fi

echo "Running smoke test..."
k6 run --env BASE_URL="$COMFYUI_ENGINE_URL" --env API_KEY="$API_KEY" \
    --env WS_URL="${COMFYUI_ENGINE_URL/http/ws}" \
    -i 10 tests/load/k6_load_test.js

echo ""
echo "Running load test..."
k6 run --env BASE_URL="$COMFYUI_ENGINE_URL" --env API_KEY="$API_KEY" \
    --env WS_URL="${COMFYUI_ENGINE_URL/http/ws}" \
    --env SCENARIO=load \
    tests/load/k6_load_test.js

echo ""
echo "Running stress test..."
k6 run --env BASE_URL="$COMFYUI_ENGINE_URL" --env API_KEY="$API_KEY" \
    --env WS_URL="${COMFYUI_ENGINE_URL/http/ws}" \
    --env SCENARIO=stress \
    tests/load/k6_load_test.js

echo ""
echo "All load tests completed!"
