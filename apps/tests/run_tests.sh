#!/usr/bin/env bash
# Central test runner for every Cloudfolio app
# Requires: cloudfolio-shared installed via ../setup-dev.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APPS_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$APPS_DIR")"
VENV_DIR="$APPS_DIR/.venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

VERBOSE_FLAG=""
COVERAGE_FLAG="false"
MARKER_VALUE=""
KEYWORD_VALUE=""
TEST_PATH=""
FAILFAST=""

show_help() {
    cat <<'EOF'
Usage: ./tests/run_tests.sh [options] [path]

Requires: Run ../setup-dev.sh first to install cloudfolio-shared

Options:
  -h, --help            Show this help message
  -v, --verbose         Verbose pytest output (-vv)
  -q, --quiet           Quiet mode (-q)
  -c, --coverage        Generate coverage report (htmlcov/)
  -m, --marker NAME     Run only tests matching marker (unit, integration, slow)
  -k, --keyword EXP     Run tests matching keyword expression
  -x, --exitfirst       Stop on first failure

Arguments:
  path                  Optional path to a specific test file or directory

Examples:
  ./tests/run_tests.sh                                    # Run entire suite
  ./tests/run_tests.sh shared/src/cloudfolio_shared/cache # Run cache tests
  ./tests/run_tests.sh -m unit                            # Fast unit tests only
  ./tests/run_tests.sh -c -v                              # Verbose with coverage
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -v|--verbose)
            VERBOSE_FLAG="-vv"
            shift
            ;;
        -q|--quiet)
            VERBOSE_FLAG="-q"
            shift
            ;;
        -c|--coverage)
            COVERAGE_FLAG="true"
            shift
            ;;
        -m|--marker)
            MARKER_VALUE="$2"
            shift 2
            ;;
        -k|--keyword)
            KEYWORD_VALUE="$2"
            shift 2
            ;;
        -x|--exitfirst)
            FAILFAST="-x"
            shift
            ;;
        --)
            shift
            break
            ;;
        -* )
            echo -e "${RED}Unknown option: $1${NC}" >&2
            exit 1
            ;;
        * )
            TEST_PATH="$1"
            shift
            ;;
    esac
done

pushd "$APPS_DIR" > /dev/null

# Ensure shared package is available (installed by setup-dev.sh)
if ! python -c "import cloudfolio_shared" >/dev/null 2>&1; then
    echo -e "${RED}❌ cloudfolio-shared not found in active environment.${NC}"
    echo -e "${YELLOW}Make sure the consolidated venv is activated:${NC}"
    echo -e "${YELLOW}  source .venv/bin/activate${NC}"
    echo -e "${YELLOW}Or run full setup:${NC}"
    echo -e "${YELLOW}  ./setup-dev.sh${NC}"
    exit 1
fi

# Install test requirements if pytest is missing
if ! python -c "import pytest" >/dev/null 2>&1; then
    echo -e "${YELLOW}Installing test dependencies...${NC}"
    pip install -r "$SCRIPT_DIR/requirements.txt"
fi

ensure_azurite() {
    if curl -s http://127.0.0.1:10000/ >/dev/null 2>&1; then
        echo -e "${GREEN}✅ Azurite detected on localhost:10000${NC}"
        return
    fi
    if ! command -v azurite >/dev/null 2>&1; then
        echo -e "${RED}❌ Azurite is required. Install via 'npm install -g azurite'.${NC}"
        exit 1
    fi
    mkdir -p "$REPO_ROOT/.azurite"
    echo -e "${YELLOW}Starting Azurite in background...${NC}"
    nohup azurite --location "$REPO_ROOT/.azurite" --silent >/dev/null 2>&1 &
    sleep 3
    echo -e "${GREEN}✅ Azurite started${NC}"
}

if [[ -z "$MARKER_VALUE" || "$MARKER_VALUE" == *"integration"* ]]; then
    ensure_azurite
fi

PYTEST_ARGS=("-c" "tests/pytest.ini")
[[ -n "$VERBOSE_FLAG" ]] && PYTEST_ARGS+=("$VERBOSE_FLAG")
[[ -n "$MARKER_VALUE" ]] && PYTEST_ARGS+=("-m" "$MARKER_VALUE")
[[ -n "$KEYWORD_VALUE" ]] && PYTEST_ARGS+=("-k" "$KEYWORD_VALUE")
[[ -n "$FAILFAST" ]] && PYTEST_ARGS+=("$FAILFAST")
[[ -n "$TEST_PATH" ]] && PYTEST_ARGS+=("$TEST_PATH")

if [[ "$COVERAGE_FLAG" == "true" ]]; then
    PYTEST_ARGS+=("--cov=cloudfolio_shared" "--cov-report=term-missing" "--cov-report=html")
fi

echo -e "${GREEN}Running tests with: python -m pytest ${PYTEST_ARGS[*]}${NC}"
python -m pytest "${PYTEST_ARGS[@]}"
RESULT=$?

if [[ "$COVERAGE_FLAG" == "true" && $RESULT -eq 0 ]]; then
    echo -e "${GREEN}Coverage report available at apps/htmlcov/index.html${NC}"
fi

popd > /dev/null

if [[ $RESULT -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed${NC}"
else
    echo -e "${RED}✗ Tests failed${NC}"
fi

exit $RESULT
