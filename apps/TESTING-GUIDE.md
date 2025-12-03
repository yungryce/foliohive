# Testing Guide: Cloudfolio Multi-App Architecture

Complete guide for testing the Cloudfolio microservices using a consolidated virtual environment, modern `pyproject.toml` packaging, and clean imports.

---

## Quick Start (30 seconds)

```bash
cd apps

# One-time setup: creates consolidated venv with all dependencies
./setup-dev.sh

# Run all tests
source .venv/bin/activate
./tests/run_tests.sh -m unit  # Fast unit tests
./tests/run_tests.sh          # All tests (requires Azurite)
```

---

## Architecture Overview

### Package Structure

```
apps/
├── setup-dev.sh                  # Setup script: creates consolidated venv
├── .venv/                        # Single consolidated venv (all deps)
├── .gitignore                    # Ignores .venv/, __pycache__, etc.
│
├── shared/
│   ├── pyproject.toml            # Modern PEP 517/518 packaging (replaces setup.py)
│   ├── README.md                 # Package documentation
│   └── src/
│       └── cloudfolio_shared/    # Installable package
│           ├── __init__.py       # Lazy-loading exports
│           ├── ai/
│           ├── cache/
│           ├── github/
│           ├── linguist/
│           ├── models/
│           └── queue/
│
├── api-gateway/
│   ├── function_app.py           # Clean: from cloudfolio_shared import ...
│   ├── requirements.txt          # App-specific requirements
│   └── tests/
│
├── sync-worker/
│   ├── function_app.py
│   ├── requirements.txt
│   └── tests/
│
├── merge-worker/
│   ├── function_app.py
│   ├── requirements.txt
│   └── tests/
│
└── tests/
    ├── conftest.py               # Global fixtures, Azurite setup
    ├── pytest.ini                # Updated test discovery paths
    ├── requirements.txt          # Test-only dependencies
    ├── run_tests.sh              # Central test runner
    └── integration/
        └── test_e2e_flow.py
```

### Key Changes

| Aspect | Before | After |
|--------|--------|-------|
| **Packaging** | `setup.py` in root | `pyproject.toml` in `apps/shared/` |
| **Virtual envs** | Per-app `.venv` in each folder | Single consolidated `apps/.venv` |
| **Import paths** | `sys.path.append()` + `from apps.shared...` | Clean `from cloudfolio_shared import ...` |
| **Module location** | `apps/shared/ai/`, `apps/shared/cache/` | `apps/shared/src/cloudfolio_shared/ai/` |
| **Local dev setup** | Manual venv creation | `./setup-dev.sh` (one command) |
| **Installation** | `pip install -e ../shared` | Automatic via setup script |
| **Test discovery** | `sys.path` manipulation in conftest | Installed package + pytest.ini paths |

---

## 1. Initial Setup

### One-Time Environment Setup

```bash
cd /home/juk/DEV/cloudfolio/apps

# Create isolated venvs for shared + each function app + tests
./setup-dev.sh

# Options:
# ./setup-dev.sh --clean        # Clean reinstall
# ./setup-dev.sh --shared-only  # Only setup shared package
# ./setup-dev.sh --app api-gateway  # Only setup specific app
```

### What `setup-dev.sh` Does

1. **Creates consolidated venv**: Single `.venv` in `apps/` containing all dependencies
2. **Installs shared package**: Installs `cloudfolio-shared[dev]` in editable mode
3. **Installs function app requirements**: Sequentially installs requirements from each app
4. **Installs test dependencies**: Installs test-specific requirements

### Activate Environment

```bash
# Single consolidated environment for all development
cd apps
source .venv/bin/activate
```

---

## 2. Running Tests

### Central Test Runner

The `apps/tests/run_tests.sh` script provides unified test execution for all components:

