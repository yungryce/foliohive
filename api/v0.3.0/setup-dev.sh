#!/usr/bin/env bash
# Setup development environment for foliohive backend (v0.3.0)
# Creates a single consolidated virtual environment at api/v0.3.0/.venv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$SCRIPT_DIR"
SHARED_DIR="$APPS_DIR/shared"
VENV_DIR="$APPS_DIR/.venv"
REPO_ROOT="$(dirname "$APPS_DIR")"
PYTHON_BIN=""
PYTHON_VERSION=""
readonly AZURITE_HELPER="$APPS_DIR/ensure-azurite.sh"

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# Configuration - Function App(s) to install requirements from
# v0.3.0 is consolidated into a single Function App hosting multiple blueprints.
readonly FUNCTION_APPS=("function-app")

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
    local needs_recreate=false
    if [[ -d "$VENV_DIR" && "$CLEAN" == true ]]; then
        needs_recreate=true
        log_warn "Removing existing consolidated venv..."
    elif [[ -d "$VENV_DIR" && "$CLEAN" != true ]]; then
        local current_version=""
        if [[ -x "$VENV_DIR/bin/python" ]]; then
            current_version=$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        fi
        if [[ "$current_version" != "$PYTHON_VERSION" ]]; then
            needs_recreate=true
            log_warn "Existing venv uses Python ${current_version:-unknown}; recreating with $PYTHON_VERSION..."
        fi
    fi

    if [[ "$needs_recreate" == true ]]; then
        rm -rf "$VENV_DIR" || die "Failed to remove $VENV_DIR"
    fi

    if [[ ! -d "$VENV_DIR" ]]; then
        log_info "Creating consolidated virtual environment at $VENV_DIR..."
        "$PYTHON_BIN" -m venv "$VENV_DIR" || die "Failed to create venv at $VENV_DIR"
    fi
}

# Activate venv and upgrade pip
activate_venv() {
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate" || die "Failed to activate venv at $VENV_DIR"
    pip install --upgrade pip wheel setuptools >/dev/null 2>&1
}

# Check if a package is installed in current venv
# Usage: is_installed <package_name>
is_installed() {
    pip show "$1" >/dev/null 2>&1
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

    if pip show foliohive-shared >/dev/null 2>&1; then
        if [[ "$FORCE_REINSTALL" == true ]]; then
            log_info "Reinstalling foliohive-shared (force enabled)..."
        else
            log_info "Refreshing foliohive-shared editable install..."
        fi
    else
        log_info "Installing foliohive-shared${extras:+ with $extras}..."
    fi

    pip install $pkg_spec
}

# =============================================================================
# CLI
# =============================================================================
show_help() {
    cat <<'EOF'
Usage: ./setup-dev.sh [options]

Options:
  -h, --help               Show this help message
  -c, --clean              Remove existing virtual environments before setup
  -f, --force              Force reinstall all packages even if installed
  -s, --shared-only        Only setup the shared package (skip function apps)
  -a, --app NAME           Setup only a specific function app
  -p, --python-version VER Specify Python version (e.g., 3.13, 3.12)
                           Default: 3.13 (Azure Functions supported)
                           Supported: 3.14, 3.13, 3.12
  --no-dev                 Skip development dependencies
  --run-tests              Run tests after setup completes
  --debug                  Enable debug output

Examples:
  ./setup-dev.sh                       # Full setup (shared + all apps)
  ./setup-dev.sh --clean               # Clean reinstall
  ./setup-dev.sh --force               # Force reinstall packages
  ./setup-dev.sh --python-version 3.12 # Use Python 3.12
    ./setup-dev.sh --app function-app    # Setup only function-app
  ./setup-dev.sh --shared-only         # Setup only shared package
  ./setup-dev.sh --run-tests           # Setup and run full test suite
EOF
}

