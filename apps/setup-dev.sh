#!/usr/bin/env bash
# Setup development environment for Cloudfolio microservices
# Creates a single consolidated virtual environment at apps/.venv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$SCRIPT_DIR"
SHARED_DIR="$APPS_DIR/shared"
VENV_DIR="$APPS_DIR/.venv"

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# Configuration - function apps to install requirements from
readonly FUNCTION_APPS=("api-gateway" "sync-worker" "merge-worker")
readonly MARKER_FILE=".setup-complete"

# =============================================================================
# Logging
# =============================================================================
log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }
log_debug() { [[ "${DEBUG:-false}" == true ]] && echo -e "[DEBUG] $1" || true; }

# =============================================================================
# Error Handling
# =============================================================================
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Setup failed with exit code $exit_code"
        log_error "Check the output above for details"
    fi
    # Ensure we're not left in an activated venv
    [[ -n "${VIRTUAL_ENV:-}" ]] && deactivate 2>/dev/null || true
    return $exit_code
}
trap cleanup EXIT

die() {
    log_error "$1"
    exit "${2:-1}"
}

# =============================================================================
# Virtual Environment Utilities
# =============================================================================

# Ensure the consolidated venv exists, optionally cleaning first
ensure_venv() {
    if [[ "$CLEAN" == true && -d "$VENV_DIR" ]]; then
        log_warn "Removing existing consolidated venv..."
        rm -rf "$VENV_DIR" || die "Failed to remove $VENV_DIR"
    fi
    
    if [[ ! -d "$VENV_DIR" ]]; then
        log_info "Creating consolidated virtual environment at $VENV_DIR..."
        python3 -m venv "$VENV_DIR" || die "Failed to create venv at $VENV_DIR"
    fi
}

# Activate venv and upgrade pip (only if needed)
activate_venv() {
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate" || die "Failed to activate venv at $VENV_DIR"
    
    # Only upgrade pip/wheel/setuptools if pip is outdated (check once per venv)
    if [[ ! -f "$VENV_DIR/$MARKER_FILE" ]] || [[ "$CLEAN" == true ]]; then
        pip install --upgrade pip wheel setuptools >/dev/null 2>&1
    fi
}

# Check if a package is installed in current venv
# Usage: is_installed <package_name>
is_installed() {
    pip show "$1" >/dev/null 2>&1
}

# Check if editable install is current (compare pyproject.toml mtime with egg-info)
# Usage: is_editable_current <package_dir>
is_editable_current() {
    local pkg_dir="$1"
    local egg_info
    
    # Find egg-info directory
    egg_info=$(find "$pkg_dir" -maxdepth 3 -name "*.egg-info" -type d 2>/dev/null | head -1)
    
    if [[ -z "$egg_info" ]]; then
        return 1  # Not installed
    fi
    
    # Check if pyproject.toml is newer than egg-info
    if [[ "$pkg_dir/pyproject.toml" -nt "$egg_info" ]]; then
        return 1  # Needs reinstall
    fi
    
    return 0  # Current
}

# Install package only if needed
# Usage: smart_install <pip_args...>
smart_install() {
    local pkg_spec="$1"
    shift
    local pkg_name
    
    # Extract package name from spec (e.g., "package[extra]" -> "package")
    pkg_name="${pkg_spec%%\[*}"
    pkg_name="${pkg_name%%-e *}"
    
    if is_installed "$pkg_name" && [[ "$FORCE_REINSTALL" != true ]]; then
        log_debug "Package $pkg_name already installed, skipping"
        return 0
    fi
    
    pip install "$pkg_spec" "$@"
}

# Install editable package only if needed
# Usage: smart_editable_install <pkg_dir> [extras]
smart_editable_install() {
    local pkg_dir="$1"
    local extras="${2:-}"
    local pkg_spec
    
    if [[ -n "$extras" ]]; then
        pkg_spec="-e ${pkg_dir}[${extras}]"
    else
        pkg_spec="-e ${pkg_dir}"
    fi
    
    if is_editable_current "$pkg_dir" && [[ "$FORCE_REINSTALL" != true ]]; then
        log_info "Shared package up-to-date, skipping reinstall"
        return 0
    fi
    
    log_info "Installing cloudfolio-shared${extras:+ with $extras}..."
    pip install $pkg_spec
}

# Mark venv setup as complete
mark_complete() {
    touch "$VENV_DIR/$MARKER_FILE"
}

# =============================================================================
# CLI
# =============================================================================
show_help() {
    cat <<'EOF'
Usage: ./setup-dev.sh [options]

Options:
  -h, --help          Show this help message
  -c, --clean         Remove existing virtual environments before setup
  -f, --force         Force reinstall all packages even if installed
  -s, --shared-only   Only setup the shared package (skip function apps)
  -a, --app NAME      Setup only a specific function app
  --no-dev            Skip development dependencies
  --debug             Enable debug output

Examples:
  ./setup-dev.sh                    # Full setup (shared + all apps)
  ./setup-dev.sh --clean            # Clean reinstall
  ./setup-dev.sh --force            # Force reinstall packages
  ./setup-dev.sh --app api-gateway  # Setup only api-gateway
  ./setup-dev.sh --shared-only      # Setup only shared package
EOF
}

