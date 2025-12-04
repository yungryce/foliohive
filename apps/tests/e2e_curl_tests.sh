#!/usr/bin/env bash
# =============================================================================
# End-to-End Curl Tests for Cloudfolio Function Apps
# =============================================================================
# Tests the complete workflow from API Gateway through queue workers
#
# Prerequisites:
#   1. Azurite running: azurite --silent --location ~/.azurite --debug ~/.azurite/debug.log
#   2. API Gateway running: cd api-gateway && func start --port 7071
#   3. Sync Worker running: cd sync-worker && func start --port 7072
#   4. Merge Worker running: cd merge-worker && func start --port 7073
#   5. GITHUB_TOKEN set in environment or local.settings.json
#
# Usage:
#   ./tests/e2e_curl_tests.sh [options]
#
# Options:
#   -h, --help          Show help
#   -v, --verbose       Verbose output (show response bodies)
#   -u, --username USER GitHub username to test (default: yungryce)
#   --api-port PORT     API Gateway port (default: 7071)
#   --skip-prereqs      Skip prerequisite checks
#   --only SUITE        Run only specific suite (health|bundles|refresh|ai)
# =============================================================================

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$(dirname "$SCRIPT_DIR")"

# Defaults
API_PORT="${API_PORT:-7071}"
API_BASE="http://localhost:${API_PORT}/api"
TEST_USERNAME="${TEST_USERNAME:-yungryce}"
VERBOSE=false
SKIP_PREREQS=false
ONLY_SUITE=""
TIMEOUT=30

# Colors
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly NC='\033[0m'

# Counters
PASSED=0
FAILED=0
SKIPPED=0

# =============================================================================
# Logging
# =============================================================================
log_info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_test()    { echo -e "${BLUE}[TEST]${NC} $1"; }
log_pass()    { echo -e "${GREEN}[PASS]${NC} $1"; ((PASSED++)); }
log_fail()    { echo -e "${RED}[FAIL]${NC} $1"; ((FAILED++)); }
log_skip()    { echo -e "${YELLOW}[SKIP]${NC} $1"; ((SKIPPED++)); }
log_section() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

# =============================================================================
# CLI Parsing
# =============================================================================
show_help() {
    cat <<'EOF'
End-to-End Curl Tests for Cloudfolio Function Apps

Usage: ./tests/e2e_curl_tests.sh [options]

Options:
  -h, --help              Show this help message
  -v, --verbose           Show full response bodies
  -u, --username USER     GitHub username to test (default: yungryce)
  --api-port PORT         API Gateway port (default: 7071)
  --skip-prereqs          Skip Azurite/server checks
  --only SUITE            Run only: health, bundles, refresh, ai

Prerequisites:
  1. Start Azurite:
     azurite --silent --location ~/.azurite --debug ~/.azurite/debug.log

  2. Start API Gateway (in separate terminal):
     cd apps/api-gateway && source .venv/bin/activate
     func start --port 7071

  3. (Optional) Start workers for queue tests:
     cd apps/sync-worker && func start --port 7072
     cd apps/merge-worker && func start --port 7073

  4. Set GITHUB_TOKEN environment variable for GitHub API access

Examples:
  ./tests/e2e_curl_tests.sh                      # Full test suite
  ./tests/e2e_curl_tests.sh -v                   # Verbose mode
  ./tests/e2e_curl_tests.sh --only health        # Health checks only
  ./tests/e2e_curl_tests.sh -u octocat --only bundles
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_help
                exit 0
                ;;
            -v|--verbose)
                VERBOSE=true
                shift
                ;;
            -u|--username)
                TEST_USERNAME="$2"
                shift 2
                ;;
            --api-port)
                API_PORT="$2"
                API_BASE="http://localhost:${API_PORT}/api"
                shift 2
                ;;
            --skip-prereqs)
                SKIP_PREREQS=true
                shift
                ;;
            --only)
                ONLY_SUITE="$2"
                shift 2
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
}

# =============================================================================
# Utilities
# =============================================================================

# Execute curl and capture response + status code
# Usage: response=$(curl_request "GET" "/health")
#        status=$(echo "$response" | tail -1)
#        body=$(echo "$response" | head -n -1)
curl_request() {
    local method="$1"
    local endpoint="$2"
    local data="${3:-}"
    local url="${API_BASE}${endpoint}"
    
    local curl_args=(
        -s
        -w '\n%{http_code}'
        -X "$method"
        -H "Content-Type: application/json"
        -H "Accept: application/json"
        --max-time "$TIMEOUT"
    )
    
    if [[ -n "$data" ]]; then
        curl_args+=(-d "$data")
    fi
    
    curl "${curl_args[@]}" "$url" 2>/dev/null || echo -e "\n000"
}