```bash
# From any directory
cd apps && source .venv/bin/activate

# Run entire suite
./tests/run_tests.sh

# Common commands
./tests/run_tests.sh -m unit              # Fast unit tests only
./tests/run_tests.sh -m integration       # Integration tests (requires Azurite)
./tests/run_tests.sh -c -v                # Verbose with coverage report
./tests/run_tests.sh -k "cache"           # Tests matching keyword "cache"
./tests/run_tests.sh ../shared            # Only shared package tests
./tests/run_tests.sh ../api-gateway/tests # Only API gateway tests
```

### Runner Options

| Flag | Description |
|------|-------------|
| `-m, --marker MARKER` | Filter by pytest marker: `unit`, `integration`, `slow` |
| `-k, --keyword EXPR` | Pytest keyword expression (e.g., `"cache and not slow"`) |
| `-c, --coverage` | Generate coverage report (HTML in `apps/htmlcov/`) |
| `-v, --verbose` | Verbose output (`-vv` for extra verbosity) |
| `-q, --quiet` | Quiet mode (`-q`) |
| `-x, --exitfirst` | Stop on first failure |
| `PATH` | Run specific test directory or file |

### Examples

```bash
# Unit tests only (no external services)
./tests/run_tests.sh -m unit

# Cache module tests
./tests/run_tests.sh ../shared/src/cloudfolio_shared/cache/tests

# API gateway with coverage
./tests/run_tests.sh ../api-gateway/tests -c -v

# Single test file
./tests/run_tests.sh ../sync-worker/tests/test_function_app.py -k "deserialize"

# Fast fail on first error
./tests/run_tests.sh -x
```

---

## 3. Test Surfaces

### 3.1 Shared Package (cloudfolio_shared)

Located in `apps/shared/src/cloudfolio_shared/`:

```
cloudfolio_shared/
├── ai/                        # AI/ML utilities
│   ├── ai_assistant.py
│   ├── repo_scoring_service.py
│   ├── type_analyzer.py
│   ├── fine_tuning.py
│   └── tests/test_*.py
├── cache/                     # Azure Blob storage
│   ├── cache_manager.py
│   ├── fingerprint_manager.py
│   └── tests/test_*.py
├── github/                    # GitHub API client
│   ├── github_api.py
│   ├── github_repo_manager.py
│   └── tests/test_*.py
├── queue/                     # Azure Storage Queues
│   ├── queue_manager.py
│   └── (tests/)
├── models/                    # Pydantic schemas
│   ├── schemas.py
│   └── tests/test_schemas.py
└── linguist/
    ├── __init__.py
    └── languages.yml
```

| Module | Test Command | Notes |
|--------|------|-------|
| **cache** | `./tests/run_tests.sh ../shared/src/cloudfolio_shared/cache/tests` | Mock Azure storage, fingerprinting |
| **github** | `./tests/run_tests.sh ../shared/src/cloudfolio_shared/github/tests` | Mock GitHub API with `responses` |
| **ai** | `./tests/run_tests.sh ../shared/src/cloudfolio_shared/ai/tests -m unit` | Type analysis, scoring (unit tests avoid heavy ML) |
| **models** | `./tests/run_tests.sh ../shared/src/cloudfolio_shared/models/tests` | Pydantic schema validation |

### 3.2 Function Apps

Each function app has isolated tests in `apps/{app}/tests/`:

```bash
# API Gateway (REST endpoints, queue orchestration)
./tests/run_tests.sh ../api-gateway/tests

# Sync Worker (GitHub data fetching, caching)
./tests/run_tests.sh ../sync-worker/tests

# Merge Worker (bundle consolidation, job enqueuing)
./tests/run_tests.sh ../merge-worker/tests
```

### 3.3 Integration Tests

End-to-end workflows testing multiple components:

```bash
./tests/run_tests.sh integration -m integration
```

---

## 4. Understanding the Test Environment

### conftest.py Setup

The `apps/tests/conftest.py` provides:

1. **Session-level fixture** (`configure_test_environment`):
   - Sets Azurite connection string for local Azure Storage emulation
   - Sets `GITHUB_TOKEN`, `GROQ_API_KEY`, `ENABLE_QUEUE_MODE` env vars
   - Runs automatically for all tests

