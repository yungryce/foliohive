#!/usr/bin/env bash
# Orchestrate full local development workflow for Cloudfolio.
# Syncs the pilot branch, prepares the shared virtualenv, runs tests,
# launches every Function App worker with log redirection, executes the
# end-to-end curl suite, and monitors the workers for crashes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
APPS_DIR="$SCRIPT_DIR"
VENV_DIR="$APPS_DIR/.venv"
LOG_DIR="$APPS_DIR/logs"

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

declare -A WORKER_PORTS=(
    [api-gateway]=7071
    [sync-worker]=7072
    [merge-worker]=7073
)
declare -a WORKER_SEQUENCE=("api-gateway" "sync-worker" "merge-worker")

declare -a WORKER_PIDS=()
declare -a WORKER_NAMES=()
declare -a WORKER_LOGS=()

declare -a TAIL_PIDS=()

SKIP_E2E=false
QUIET=false
TAIL_LOGS=false
FUNC_VERBOSE=false
VENV_ACTIVE=false

usage() {
    cat <<'EOF'
Usage: ./run-dev-session.sh [options]

Options:
  -h, --help          Show this help message
  --skip-e2e          Skip ./tests/e2e_curl_tests.sh
  --quiet             Reduce log chatter once workers are running
    --tail-logs          Tail worker logs in real time (prefixed)
    --func-verbose       Start workers with 'func start --verbose'

Additional options are forwarded to ./setup-dev.sh, e.g.:
    ./run-dev-session.sh -- --clean --python-version 3.12

The script always starts api-gateway, sync-worker, and merge-worker using
Azure Functions Core Tools, writing logs to apps/logs/<worker>.log. All
commands run from the repository root (../) and the apps/ directory.
Press Ctrl+C to stop the workers and exit when monitoring is enabled.
EOF
}

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }

cleanup() {
    local exit_code=$?

    for pid in "${TAIL_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done

    # Kill any remaining func processes on our ports
    for port in 7071 7072 7073; do
        lsof -ti:$port | xargs kill -9 2>/dev/null || true
    done
    if [[ "$VENV_ACTIVE" == true ]]; then
        deactivate 2>/dev/null || true
    fi
    exit "$exit_code"
}
trap cleanup EXIT
trap 'log_warn "Interrupted; shutting down..."; exit 2' INT TERM

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            --skip-e2e)
                SKIP_E2E=true
                shift
                continue
                ;;
            --quiet)
                QUIET=true
                shift
                continue
                ;;
            --tail-logs)
                TAIL_LOGS=true
                shift
                continue
                ;;
            --func-verbose)
                FUNC_VERBOSE=true
                shift
                continue
                ;;
        esac
    done
}

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log_error "Required command '$cmd' not found in PATH"
        exit 1
    fi
}

activate_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        log_error "Virtual environment not found at $VENV_DIR. Run setup-dev.sh first."
        exit 1
    fi
    log_step "Activating consolidated virtual environment"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    VENV_ACTIVE=true
}

validate_env() {
    if ! python - <<'PY' >/dev/null 2>&1
import cloudfolio_shared
PY
    then
        log_error "cloudfolio-shared not importable from venv. Run ./setup-dev.sh (without skipping)."
        exit 1
    fi
}

clean_logs() {
    if [[ -d "$LOG_DIR" ]]; then
        rm -rf "$LOG_DIR"
    fi
}

prepare_logs() {
    mkdir -p "$LOG_DIR"
}

ensure_dependencies() {
    require_command func
    require_command curl
    require_command jq
}

wait_for_worker_ready() {
    local name="$1"
    local log_file="$2"
    local pid="$3"
    local attempts=60
    local ready=false

    while (( attempts-- > 0 )); do
        if ! kill -0 "$pid" >/dev/null 2>&1; then
            log_error "$name exited before signalling readiness. See $log_file"
            tail -n 40 "$log_file" || true
            exit 1
        fi
        if grep -qE 'Host lock lease acquired|Job host started|Host started' "$log_file"; then
            ready=true
            break
        fi
        sleep 1
    done

    if [[ "$ready" == true ]]; then
        log_info "$name ready (log: $log_file)"
    else
        log_warn "$name has not reported readiness after 60s; monitor $log_file"
    fi
}

start_log_tailers() {
    if [[ "$TAIL_LOGS" != true ]]; then
        return 0
    fi

    log_step "Tailing worker logs (Ctrl+C to stop session)"
    local use_stdbuf=false
    if command -v stdbuf >/dev/null 2>&1; then
        use_stdbuf=true
    fi

    for i in "${!WORKER_LOGS[@]}"; do
        local name="${WORKER_NAMES[$i]}"
        local log_file="${WORKER_LOGS[$i]}"

        if [[ "$use_stdbuf" == true ]]; then
            ( tail -n 0 -F "$log_file" | stdbuf -oL sed -u "s/^/[$name] /" ) &
        else
            ( tail -n 0 -F "$log_file" | sed -u "s/^/[$name] /" ) &
        fi
        TAIL_PIDS+=("$!")
    done
}

start_worker() {
    local name="$1"
    local port="$2"
    local app_dir="$APPS_DIR/$name"
    local log_file="$LOG_DIR/${name}.log"

    if [[ ! -d "$app_dir" ]]; then
        log_error "Function app directory not found: $app_dir"
        exit 1
    fi

    log_step "Starting $name on port $port"
    : > "$log_file"

    local -a func_args
    func_args=(start --python --port "$port")
    if [[ "$FUNC_VERBOSE" == true ]]; then
        func_args+=(--verbose)
    fi

    (
        cd "$app_dir"
        PYTHON_ISOLATE_WORKER_DEPENDENCIES=0 func "${func_args[@]}" >"$log_file" 2>&1
    ) &
    local pid=$!

    WORKER_NAMES+=("$name")
    WORKER_PIDS+=("$pid")
    WORKER_LOGS+=("$log_file")
    log_info "$name PID: $pid (log: $log_file)"

    wait_for_worker_ready "$name" "$log_file" "$pid"
}

run_e2e_tests() {
    if [[ "$SKIP_E2E" == true ]]; then
        log_warn "Skipping e2e curl tests as requested"
        return 0
    fi
    log_step "Running end-to-end curl suite"
    (cd "$APPS_DIR" && ./tests/e2e_curl_tests.sh)
}

monitor_workers() {
    log_info "Workers running. Press Ctrl+C to stop them."
    while true; do
        sleep 5
        for i in "${!WORKER_PIDS[@]}"; do
            local pid="${WORKER_PIDS[$i]}"
            local name="${WORKER_NAMES[$i]}"
            local log_file="${WORKER_LOGS[$i]}"
            if ! kill -0 "$pid" >/dev/null 2>&1; then
                log_error "$name terminated unexpectedly"
                tail -n 40 "$log_file" || true
                exit 1
            fi
        done
        if [[ "$QUIET" != true ]]; then
            log_info "All workers healthy (logs in $LOG_DIR)"
        fi
    done
}

main() {
    parse_args "$@"
    ensure_dependencies
    
    activate_venv
    validate_env
    clean_logs
    prepare_logs

    for worker in "${WORKER_SEQUENCE[@]}"; do
        start_worker "$worker" "${WORKER_PORTS[$worker]}"
    done

    start_log_tailers

    if run_e2e_tests; then
        log_info "Workflow complete. Logs available in $LOG_DIR"
        monitor_workers
    else
        log_error "E2E tests failed. Check logs and fix issues before monitoring."
        exit 1
    fi
}

main "$@"
