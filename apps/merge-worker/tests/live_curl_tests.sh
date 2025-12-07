#!/usr/bin/env bash
# ============================================================================
# Merge Worker Live Curl Tests
# ============================================================================
# Invokes the merge worker queue-trigger via the Functions host admin endpoint
# using curl to validate success and failure paths.
#
# Prerequisites:
#   1. Merge worker running locally: `func start --port 7073`
#   2. Optional: jq for verbose output formatting
#
# Usage:
#   ./tests/live_curl_tests.sh [options]
#
# Options:
#   -h, --help            Show help
#   -v, --verbose         Verbose output
#   --port PORT           Worker port (default: 7073)
#   --function-key KEY    Host/function key (optional)
#   --username USER       Username for bundle owner (default: yungryce)
# ============================================================================

set -euo pipefail

PORT="${PORT:-7073}"
FUNCTION_NAME="process_merge_job"
FUNCTION_KEY="${FUNCTION_KEY:-}"
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

help() {
    cat <<'EOF'
Merge Worker Live Curl Tests

Usage: ./tests/live_curl_tests.sh [options]

Options:
  -h, --help            Show this help message
  -v, --verbose         Show verbose logging
  --port PORT           Merge worker Functions host port (default: 7073)
  --function-key KEY    Optional Functions host key (x-functions-key)
  --username USER       GitHub username used in test payload (default: yungryce)
EOF
}

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_test()  { echo -e "${BLUE}[TEST]${NC} $1"; }
log_pass()  { echo -e "${GREEN}[PASS]${NC} $1"; ((PASSED++)); }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; ((FAILED++)); }
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
            --port)
                PORT="$2"
                shift 2
                ;;
            --function-key)
                FUNCTION_KEY="$2"
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

invoke_function() {
    local payload="$1"
    local url="http://localhost:${PORT}/admin/functions/${FUNCTION_NAME}"
    local response status body
    local headers=("-H" "Content-Type: application/json")

    if [[ -n "$FUNCTION_KEY" ]]; then
        headers+=("-H" "x-functions-key: ${FUNCTION_KEY}")
    fi

    response=$(curl -sS -w '\n%{http_code}' -X POST "${url}" "${headers[@]}" -d "${payload}" || true)
    status=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n -1)

    if [[ "$VERBOSE" == true ]]; then
        echo "$body"
    fi

    echo "$status" "$body"
}

build_valid_payload() {
    local msg
    msg=$(cat <<JSON
{
  "job_id": "curl-merge-job",
  "username": "${TEST_USERNAME}",
  "synced_repos": ["cloudfolio", "awesome-project"],
  "fresh_repos": [
    {
      "name": "cloudfolio",
      "metadata": {"name": "cloudfolio"},
      "fingerprint": "fp-cloudfolio",
      "has_documentation": true
    },
    {
      "name": "awesome-project",
      "metadata": {"name": "awesome-project"},
      "fingerprint": "fp-awesome",
      "has_documentation": false
    }
  ],
  "cached_bundle": []
}
JSON
)
    local encoded
    encoded=$(printf '%s' "${msg}" | base64 | tr -d '\n')
    printf '{"input":"%s"}' "${encoded}"
}

build_invalid_payload() {
    local msg
    msg=$(cat <<JSON
{
  "job_id": "",
  "username": "",
  "fresh_repos": []
}
JSON
)
    local encoded
    encoded=$(printf '%s' "${msg}" | base64 | tr -d '\n')
    printf '{"input":"%s"}' "${encoded}"
}

test_valid_message() {
    log_test "Invoke merge worker with valid payload"
    local payload="$(build_valid_payload)"
    read -r status body <<<"$(invoke_function "$payload")"

    if [[ "$status" == "200" || "$status" == "202" ]]; then
        log_pass "Merge worker accepted valid payload (HTTP $status)"
        return 0
    fi

    log_fail "Expected 200/202 but received $status"
    [[ "$VERBOSE" == true ]] || echo "$body"
    return 1
}

test_invalid_message() {
    log_test "Invoke merge worker with invalid payload"
    local payload="$(build_invalid_payload)"
    read -r status body <<<"$(invoke_function "$payload")"

    if [[ "$status" == "400" || "$status" == "500" ]]; then
        log_pass "Merge worker rejected invalid payload (HTTP $status)"
        return 0
    fi

    log_fail "Expected 400/500 but received $status"
    [[ "$VERBOSE" == true ]] || echo "$body"
    return 1
}

main() {
    parse_args "$@"
    log_section "Configuration"
    log_info "Worker admin endpoint: http://localhost:${PORT}/admin/functions/${FUNCTION_NAME}"
    log_info "Username: ${TEST_USERNAME}"
    log_info "Verbose: ${VERBOSE}"

    if [[ -z "${FUNCTION_KEY}" ]]; then
        log_warn "Function key not provided; assuming local host with anonymous admin access"
    fi

    log_section "Tests"
    test_valid_message
    test_invalid_message

    log_section "Summary"
    log_info "Passed: ${PASSED}"
    log_info "Failed: ${FAILED}"

    if [[ ${FAILED} -ne 0 ]]; then
        exit 1
    fi
}

main "$@"