2. **Reusable fixtures**:
   - `mock_azure_blob_client` - Mock BlobServiceClient
   - `mock_default_credential` - Mock Azure credential
   - `sample_repo_metadata` - Example GitHub repo metadata
   - `sample_repos_bundle` - Example repository bundle
   - `temp_linguist_file` - Temporary linguist languages.yml
   - `mock_env_vars` - Environment variable patching

3. **Clean imports**:
   - No `sys.path` manipulation needed
   - All modules imported via installed `cloudfolio_shared` package

### pytest.ini Configuration

The `apps/tests/pytest.ini` configures:

```ini
[pytest]
minversion = 8.0
testpaths =
    ../shared/src/cloudfolio_shared/cache/tests
    ../shared/src/cloudfolio_shared/github/tests
    ../shared/src/cloudfolio_shared/ai/tests
    ../shared/src/cloudfolio_shared/models/tests
    ../api-gateway/tests
    ../sync-worker/tests
    ../merge-worker/tests
    ../training-worker/tests
    integration

markers =
    unit: Fast isolated tests with mocked dependencies
    integration: Tests that require Azurite or external services
    slow: Long-running scenarios (ML model loading, etc.)
```

### Azurite (Azure Storage Emulator)

The `run_tests.sh` script automatically:

1. Checks if Azurite is running on `http://127.0.0.1:10000`
2. If not, attempts to start it via `azurite --location ./.azurite --silent`
3. Uses connection string: `DefaultEndpointsProtocol=http;...` pointing to local emulator

**Install Azurite** (if needed):
```bash
npm install -g azurite
```

---

## 5. Test Markers

Tests are organized by marker for selective execution:

### `@pytest.mark.unit`
Fast, isolated tests with all external dependencies mocked.
- **No network calls** - GitHub API mocked
- **No Azurite** - Storage mocked
- **Runs in seconds**

```bash
./tests/run_tests.sh -m unit
```

### `@pytest.mark.integration`
Tests requiring real Azure Storage Emulator (Azurite) and/or external services.
- **Azurite required** - Real queue/blob operations
- **Optional live services** - GitHub API (optional, often mocked)
- **Runs in 10-30 seconds**

```bash
./tests/run_tests.sh -m integration
```

### `@pytest.mark.slow`
Long-running tests (ML model loading, heavy computations).
- **Runs in minutes**
- Excluded from CI by default

```bash
./tests/run_tests.sh -m slow
```

---

## 6. Common Workflows

### Local Development: Fast Feedback Loop

```bash
cd apps
source tests/.venv/bin/activate

# Edit code in apps/api-gateway/
# Run only affected tests
./tests/run_tests.sh ../api-gateway/tests -m unit -x

# Or watch for changes (requires pytest-watch)
ptw ../api-gateway/tests -- -m unit
```

### Before Committing

```bash
cd apps
source tests/.venv/bin/activate

# Run full suite with coverage
./tests/run_tests.sh -c

# Check coverage report
open htmlcov/index.html
```

### Debugging a Failing Test

```bash
cd apps
source tests/.venv/bin/activate

# Run with verbose output and stop on first failure
./tests/run_tests.sh -vv -x -k "test_name_of_failing_test"
```

### Testing a Single Module

```bash
# Test only cache manager
./tests/run_tests.sh ../shared/src/cloudfolio_shared/cache/tests/test_cache_manager.py -v

# Test only sync-worker function app
./tests/run_tests.sh ../sync-worker/tests -v

# Test specific function
./tests/run_tests.sh ../merge-worker/tests -k "test_process_merge_payload" -vv
```

---

## 7. Clean Imports in Tests

With the new structure, tests use clean imports:

### Before (Old sys.path approach)
```python
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2]))
from apps.shared.cache.cache_manager import CacheManager
```

### After (New installed package)
```python
from cloudfolio_shared import CacheManager
# or
from cloudfolio_shared.cache import CacheManager
```

### Example Test Files

**`apps/shared/src/cloudfolio_shared/models/tests/test_schemas.py`**
```python
from cloudfolio_shared.models.schemas import SyncJobMessage, MergeJobMessage
```