# Parse response to extract body and status
parse_response() {
    local response="$1"
    local status
    local body
    
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    echo "$status"
    echo "$body"
}

# Assert HTTP status code
assert_status() {
    local expected="$1"
    local actual="$2"
    local test_name="$3"
    
    if [[ "$actual" == "$expected" ]]; then
        log_pass "$test_name (HTTP $actual)"
        return 0
    else
        log_fail "$test_name - Expected HTTP $expected, got $actual"
        return 1
    fi
}

# Assert JSON field exists and optionally matches value
# Usage: assert_json_field "$body" ".status" "ok"
assert_json_field() {
    local body="$1"
    local field="$2"
    local expected="${3:-}"
    
    local actual
    actual=$(echo "$body" | jq -r "$field" 2>/dev/null || echo "null")
    
    if [[ "$actual" == "null" ]]; then
        return 1
    fi
    
    if [[ -n "$expected" && "$actual" != "$expected" ]]; then
        return 1
    fi
    
    return 0
}

# Check if a service is running
check_service() {
    local url="$1"
    local name="$2"
    
    if curl -s --max-time 2 "$url" >/dev/null 2>&1; then
        log_info "$name is running"
        return 0
    else
        log_warn "$name is not responding at $url"
        return 1
    fi
}

# =============================================================================
# Prerequisite Checks
# =============================================================================
check_prerequisites() {
    log_section "Prerequisite Checks"
    
    # Check jq is available
    if ! command -v jq &>/dev/null; then
        log_error "jq is required but not installed. Install with: sudo apt install jq"
        exit 1
    fi
    log_info "jq available"
    
    # Check curl is available
    if ! command -v curl &>/dev/null; then
        log_error "curl is required but not installed"
        exit 1
    fi
    log_info "curl available"
    
    # Check Azurite (optional but recommended)
    if curl -s --max-time 2 "http://127.0.0.1:10000/" >/dev/null 2>&1; then
        log_info "Azurite blob service running (port 10000)"
    else
        log_warn "Azurite not detected - cache/queue operations may fail"
    fi
    
    # Check API Gateway
    if ! check_service "${API_BASE}/health" "API Gateway"; then
        log_error "API Gateway must be running on port $API_PORT"
        log_info "Start with: cd apps/api-gateway && func start --port $API_PORT"
        exit 1
    fi
}

# =============================================================================
# Test Suites
# =============================================================================

test_health() {
    log_section "Health Check Tests"
    
    local response status body
    
    # Test 1: Health endpoint returns 200
    log_test "GET /health"
    response=$(curl_request "GET" "/health")
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if assert_status "200" "$status" "Health endpoint accessible"; then
        if assert_json_field "$body" ".status" "ok"; then
            log_pass "Health status is 'ok'"
        else
            log_fail "Health status field missing or not 'ok'"
        fi
        
        if assert_json_field "$body" ".queue_mode"; then
            log_pass "Queue mode field present"
        fi
        
        if assert_json_field "$body" ".cache"; then
            log_pass "Cache field present"
        fi
    fi
    
    [[ "$VERBOSE" == true ]] && echo "$body" | jq . 2>/dev/null || true
}

test_bundles() {
    log_section "Bundle Endpoint Tests"
    
    local response status body
    
    # Test 1: Get bundle for user (may be 404 if no cache)
    log_test "GET /bundles/${TEST_USERNAME}"
    response=$(curl_request "GET" "/bundles/${TEST_USERNAME}")
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if [[ "$status" == "200" ]]; then
        log_pass "Bundle found for ${TEST_USERNAME}"
        if assert_json_field "$body" ".username" "$TEST_USERNAME"; then
            log_pass "Username matches"
        fi
        if assert_json_field "$body" ".data"; then
            log_pass "Bundle data present"
        fi
    elif [[ "$status" == "404" ]]; then
        log_warn "No cached bundle for ${TEST_USERNAME} (expected on first run)"
    else
        log_fail "Unexpected status $status for bundle request"
    fi
    
    [[ "$VERBOSE" == true ]] && echo "$body" | jq . 2>/dev/null || true
    
    # Test 2: Bundle with missing username returns 400
    log_test "GET /bundles/ (missing username)"
    response=$(curl_request "GET" "/bundles/")
    status=$(echo "$response" | tail -1)
    # Note: Azure Functions may return 404 for missing route param
    if [[ "$status" == "400" || "$status" == "404" ]]; then
        log_pass "Missing username handled correctly (HTTP $status)"
    else
        log_fail "Expected 400/404 for missing username, got $status"
    fi
    
    # Test 3: Get single repo bundle
    log_test "GET /bundles/${TEST_USERNAME}/cloudfolio"
    response=$(curl_request "GET" "/bundles/${TEST_USERNAME}/cloudfolio")
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if [[ "$status" == "200" ]]; then
        log_pass "Single repo bundle found"
    elif [[ "$status" == "404" ]]; then
        log_warn "Repo 'cloudfolio' not cached (trigger refresh first)"
    else
        log_fail "Unexpected status $status"
    fi
}