CLEAN=false
SHARED_ONLY=false
SPECIFIC_APP=""
INSTALL_DEV=true
FORCE_REINSTALL=false
DEBUG=false

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_help
                exit 0
                ;;
            -c|--clean)
                CLEAN=true
                FORCE_REINSTALL=true  # Clean implies force
                shift
                ;;
            -f|--force)
                FORCE_REINSTALL=true
                shift
                ;;
            -s|--shared-only)
                SHARED_ONLY=true
                shift
                ;;
            -a|--app)
                [[ -z "${2:-}" ]] && die "Option --app requires an argument"
                SPECIFIC_APP="$2"
                shift 2
                ;;
            --no-dev)
                INSTALL_DEV=false
                shift
                ;;
            --debug)
                DEBUG=true
                shift
                ;;
            *)
                die "Unknown option: $1. Use --help for usage."
                ;;
        esac
    done
}

# =============================================================================
# Setup Functions
# =============================================================================

check_python() {
    command -v python3 &>/dev/null || die "Python 3 is required but not installed."
    
    local python_ver
    python_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    log_info "Found Python $python_ver"
    
    if [[ ! "$python_ver" =~ ^3\.(11|12|13)$ ]]; then
        log_warn "Python 3.11+ recommended, found $python_ver"
    fi
}

setup_shared() {
    log_step "Installing shared package..."
    
    [[ -d "$SHARED_DIR" ]] || die "Shared directory not found: $SHARED_DIR"
    
    local extras=""
    [[ "$INSTALL_DEV" == true ]] && extras="dev"
    
    smart_editable_install "$SHARED_DIR" "$extras"
    log_info "Shared package installed"
}

install_requirements() {
    local app_name="$1"
    local app_dir="$APPS_DIR/$app_name"
    
    if [[ ! -f "$app_dir/requirements.txt" ]]; then
        log_debug "No requirements.txt in $app_name, skipping"
        return 0
    fi
    
    log_info "Installing $app_name requirements..."
    # Filter and install only missing packages
    grep -v -E "^(cloudfolio-shared|\s*#|-e|$)" "$app_dir/requirements.txt" | while read -r req; do
        [[ -z "$req" ]] && continue
        local pkg_name="${req%%[=<>]*}"
        if ! is_installed "$pkg_name" || [[ "$FORCE_REINSTALL" == true ]]; then
            pip install "$req" 2>/dev/null || log_warn "Failed to install: $req"
        else
            log_debug "Skipping already installed: $pkg_name"
        fi
    done
}

setup_function_app() {
    local app_name="$1"
    local app_dir="$APPS_DIR/$app_name"
    
    [[ -d "$app_dir" ]] || die "Function app not found: $app_dir"
    
    log_step "Setting up $app_name..."
    install_requirements "$app_name"
    log_info "$app_name dependencies installed"
}

setup_tests() {
    log_step "Setting up test dependencies..."
    local tests_dir="$APPS_DIR/tests"
    
    [[ -d "$tests_dir" ]] || die "Tests directory not found: $tests_dir"
    
    # Install test-specific requirements
    if [[ -f "$tests_dir/requirements.txt" ]]; then
        log_info "Installing test requirements..."
        grep -v -E "^(\s*#|-e|$)" "$tests_dir/requirements.txt" | while read -r req; do
            [[ -z "$req" ]] && continue
            local pkg_name="${req%%[=<>]*}"
            if ! is_installed "$pkg_name" || [[ "$FORCE_REINSTALL" == true ]]; then
                pip install "$req" 2>/dev/null || log_warn "Failed to install: $req"
            else
                log_debug "Skipping already installed: $pkg_name"
            fi
        done
    fi
    
    log_info "Test dependencies installed"
}

print_success() {
    echo ""
    echo -e "${GREEN}✅ Development environment setup complete!${NC}"
    echo ""
    echo "Consolidated virtual environment: apps/.venv"
    echo ""
    echo "To activate:"
    echo "  source apps/.venv/bin/activate"
    echo ""
    echo "To run tests:"
    echo "  cd apps && source .venv/bin/activate && ./tests/run_tests.sh"
}

# =============================================================================
# Main
# =============================================================================
main() {
    parse_args "$@"
    
    log_info "Setting up Cloudfolio development environment..."
    [[ "$FORCE_REINSTALL" == true ]] && log_info "Force reinstall enabled"
    
    check_python
    ensure_venv
    activate_venv
    
    setup_shared
    
    if [[ "$SHARED_ONLY" == true ]]; then
        mark_complete
        deactivate
        log_info "Shared-only mode: skipping function apps"
        echo ""
        echo -e "${GREEN}✅ Shared package setup complete!${NC}"
        echo ""
        echo "To activate: source apps/.venv/bin/activate"
        exit 0
    fi
    
    if [[ -n "$SPECIFIC_APP" ]]; then
        setup_function_app "$SPECIFIC_APP"
    else
        for app in "${FUNCTION_APPS[@]}"; do
            setup_function_app "$app"
        done
        setup_tests
    fi
    
    mark_complete
    deactivate
    print_success
}

main "$@"