**`apps/sync-worker/tests/test_function_app.py`**
```python
from cloudfolio_shared import (
    cache_manager,
    GitHubAPI,
    GitHubRepoManager,
    FileTypeAnalyzer,
)
```

**`apps/api-gateway/tests/test_function_app.py`**
```python
from cloudfolio_shared import (
    cache_manager,
    queue_manager,
    AIAssistant,
    RepoScoringService,
)
```

---

## 8. Packaging & Deployment

### Build Wheel for Production

```bash
cd apps/shared

# Install build tools
pip install build

# Build wheel
python -m build --wheel

# Output: dist/cloudfolio_shared-1.0.0-py3-none-any.whl
```

### Azure Function App Deployment

For Azure deployment, include the wheel in each function app's deployment package:

```bash
# In CI/CD pipeline:
1. Build wheel: python -m build --wheel
2. Copy wheel to each app: cp dist/cloudfolio_shared-*.whl ../api-gateway/.python_packages/
3. Deploy function apps
```

Each function app's `requirements.txt` will reference the wheel:
```
azure-functions>=1.17.0
# In .python_packages/:
cloudfolio_shared-1.0.0-py3-none-any.whl
```

---

## 9. Troubleshooting

### `ImportError: No module named 'cloudfolio_shared'`

**Solution**: Run `./setup-dev.sh` to install the shared package in editable mode.

```bash
cd apps && ./setup-dev.sh
source tests/.venv/bin/activate
```

### Azurite Connection Issues

**Error**: `BlockBlobService: unable to connect to Azure Storage`

**Solution**: Start Azurite manually or let the script auto-start it:

```bash
# Manual start
npm install -g azurite
azurite --location ./.azurite --silent &

# Or let run_tests.sh start it automatically
./tests/run_tests.sh -m integration
```

### Tests Pass Locally but Fail in CI

**Likely cause**: Different Python version or missing dependencies.

**Solution**:
```bash
# Ensure Python 3.11+
python3 --version

# Reinstall with clean venv
./setup-dev.sh --clean

# Run tests
source tests/.venv/bin/activate
./tests/run_tests.sh
```

### Coverage Report Empty

**Solution**: Ensure tests actually ran:

```bash
./tests/run_tests.sh -c -v
# Should show: "Coverage report available at apps/htmlcov/index.html"
open htmlcov/index.html
```

---

## 10. CI/CD Integration

### GitHub Actions Example

```yaml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Setup development environment
        working-directory: apps
        run: ./setup-dev.sh --no-dev
      
      - name: Run tests
        working-directory: apps
        run: |
          source tests/.venv/bin/activate
          ./tests/run_tests.sh -m unit
```

---

## Summary

| Task | Command |
|------|---------|
| Initial setup | `cd apps && ./setup-dev.sh` |
| Activate test environment | `source apps/tests/.venv/bin/activate` |
| Run all tests | `./tests/run_tests.sh` |
| Run unit tests only | `./tests/run_tests.sh -m unit` |
| Run with coverage | `./tests/run_tests.sh -c` |
| Test specific module | `./tests/run_tests.sh ../shared/src/cloudfolio_shared/cache/tests` |
| Build wheel for deployment | `cd apps/shared && python -m build --wheel` |


If you still prefer invoking pytest from within an app directory, you can, but keep in mind that you will lose the shared fixtures unless you import `apps/tests/conftest.py`. Sticking with the centralized runner avoids drift.

### 3.3 Integration & End-to-End

- `apps/tests/integration/test_e2e_flow.py` currently serves as a scaffold—add real API↔queue↔cache assertions there.
- Mark end‑to‑end coverage with both `@pytest.mark.integration` and `@pytest.mark.slow`, then execute:

```bash
./apps/tests/run_tests.sh -m "integration and slow" apps/tests/integration
```

- The runner ensures Azurite is reachable before these suites start. If you want to exercise real Azure resources (storage account, queues, functions), export the appropriate connection strings **before** running the script so they override the defaults.

---

## 4. Coverage & Reporting

