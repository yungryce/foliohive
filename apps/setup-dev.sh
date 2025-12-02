#!/usr/bin/env bash
# Setup development environment for Cloudfolio microservices
# Creates isolated virtual environments and installs shared package

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$SCRIPT_DIR"
SHARED_DIR="$APPS_DIR/shared"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

PYTHON_VERSION="3.11"
FUNCTION_APPS=("api-gateway" "sync-worker" "merge-worker")

show_help() {
    cat <<'EOF'
Usage: ./setup-dev.sh [options]

Options:
  -h, --help          Show this help message
  -c, --clean         Remove existing virtual environments before setup
  -s, --shared-only   Only setup the shared package (skip function apps)
  -a, --app NAME      Setup only a specific function app
  --no-dev            Skip development dependencies

Examples:
  ./setup-dev.sh                    # Full setup (shared + all apps)
  ./setup-dev.sh --clean            # Clean reinstall
  ./setup-dev.sh --app api-gateway  # Setup only api-gateway
  ./setup-dev.sh --shared-only      # Setup only shared package
EOF
}

CLEAN=false
SHARED_ONLY=false
SPECIFIC_APP=""
INSTALL_DEV=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -c|--clean)
            CLEAN=true
            shift
            ;;
        -s|--shared-only)
            SHARED_ONLY=true
            shift
            ;;
        -a|--app)
            SPECIFIC_APP="$2"
            shift 2
            ;;
        --no-dev)
            INSTALL_DEV=false
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check Python version
check_python() {
    if ! command -v python3 &>/dev/null; then
        log_error "Python 3 is required but not installed."
        exit 1
    fi
    
    PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    log_info "Found Python $PYTHON_VER"
    
    if [[ ! "$PYTHON_VER" =~ ^3\.(11|12|13)$ ]]; then
        log_warn "Python 3.11+ recommended, found $PYTHON_VER"
    fi
}

# Setup shared package
setup_shared() {
    log_step "Setting up shared package..."
    cd "$SHARED_DIR"
    
    if [[ "$CLEAN" == true && -d ".venv" ]]; then
        log_warn "Removing existing shared venv..."
        rm -rf .venv
    fi
    
    if [[ ! -d ".venv" ]]; then
        log_info "Creating virtual environment for shared..."
        python3 -m venv .venv
    fi
    
    source .venv/bin/activate
    pip install --upgrade pip wheel setuptools >/dev/null
    
    if [[ "$INSTALL_DEV" == true ]]; then
        log_info "Installing shared package with dev dependencies..."
        pip install -e ".[dev]"
    else
        log_info "Installing shared package..."
        pip install -e .
    fi
    
    deactivate
    log_info "Shared package ready: $SHARED_DIR/.venv"
}

# Setup a single function app
setup_function_app() {
    local app_name="$1"
    local app_dir="$APPS_DIR/$app_name"
    
    if [[ ! -d "$app_dir" ]]; then
        log_error "Function app not found: $app_dir"
        return 1
    fi
    
    log_step "Setting up $app_name..."
    cd "$app_dir"
    
    if [[ "$CLEAN" == true && -d ".venv" ]]; then
        log_warn "Removing existing $app_name venv..."
        rm -rf .venv
    fi
    
    if [[ ! -d ".venv" ]]; then
        log_info "Creating virtual environment for $app_name..."
        python3 -m venv .venv
    fi
    
    source .venv/bin/activate
    pip install --upgrade pip wheel setuptools >/dev/null
    
    # Install shared package in editable mode
    log_info "Installing cloudfolio-shared in editable mode..."
    if [[ "$INSTALL_DEV" == true ]]; then
        pip install -e "$SHARED_DIR"[dev]
    else
        pip install -e "$SHARED_DIR"
    fi
    
    # Install app-specific requirements (filtering out cloudfolio-shared if present)
    if [[ -f "requirements.txt" ]]; then
        log_info "Installing $app_name requirements..."
        grep -v "cloudfolio-shared" requirements.txt | grep -v "^\s*#" | grep -v "^-e" | grep -v "^$" | while read -r req; do
            pip install "$req" 2>/dev/null || true
        done
    fi
    
    deactivate
    log_info "$app_name ready: $app_dir/.venv"
}

# Setup integration tests
setup_tests() {
    log_step "Setting up integration tests..."
    local tests_dir="$APPS_DIR/tests"
    cd "$tests_dir"
    
    if [[ "$CLEAN" == true && -d ".venv" ]]; then
        log_warn "Removing existing tests venv..."
        rm -rf .venv
    fi
    
    if [[ ! -d ".venv" ]]; then
        log_info "Creating virtual environment for tests..."
        python3 -m venv .venv
    fi
    
    source .venv/bin/activate
    pip install --upgrade pip wheel setuptools >/dev/null
    
    # Install shared package with dev deps
    log_info "Installing cloudfolio-shared with dev dependencies..."
    pip install -e "$SHARED_DIR[dev]"
    
    # Install test-specific requirements
    if [[ -f "requirements.txt" ]]; then
        log_info "Installing test requirements..."
        pip install -r requirements.txt
    fi
    
    deactivate
    log_info "Tests environment ready: $tests_dir/.venv"
}

# Main execution
main() {
    log_info "Setting up Cloudfolio development environment..."
    check_python
    
    # Always setup shared first
    setup_shared
    
    if [[ "$SHARED_ONLY" == true ]]; then
        log_info "Shared-only mode: skipping function apps"
        echo ""
        echo -e "${GREEN}✅ Shared package setup complete!${NC}"
        echo ""
        echo "To activate: source apps/shared/.venv/bin/activate"
        exit 0
    fi
    
    # Setup function apps
    if [[ -n "$SPECIFIC_APP" ]]; then
        setup_function_app "$SPECIFIC_APP"
    else
        for app in "${FUNCTION_APPS[@]}"; do
            setup_function_app "$app"
        done
        setup_tests
    fi
    
    echo ""
    echo -e "${GREEN}✅ Development environment setup complete!${NC}"
    echo ""
    echo "To activate a specific environment:"
    echo "  source apps/shared/.venv/bin/activate      # For shared development"
    echo "  source apps/api-gateway/.venv/bin/activate # For API gateway"
    echo "  source apps/sync-worker/.venv/bin/activate # For sync worker"
    echo "  source apps/merge-worker/.venv/bin/activate # For merge worker"
    echo "  source apps/tests/.venv/bin/activate       # For integration tests"
    echo ""
    echo "To run tests:"
    echo "  cd apps && source tests/.venv/bin/activate && ./tests/run_tests.sh"
}

main "$@"