test_refresh() {
    log_section "Refresh Workflow Tests"
    
    local response status body job_id
    
    # Test 1: Trigger bundle refresh
    log_test "POST /bundles/${TEST_USERNAME}/refresh"
    response=$(curl_request "POST" "/bundles/${TEST_USERNAME}/refresh" '{"force_refresh": false}')
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if [[ "$status" == "202" ]]; then
        log_pass "Refresh job accepted"
        
        job_id=$(echo "$body" | jq -r '.job_id' 2>/dev/null)
        if [[ -n "$job_id" && "$job_id" != "null" ]]; then
            log_pass "Job ID returned: ${job_id:0:8}..."
            
            # Test 2: Check job status
            log_test "GET /bundles/${TEST_USERNAME}/status?job_id=${job_id}"
            response=$(curl_request "GET" "/bundles/${TEST_USERNAME}/status?job_id=${job_id}")
            status=$(echo "$response" | tail -1)
            body=$(echo "$response" | head -n -1)
            
            if assert_status "200" "$status" "Job status endpoint"; then
                local job_status
                job_status=$(echo "$body" | jq -r '.status' 2>/dev/null)
                log_info "Job status: $job_status"
                
                if assert_json_field "$body" ".progress.total"; then
                    local total
                    total=$(echo "$body" | jq -r '.progress.total' 2>/dev/null)
                    log_pass "Progress tracking works (total: $total)"
                fi
            fi
            
            [[ "$VERBOSE" == true ]] && echo "$body" | jq . 2>/dev/null || true
        fi
        
    elif [[ "$status" == "200" ]]; then
        # May return 200 if bundle is already cached
        local cached_status
        cached_status=$(echo "$body" | jq -r '.status' 2>/dev/null)
        if [[ "$cached_status" == "cached" ]]; then
            log_pass "Bundle already cached, no refresh needed"
        else
            log_warn "Unexpected 200 response"
        fi
        
    elif [[ "$status" == "503" ]]; then
        log_warn "Queue mode disabled (check Azurite/queue config)"
        
    else
        log_fail "Unexpected status $status for refresh"
    fi
    
    [[ "$VERBOSE" == true ]] && echo "$body" | jq . 2>/dev/null || true
    
    # Test 3: Missing job_id returns 400
    log_test "GET /bundles/${TEST_USERNAME}/status (missing job_id)"
    response=$(curl_request "GET" "/bundles/${TEST_USERNAME}/status")
    status=$(echo "$response" | tail -1)
    
    if assert_status "400" "$status" "Missing job_id handled"; then
        :
    fi
    
    # Test 4: Invalid job_id returns 404
    log_test "GET /bundles/${TEST_USERNAME}/status?job_id=invalid-uuid"
    response=$(curl_request "GET" "/bundles/${TEST_USERNAME}/status?job_id=00000000-0000-0000-0000-000000000000")
    status=$(echo "$response" | tail -1)
    
    if assert_status "404" "$status" "Invalid job_id returns 404"; then
        :
    fi
}

test_ai() {
    log_section "AI Query Tests"
    
    local response status body
    
    # Test 1: AI query requires body
    log_test "POST /ai (empty body)"
    response=$(curl_request "POST" "/ai" '{}')
    status=$(echo "$response" | tail -1)
    
    if assert_status "400" "$status" "Empty AI query rejected"; then
        :
    fi
    
    # Test 2: AI query requires username and query
    log_test "POST /ai (missing fields)"
    response=$(curl_request "POST" "/ai" '{"query": "test"}')
    status=$(echo "$response" | tail -1)
    
    if assert_status "400" "$status" "Missing username rejected"; then
        :
    fi
    
    # Test 3: Valid AI query (may fail if no bundle cached)
    log_test "POST /ai (valid query)"
    response=$(curl_request "POST" "/ai" "{\"query\": \"What projects use Python?\", \"username\": \"${TEST_USERNAME}\"}")
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if [[ "$status" == "200" ]]; then
        log_pass "AI query successful"
        [[ "$VERBOSE" == true ]] && echo "$body" | jq . 2>/dev/null || true
    elif [[ "$status" == "400" ]]; then
        local error_msg
        error_msg=$(echo "$body" | jq -r '.error' 2>/dev/null)
        if [[ "$error_msg" == *"No repository bundle"* ]]; then
            log_warn "No bundle cached - trigger refresh first"
        else
            log_fail "AI query failed: $error_msg"
        fi
    else
        log_fail "Unexpected status $status for AI query"
    fi
}