- Append `-c/--coverage` to any invocation to collect repo-wide coverage. Example:

```bash
./apps/tests/run_tests.sh -c ../shared ../api-gateway/tests
```

- Reports:
    - Terminal summary with missing-line hints (`--cov-report=term-missing`).
    - HTML dashboard at `apps/htmlcov/index.html` (open in a browser or `python -m http.server` from `apps/`).
- Coverage scope defaults to `--cov=apps`, so adding new modules under `apps/` automatically contributes.

---

## 5. Local Environment Expectations

- **Azurite** – Required for any test touching queues or blobs. The runner auto-starts it if the CLI is installed globally (`npm install -g azurite`). Pre-created data lives in `./.azurite` at the repo root.
- **Environment variables** – `configure_test_environment()` in `conftest.py` seeds the most common secrets with deterministic values. Override them in your shell when validating real integrations (e.g., `export GITHUB_TOKEN=...`).
- **Python path** – Both the repository root and `apps/` are injected into `sys.path`, so `from apps.shared...` works regardless of where the test file lives.
- **Dependencies** – `apps/tests/requirements.txt` is the single source of truth for pytest plugins, Azure SDK clients, and tooling (black, ruff, mypy). The runner bootstraps it if `pytest` is missing, but installing once up front is faster.

---

## 6. Manual Verification Patterns

Unit tests catch regressions, but interactive checks help when designing new behavior.

### CacheManager Smoke Test

```python
python3 -q <<'PY'
from apps.shared.cache.cache_manager import CacheManager

cache = CacheManager(use_cache=True)
print("container", cache.container_name)
print("key", CacheManager.generate_cache_key('bundle', 'yungryce'))

sample = {'username': 'testuser', 'repos': 2}
print("payload", sample)
PY
```

### GitHubAPI Decoder

```python
python3 -q <<'PY'
from apps.shared.github.github_api import GitHubAPI
import base64

api = GitHubAPI(token='ghp_test123', username='yungryce')
encoded = base64.b64encode(b"hello world!").decode()
print(api.decode_file_content({'content': encoded, 'encoding': 'base64'}))
PY
```

### Fingerprint Determinism

```python
python3 -q <<'PY'
from apps.shared.cache.fingerprint_manager import FingerprintManager

fp = FingerprintManager()
files = ['app.py', 'utils.py', 'config.yml']
print(fp.compute_fingerprint({'files': files}))
print(fp.compute_fingerprint({'files': files}))  # repeatable
PY
```

These quick scripts reuse the same modules/fixtures as the automated suite, so discrepancies usually signal a missing fixture or env var in tests.

---

## 7. Troubleshooting & CI Notes

- **Missing imports** – Ensure tests run through `./apps/tests/run_tests.sh` or pass `-c apps/tests/pytest.ini` when calling `pytest` manually.
- **Credential failures** – Unit tests always mock Azure identity; if you see `DefaultAzureCredential` errors, confirm you did not disable the `mock_default_credential` fixture.
- **Azurite not found** – Install it globally (`npm install -g azurite`) or run the Docker image (`mcr.microsoft.com/azure-storage/azurite`). The runner aborts with a clear message if it cannot probe `http://127.0.0.1:10000/`.
- **CI configuration** – Point your pipeline at the same script:

```yaml
- name: Run Cloudfolio tests
    run: |
        python -m pip install -r apps/tests/requirements.txt
        ./apps/tests/run_tests.sh -c -m "not slow"
```

- **Slow suites** – Mark with `@pytest.mark.slow` so engineers and CI can exclude them (`-m "not slow"`).

---

## 8. Checklist

- [ ] `./apps/tests/run_tests.sh -c` succeeds locally.
- [ ] Component-specific suites (API Gateway, Sync Worker, Merge Worker, Training Worker) pass when targeted via positional arguments.
- [ ] Integration tests tagged `integration` run against Azurite before hitting real Azure resources.
- [ ] Coverage report reviewed; new modules include regression tests.

Once those boxes are checked, you have a consistent signal that `apps/shared` and every consuming app remain in sync.
