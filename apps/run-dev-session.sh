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

PYTHON_SITE_PACKAGES=""
PYTHONPATH_EXTRA=""

SKIP_PULL=false
SKIP_SETUP=false
SKIP_E2E=false
QUIET=false
VENV_ACTIVE=false
NO_TEST_RUN=false

usage() {
    cat <<'EOF'
Usage: ./run-dev-session.sh [options]

Options:
  -h, --help          Show this help message
  -sp, --skip-pull    Skip git fetch/pull for pilot branch
  -ss, --skip-setup   Skip ./setup-dev.sh (and tests)
  --no-test-run       Skip tests during setup (runs setup only)
  --skip-e2e          Skip ./tests/e2e_curl_tests.sh
  --quiet             Reduce log chatter once workers are running

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
    if [[ ${#WORKER_PIDS[@]} -gt 0 ]]; then
        for i in "${!WORKER_PIDS[@]}"; do
            local pid="${WORKER_PIDS[$i]}"
            local name="${WORKER_NAMES[$i]}"
            if kill -0 "$pid" >/dev/null 2>&1; then
                log_warn "Stopping $name (PID $pid)"
                kill "$pid" >/dev/null 2>&1 || true
                wait "$pid" 2>/dev/null || true
            fi
        done
    fi
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
            -sp|--skip-pull)
                SKIP_PULL=true
                shift
                ;;
            -ss|--skip-setup)
                SKIP_SETUP=true
                shift
                ;;
            --no-test-run)
                NO_TEST_RUN=true
                shift
                ;;
            --skip-e2e)
                SKIP_E2E=true
                shift
                ;;
            --quiet)
                QUIET=true
                shift
                ;;
            --)
                shift
                break
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
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

ensure_branch_synced() {
    if [[ "$SKIP_PULL" == true ]]; then
        log_warn "Skipping git pull as requested"
        return
    fi
    log_step "Syncing branch 'pilot' with origin"
    require_command git
    local current_branch
    current_branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
    if [[ "$current_branch" != "pilot" ]]; then
        log_warn "Current branch is $current_branch (expected pilot)"
    fi
    git -C "$REPO_ROOT" fetch origin
    git -C "$REPO_ROOT" pull --ff-only origin pilot
}

run_setup() {
    if [[ "$SKIP_SETUP" == true ]]; then
        log_warn "Skipping setup-dev.sh as requested"
        return
    fi
    local setup_cmd="./setup-dev.sh"
    if [[ "$NO_TEST_RUN" != true ]]; then
        setup_cmd="$setup_cmd --run-tests"
        log_step "Running setup-dev.sh with tests"
    else
        log_step "Running setup-dev.sh (tests skipped)"
    fi
    (cd "$APPS_DIR" && $setup_cmd)
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

prepare_logs() {
    mkdir -p "$LOG_DIR"
}

ensure_dependencies() {
    require_command func
    require_command curl
    require_command jq
}

compute_python_paths() {
    PYTHON_SITE_PACKAGES=$(python - <<'PY'
import sysconfig
print(sysconfig.get_path("purelib"))
PY
)
    if [[ -n "$PYTHON_SITE_PACKAGES" ]]; then
        PYTHONPATH_EXTRA="$PYTHON_SITE_PACKAGES:$APPS_DIR/shared/src"
    else
        log_warn "Unable to determine site-packages path; Function Apps may not see shared deps"
        PYTHONPATH_EXTRA="$APPS_DIR/shared/src"
    fi
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
    (
        cd "$app_dir"
        local worker_pythonpath="$PYTHONPATH_EXTRA"
        if [[ -n "${PYTHONPATH:-}" ]]; then
            if [[ -n "$worker_pythonpath" ]]; then
                worker_pythonpath="$worker_pythonpath:${PYTHONPATH}"
            else
                worker_pythonpath="${PYTHONPATH}"
            fi
        fi
        PYTHON_ISOLATE_WORKER_DEPENDENCIES=0 \
        PYTHONPATH="$worker_pythonpath" \
        func start --python --port "$port" >"$log_file" 2>&1
    ) &
    local pid=$!

    WORKER_NAMES+=("$name")
    WORKER_PIDS+=("$pid")
    WORKER_LOGS+=("$log_file")

    wait_for_worker_ready "$name" "$log_file" "$pid"
}

run_e2e_tests() {
    if [[ "$SKIP_E2E" == true ]]; then
        log_warn "Skipping e2e curl tests as requested"
        return
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
    
    # Warn about git pull if not skipped
    if [[ "$SKIP_PULL" == false ]]; then
        echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${YELLOW}⚠️  Git pull will be performed on branch 'pilot'${NC}"
        echo -e "${YELLOW}To skip: use -sp or --skip-pull${NC}"
        echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        read -p "Continue? (y/n) " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_warn "Aborted by user"
            exit 0
        fi
    fi
    
    ensure_branch_synced
    run_setup
    activate_venv
    compute_python_paths
    prepare_logs

    for worker in "${WORKER_SEQUENCE[@]}"; do
        start_worker "$worker" "${WORKER_PORTS[$worker]}"
    done

    run_e2e_tests

    log_info "Workflow complete. Logs available in $LOG_DIR"
    monitor_workers
}

main "$@"