CLEAN=false
SHARED_ONLY=false
SPECIFIC_APP=""
INSTALL_DEV=true
FORCE_REINSTALL=false
DEBUG=false
RUN_TESTS=false
REQUESTED_PYTHON_VERSION=""  # User-specified Python version (e.g., "3.14", "3.12")

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
            -p|--python-version)
                [[ -z "${2:-}" ]] && die "Option --python-version requires an argument (e.g., 3.14)"
                REQUESTED_PYTHON_VERSION="$2"
                shift 2
                ;;
            --no-dev)
                INSTALL_DEV=false
                shift
                ;;
            --run-tests)
                RUN_TESTS=true
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
    # Supported Python versions for Azure Functions v4
    local supported_versions=("3.14" "3.13" "3.12")
    local default_version="3.13"
    local target_version="${REQUESTED_PYTHON_VERSION:-$default_version}"
    
    # Validate requested version
    local version_valid=false
    for v in "${supported_versions[@]}"; do
        if [[ "$target_version" == "$v" ]]; then
            version_valid=true
            break
        fi
    done
    
    if [[ "$version_valid" != true ]]; then
        die "Python $target_version is not supported. Supported versions: ${supported_versions[*]}"
    fi
    
    # Build candidate list based on target version
    local candidates=("python${target_version}" "python3")
    
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" &>/dev/null; then
            local detected_version
            detected_version=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            if [[ "$detected_version" == "$target_version"* ]]; then
                PYTHON_BIN="$candidate"
                PYTHON_VERSION="$detected_version"
                break
            fi
        fi
    done

    if [[ -z "$PYTHON_BIN" ]]; then
        die "Python $target_version not found. Install python${target_version} and retry."
    fi

    if [[ -n "$REQUESTED_PYTHON_VERSION" && "$REQUESTED_PYTHON_VERSION" != "3.13" ]]; then
        log_warn "Using Python $PYTHON_VERSION. Note: Azure Functions officially supports 3.12-3.14."
    fi
    
    log_info "Using Python $PYTHON_VERSION via $PYTHON_BIN"
}

setup_shared() {
    log_step "Installing shared package..."
    
    [[ -d "$SHARED_DIR" ]] || die "Shared directory not found: $SHARED_DIR"
    
    local extras=""
    [[ "$INSTALL_DEV" == true ]] && extras="dev"
    
    smart_editable_install "$SHARED_DIR" "$extras"
    log_info "Shared package installed"


    # Log installed packages for debugging, deleted afterward
    local pkg_log="$VENV_DIR/.installed-packages.log"
    pip list --format=columns >"$pkg_log"
    log_info "Dependencies presently installed (see temporary log):"
    sed -n '1,20p' "$pkg_log"
    log_info "Full list recorded in $pkg_log (deleted immediately afterward)."
    rm -f "$pkg_log"
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
    grep -v -E "^(foliohive-shared|\s*#|-e|$)" "$app_dir/requirements.txt" | while read -r req; do
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

run_tests() {
    local tests_dir="$APPS_DIR/tests"
    log_step "Running test suite..."
    
    # Check if pytest is available
    if ! python -c "import pytest" >/dev/null 2>&1; then
        log_error "pytest not found"
        return 1
    fi
    
    if [[ -z "${SKIP_AZURITE_HELPER:-}" ]]; then
        bash "$AZURITE_HELPER"
    fi
    
    # Run pytest in current shell to preserve venv activation
    cd "$APPS_DIR"
    python -m pytest -c tests/pytest.ini
    local exit_code=$?
    cd - >/dev/null
    return $exit_code
}

print_success() {
    echo ""
    echo -e "${GREEN}✅ Development environment setup complete!${NC}"
    echo ""
    echo "Consolidated virtual environment: $VENV_DIR"
    echo ""
    echo "To activate:"
    echo "  source $VENV_DIR/bin/activate"
    echo ""
    echo "To run tests:"
    echo "  source $VENV_DIR/bin/activate && python -m pytest -c tests/pytest.ini"
    echo ""
    echo "To run full local workflow (Azurite + Functions + UI):"
    echo "  ./run-dev-session.sh"
}

# =============================================================================
# Main
# =============================================================================
main() {
    parse_args "$@"
    
    log_info "Setting up foliohive development environment..."
    [[ "$FORCE_REINSTALL" == true ]] && log_info "Force reinstall enabled"
    
    check_python
    ensure_venv
    activate_venv
    
    setup_shared
    
    if [[ "$SHARED_ONLY" == true ]]; then
        deactivate
        log_info "Shared-only mode: skipping function apps"
        echo ""
        echo -e "${GREEN}✅ Shared package setup complete!${NC}"
        echo ""
        echo "To activate: source $VENV_DIR/bin/activate"
        exit 0
    fi
    
    local apps_to_setup=()
    if [[ -n "$SPECIFIC_APP" ]]; then
        apps_to_setup=("$SPECIFIC_APP")
    else
        apps_to_setup=("${FUNCTION_APPS[@]}")
    fi
    for app in "${apps_to_setup[@]}"; do
        setup_function_app "$app"
    done

    if [[ "$RUN_TESTS" == true ]]; then
        setup_tests
        SKIP_AZURITE_HELPER=true run_tests || {
            deactivate 2>/dev/null || true
            exit 1
        }
    fi
    
    deactivate
    print_success
}

main "$@"
