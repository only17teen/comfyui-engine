#!/bin/bash
set -euo pipefail

# Load test runner script for ComfyUI Engine

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
K6_SCRIPT="${K6_SCRIPT:-tests/load/k6_load_test.js}"
LOCUSTFILE="${LOCUSTFILE:-tests/load/locustfile.py}"
BASE_URL="${BASE_URL:-http://localhost:8080}"
API_KEY="${API_KEY:-}"
DURATION="${DURATION:-10m}"
VUS="${VUS:-50}"
RESULTS_DIR="${RESULTS_DIR:-load-test-results}"

# Create results directory
mkdir -p "$RESULTS_DIR"

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_debug() {
    echo -e "${BLUE}[DEBUG]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    local missing=()
    
    if ! command -v k6 >/dev/null 2>&1; then
        missing+=("k6")
    fi
    
    if ! command -v locust >/dev/null 2>&1; then
        missing+=("locust")
    fi
    
    if ! command -v curl >/dev/null 2>&1; then
        missing+=("curl")
    fi
    
    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing prerequisites: ${missing[*]}"
        log_info "Install k6: https://k6.io/docs/getting-started/installation/"
        log_info "Install locust: pip install locust"
        exit 1
    fi
    
    log_info "All prerequisites found"
}

# Health check
health_check() {
    log_info "Performing health check..."
    
    local max_attempts=30
    local attempt=0
    
    while [ $attempt -lt $max_attempts ]; do
        if curl -s "${BASE_URL}/health" > /dev/null 2>&1; then
            log_info "Health check passed"
            return 0
        fi
        
        attempt=$((attempt + 1))
        log_warn "Health check attempt $attempt/$max_attempts failed, retrying..."
        sleep 2
    done
    
    log_error "Health check failed after $max_attempts attempts"
    return 1
}

# Run k6 load test
run_k6_test() {
    log_info "Running k6 load test..."
    log_info "Script: $K6_SCRIPT"
    log_info "Duration: $DURATION"
    log_info "VUs: $VUS"
    
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local results_file="${RESULTS_DIR}/k6_results_${timestamp}.json"
    local summary_file="${RESULTS_DIR}/k6_summary_${timestamp}.json"
    
    export BASE_URL
    export API_KEY
    
    k6 run \
        --duration "$DURATION" \
        --vus "$VUS" \
        --out json="$results_file" \
        --summary-export="$summary_file" \
        "$K6_SCRIPT"
    
    log_info "k6 results saved to: $results_file"
    log_info "k6 summary saved to: $summary_file"
    
    # Display summary
    if [ -f "$summary_file" ]; then
        log_info "k6 test summary:"
        cat "$summary_file" | python3 -m json.tool 2>/dev/null || cat "$summary_file"
    fi
}

# Run k6 chaos test
run_k6_chaos_test() {
    log_info "Running k6 chaos test..."
    
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local results_file="${RESULTS_DIR}/k6_chaos_results_${timestamp}.json"
    local summary_file="${RESULTS_DIR}/k6_chaos_summary_${timestamp}.json"
    
    export BASE_URL
    export API_KEY
    export CHAOS_MODE=true
    
    k6 run \
        --duration "$DURATION" \
        --vus "$VUS" \
        --out json="$results_file" \
        --summary-export="$summary_file" \
        tests/load/k6_chaos_test.js
    
    log_info "k6 chaos results saved to: $results_file"
    log_info "k6 chaos summary saved to: $summary_file"
}

# Run Locust test
run_locust_test() {
    log_info "Running Locust load test..."
    log_info "Locustfile: $LOCUSTFILE"
    
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local results_file="${RESULTS_DIR}/locust_results_${timestamp}.csv"
    local report_file="${RESULTS_DIR}/locust_report_${timestamp}.html"
    
    locust \
        -f "$LOCUSTFILE" \
        --host "$BASE_URL" \
        --users "$VUS" \
        --spawn-rate 10 \
        --run-time "$DURATION" \
        --csv "$results_file" \
        --html "$report_file" \
        --headless \
        --only-summary
    
    log_info "Locust results saved to: $results_file"
    log_info "Locust report saved to: $report_file"
}

# Run all tests
run_all_tests() {
    log_info "Running all load tests..."
    
    health_check
    
    run_k6_test
    run_k6_chaos_test
    run_locust_test
    
    log_info "All tests completed"
    log_info "Results saved to: $RESULTS_DIR"
}

# Generate report
generate_report() {
    log_info "Generating test report..."
    
    local report_file="${RESULTS_DIR}/test_report_$(date +%Y%m%d_%H%M%S).md"
    
    cat > "$report_file" << EOF
# ComfyUI Engine Load Test Report

Generated: $(date)
Base URL: $BASE_URL
Duration: $DURATION
Virtual Users: $VUS

## Test Results

### k6 Load Test
$(ls -1 ${RESULTS_DIR}/k6_summary_*.json 2>/dev/null | tail -1 | xargs cat 2>/dev/null || echo "No k6 results found")

### k6 Chaos Test
$(ls -1 ${RESULTS_DIR}/k6_chaos_summary_*.json 2>/dev/null | tail -1 | xargs cat 2>/dev/null || echo "No k6 chaos results found")

### Locust Test
$(ls -1 ${RESULTS_DIR}/locust_results_*.csv 2>/dev/null | tail -1 | head -20 2>/dev/null || echo "No Locust results found")

## Files

$(ls -1 ${RESULTS_DIR}/)
EOF
    
    log_info "Report generated: $report_file"
}

# Show usage
usage() {
    echo "Usage: $0 [OPTIONS] [COMMAND]"
    echo
    echo "Commands:"
    echo "  k6          Run k6 load test"
    echo "  k6-chaos    Run k6 chaos test"
    echo "  locust      Run Locust load test"
    echo "  all         Run all tests (default)"
    echo "  report      Generate test report"
    echo
    echo "Options:"
    echo "  -u, --base-url URL     Base URL for testing (default: http://localhost:8080)"
    echo "  -k, --api-key KEY      API key for authentication"
    echo "  -d, --duration TIME    Test duration (default: 10m)"
    echo "  -v, --vus NUM          Number of virtual users (default: 50)"
    echo "  -o, --output DIR       Output directory for results (default: load-test-results)"
    echo "  -h, --help             Show this help message"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -u|--base-url)
            BASE_URL="$2"
            shift 2
            ;;
        -k|--api-key)
            API_KEY="$2"
            shift 2
            ;;
        -d|--duration)
            DURATION="$2"
            shift 2
            ;;
        -v|--vus)
            VUS="$2"
            shift 2
            ;;
        -o|--output)
            RESULTS_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        k6|k6-chaos|locust|all|report)
            COMMAND="$1"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main
main() {
    check_prerequisites
    
    case "${COMMAND:-all}" in
        k6)
            health_check
            run_k6_test
            ;;
        k6-chaos)
            health_check
            run_k6_chaos_test
            ;;
        locust)
            health_check
            run_locust_test
            ;;
        all)
            run_all_tests
            ;;
        report)
            generate_report
            ;;
        *)
            log_error "Unknown command: $COMMAND"
            usage
            exit 1
            ;;
    esac
}

main