# =============================================================================
# Integration/Workflow Tests
# =============================================================================
test_full_workflow() {
    log_section "Full Workflow Integration Test"
    
    local response status body job_id
    
    log_info "Testing complete refresh → poll → query workflow for $TEST_USERNAME"
    
    # Step 1: Trigger refresh
    log_test "Step 1: Trigger refresh"
    response=$(curl_request "POST" "/bundles/${TEST_USERNAME}/refresh" '{"force_refresh": true}')
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if [[ "$status" != "202" && "$status" != "200" ]]; then
        log_fail "Refresh failed with status $status"
        return 1
    fi
    
    job_id=$(echo "$body" | jq -r '.job_id // empty' 2>/dev/null)
    
    if [[ -z "$job_id" ]]; then
        log_info "Bundle was cached, skipping poll"
    else
        log_pass "Job started: ${job_id:0:8}..."
        
        # Step 2: Poll for completion (max 60 seconds)
        log_test "Step 2: Poll job status"
        local max_attempts=12
        local attempt=0
        local job_status="queued"
        
        while [[ "$attempt" -lt "$max_attempts" && "$job_status" != "completed" ]]; do
            sleep 5
            ((attempt++))
            
            response=$(curl_request "GET" "/bundles/${TEST_USERNAME}/status?job_id=${job_id}")
            status=$(echo "$response" | tail -1)
            body=$(echo "$response" | head -n -1)
            
            if [[ "$status" == "200" ]]; then
                job_status=$(echo "$body" | jq -r '.status' 2>/dev/null)
                local progress
                progress=$(echo "$body" | jq -r '.progress.percentage' 2>/dev/null)
                log_info "Attempt $attempt: $job_status ($progress%)"
            else
                log_warn "Status check failed (HTTP $status)"
            fi
        done
        
        if [[ "$job_status" == "completed" ]]; then
            log_pass "Job completed successfully"
        else
            log_warn "Job did not complete within timeout (status: $job_status)"
        fi
    fi
    
    # Step 3: Fetch bundle
    log_test "Step 3: Fetch cached bundle"
    response=$(curl_request "GET" "/bundles/${TEST_USERNAME}")
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)
    
    if assert_status "200" "$status" "Bundle retrieved"; then
        local repo_count
        repo_count=$(echo "$body" | jq '.data | length' 2>/dev/null)
        log_pass "Bundle contains $repo_count repositories"
    fi
    
    # Step 4: AI query (if bundle exists)
    if [[ "$status" == "200" ]]; then
        log_test "Step 4: AI query"
        response=$(curl_request "POST" "/ai" "{\"query\": \"What are the main projects?\", \"username\": \"${TEST_USERNAME}\"}")
        status=$(echo "$response" | tail -1)
        
        if [[ "$status" == "200" ]]; then
            log_pass "AI query successful"
        else
            log_warn "AI query failed (may need API keys configured)"
        fi
    fi
}

# =============================================================================
# Summary
# =============================================================================
print_summary() {
    log_section "Test Summary"
    
    local total=$((PASSED + FAILED + SKIPPED))
    
    echo ""
    echo -e "  ${GREEN}Passed${NC}:  $PASSED"
    echo -e "  ${RED}Failed${NC}:  $FAILED"
    echo -e "  ${YELLOW}Skipped${NC}: $SKIPPED"
    echo -e "  Total:   $total"
    echo ""
    
    if [[ $FAILED -gt 0 ]]; then
        echo -e "${RED}Some tests failed!${NC}"
        return 1
    else
        echo -e "${GREEN}All tests passed!${NC}"
        return 0
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    parse_args "$@"
    
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║         Cloudfolio E2E Curl Tests                             ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    log_info "API Base: $API_BASE"
    log_info "Test Username: $TEST_USERNAME"
    log_info "Verbose: $VERBOSE"
    
    # Prerequisites
    if [[ "$SKIP_PREREQS" != true ]]; then
        check_prerequisites
    fi
    
    # Run test suites
    case "${ONLY_SUITE}" in
        health)
            test_health
            ;;
        bundles)
            test_bundles
            ;;
        refresh)
            test_refresh
            ;;
        ai)
            test_ai
            ;;
        workflow)
            test_full_workflow
            ;;
        "")
            # Run all suites
            test_health
            test_bundles
            test_refresh
            test_ai
            ;;
        *)
            log_error "Unknown suite: $ONLY_SUITE"
            log_info "Valid suites: health, bundles, refresh, ai, workflow"
            exit 1
            ;;
    esac
    
    print_summary
}

main "$@"
