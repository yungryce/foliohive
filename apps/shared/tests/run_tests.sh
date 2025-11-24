#!/usr/bin/env bash
# Run shared module tests with various options

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
VERBOSE=""
COVERAGE=false
MARKER=""
TEST_FILE=""
FAILFAST=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE="-v"
            shift
            ;;
        -c|--coverage)
            COVERAGE=true
            shift
            ;;
        -m|--marker)
            MARKER="-m $2"
            shift 2
            ;;
        -k|--keyword)
            KEYWORD="-k $2"
            shift 2
            ;;
        -f|--file)
            TEST_FILE="tests/$2"
            shift 2
            ;;
        -x|--exitfirst)
            FAILFAST="-x"
            shift
            ;;
        -h|--help)
            echo "Usage: ./run_tests.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -v, --verbose       Verbose output"
            echo "  -c, --coverage      Run with coverage report"
            echo "  -m, --marker NAME   Run tests with specific marker (unit, integration, slow)"
            echo "  -k, --keyword EXPR  Run tests matching keyword expression"
            echo "  -f, --file FILE     Run specific test file (e.g., test_cache_manager.py)"
            echo "  -x, --exitfirst     Exit on first failure"
            echo "  -h, --help          Show this help message"
            echo ""
            echo "Examples:"
            echo "  ./run_tests.sh                          # Run all tests"
            echo "  ./run_tests.sh -v -c                    # Verbose with coverage"
            echo "  ./run_tests.sh -m unit                  # Run only unit tests"
            echo "  ./run_tests.sh -f test_fingerprint_manager.py   # Run specific file"
            echo "  ./run_tests.sh -k 'cache and not slow'  # Run cache tests, skip slow"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Check if we're in the right directory
if [[ ! -f "tests/conftest.py" ]]; then
    echo -e "${RED}Error: Must run from apps/shared directory${NC}"
    echo "Current directory: $(pwd)"
    exit 1
fi

# Check if dependencies are installed
if ! python -c "import pytest" 2>/dev/null; then
    echo -e "${YELLOW}Installing test dependencies...${NC}"
    pip install -r tests/requirements.txt
fi

# Build pytest command
PYTEST_CMD="pytest $VERBOSE $MARKER $KEYWORD $FAILFAST"

if [[ -n "$TEST_FILE" ]]; then
    PYTEST_CMD="$PYTEST_CMD $TEST_FILE"
fi

if [[ "$COVERAGE" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD --cov=. --cov-report=term-missing --cov-report=html"
fi

echo -e "${GREEN}Running tests...${NC}"
echo "Command: $PYTEST_CMD"
echo ""

# Run tests
eval $PYTEST_CMD
TEST_EXIT_CODE=$?

# Show coverage report location if generated
if [[ "$COVERAGE" == true ]] && [[ $TEST_EXIT_CODE -eq 0 ]]; then
    echo ""
    echo -e "${GREEN}Coverage report generated: htmlcov/index.html${NC}"
fi

# Exit with test result
if [[ $TEST_EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
else
    echo -e "${RED}✗ Some tests failed${NC}"
fi

exit $TEST_EXIT_CODE
