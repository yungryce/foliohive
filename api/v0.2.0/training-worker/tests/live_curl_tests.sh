#!/usr/bin/env bash
# ============================================================================
# Training Worker Live Curl Tests
# ============================================================================
# Provides HTTP-based smoke tests for the training worker process. Two optional
# checks are available:
#   1. Health endpoint verification (GET)
#   2. Ad-hoc invocation of a test endpoint that emulates queue delivery (POST)
#
# Because the training worker is a long-running poller, direct queue triggers
# are not exposed via HTTP. These curl tests target any HTTP façade (e.g. a
# health probe or dev-only trigger endpoint) that wraps the worker.
#
# Usage:
#   ./tests/live_curl_tests.sh [options]
#
# Options:
#   -h, --help              Show help
#   -v, --verbose           Verbose output
#   --health-url URL        Health endpoint URL (default: TRAINING_HEALTH_URL env or http://localhost:7074/health)
#   --invoke-url URL        Optional POST endpoint to trigger a test training run
#   --username USER         Username used in sample payload (default: yungryce)
# ============================================================================

set -euo pipefail

HEALTH_URL="${TRAINING_HEALTH_URL:-http://localhost:7074/health}"
INVOKE_URL=""
TEST_USERNAME="${TEST_USERNAME:-yungryce}"
VERBOSE=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

help() {
    cat <<'EOF'
Training Worker Live Curl Tests

Usage: ./tests/live_curl_tests.sh [options]

Options:
  -h, --help              Show this help message
  -v, --verbose           Show verbose logging
  --health-url URL        Health endpoint URL (default: TRAINING_HEALTH_URL env or http://localhost:7074/health)
  --invoke-url URL        Optional POST endpoint to trigger test training payload
  --username USER         Username for sample training payload (default: yungryce)
EOF
}

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_test()  { echo -e "${BLUE}[TEST]${NC} $1"; }
log_pass()  { echo -e "${GREEN}[PASS]${NC} $1"; ((PASSED++)); }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; ((FAILED++)); }
log_skip()  { echo -e "${YELLOW}[SKIP]${NC} $1"; ((SKIPPED++)); }
log_section(){ echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                help
                exit 0
                ;;
            -v|--verbose)
                VERBOSE=true
                shift
                ;;
            --health-url)
                HEALTH_URL="$2"
                shift 2
                ;;
            --invoke-url)
                INVOKE_URL="$2"
                shift 2
                ;;
            --username)
                TEST_USERNAME="$2"
                shift 2
                ;;
            --*=*)
                log_warn "Ignoring unknown option $1"
                shift
                ;;
            *)
                log_warn "Ignoring positional argument $1"
                shift
                ;;
        esac
    done
}

test_health_endpoint() {
    log_test "GET ${HEALTH_URL}"
    local response status body
    response=$(curl -sS -w '\n%{http_code}' "${HEALTH_URL}" || true)
    status=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n -1)

    [[ "$VERBOSE" == true ]] && echo "$body"

    if [[ "$status" == "200" ]]; then
        log_pass "Health endpoint returned 200"
        return 0
    fi

    log_fail "Expected 200 from health endpoint, got $status"
    [[ "$VERBOSE" == true ]] || echo "$body"
    return 1
}

build_sample_payload() {
    cat <<JSON
{
  "username": "${TEST_USERNAME}",
  "repos_bundle": [
    {"name": "cloudfolio", "has_documentation": true},
    {"name": "awesome", "has_documentation": true},
    {"name": "demo", "has_documentation": true}
  ],
  "training_params": {"epochs": 1},
  "experiment_name": "curl-smoke"
}
JSON
}

test_invoke_endpoint() {
    if [[ -z "${INVOKE_URL}" ]]; then
        log_skip "No invoke URL provided; skipping training trigger test"
        return 0
    fi

    log_test "POST ${INVOKE_URL}"
    local payload
    payload="$(build_sample_payload)"
    local response status body
    response=$(curl -sS -w '\n%{http_code}' -H "Content-Type: application/json" -X POST "${INVOKE_URL}" -d "${payload}" || true)
    status=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n -1)

    if [[ "$VERBOSE" == true ]]; then
        echo "$body"
    fi

    if [[ "$status" == "200" || "$status" == "202" || "$status" == "204" ]]; then
        log_pass "Invoke endpoint accepted payload (HTTP $status)"
        return 0
    fi

    log_fail "Expected 200/202/204 from invoke endpoint, got $status"
    [[ "$VERBOSE" == true ]] || echo "$body"
    return 1
}

main() {
    parse_args "$@"
    log_section "Configuration"
    log_info "Health URL: ${HEALTH_URL}"
    if [[ -n "${INVOKE_URL}" ]]; then
        log_info "Invoke URL: ${INVOKE_URL}"
    else
        log_info "Invoke URL: (not provided)"
    fi
    log_info "Username: ${TEST_USERNAME}"
    log_info "Verbose: ${VERBOSE}"

    log_section "Tests"
    test_health_endpoint
    test_invoke_endpoint

    log_section "Summary"
    log_info "Passed: ${PASSED}"
    log_info "Failed: ${FAILED}"
    log_info "Skipped: ${SKIPPED}"

    if [[ ${FAILED} -ne 0 ]]; then
        exit 1
    fi
}

main "$@"
