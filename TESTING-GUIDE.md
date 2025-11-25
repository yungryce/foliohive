# Testing Guide: Cloudfolio Components

Comprehensive testing strategy for the distributed Cloudfolio architecture. **No single entry point exists for `apps/shared`** — it's an importable package, not a runnable service.

---

## Part 1: Understanding the Architecture

### What is `apps/shared`?

**It's a Python package, not an application.** Think of it like `node_modules` in npm or `vendor/` in PHP.

```
apps/shared/                    # Package (imported by other apps)
├── __init__.py               # Makes it a package
├── setup.py                  # Package metadata (for pip install)
├── cache/                    # Submodule: CacheManager, FingerprintManager
├── github/                   # Submodule: GitHubAPI, GitHubRepoManager
├── ai/                       # Submodule: AIAssistant, RepoScoringService
├── models/                   # Submodule: Pydantic schemas
├── queue/                    # Submodule: QueueManager
└── tests/                    # Unit tests (runs independently)

apps/api-gateway/             # Application (imports from shared)
├── function_app.py          # Entry point: from apps.shared import ...
└── requirements.txt         # Lists: portfolio-shared (installs from setup.py)

apps/sync-worker/             # Application (imports from shared)
apps/merge-worker/            # Application (imports from shared)
apps/training-worker/         # Application (imports from shared)
```

### Entry Points (Real Applications)

- **API Gateway**: `apps/api-gateway/function_app.py` (HTTP routes)
- **Sync Worker**: `apps/sync-worker/function_app.py` (queue trigger)
- **Merge Worker**: `apps/merge-worker/function_app.py` (queue trigger)
- **Training Worker**: `apps/training-worker/train_worker.py` (ACI container)

---

## Part 2: Testing `apps/shared` in Isolation

### Setup

```bash
cd /home/juk/cloudfolio/apps/shared

# 1. Install shared package in development mode
pip install -e .                    # Installs dependencies + makes package importable
pip install -r tests/requirements.txt  # Adds pytest, coverage, mocks
```

### Running Tests with `run_tests.sh`

The `tests/run_tests.sh` script is your main entry point:

```bash
# 1. Run all tests (full suite)
./tests/run_tests.sh -v -c

# 2. Run only unit tests (fast, mocked)
./tests/run_tests.sh -m unit -v

# 3. Run only integration tests (may call real GitHub/Azure)
./tests/run_tests.sh -m integration

# 4. Run specific test file
./tests/run_tests.sh -f test_cache_manager.py

# 5. Run tests matching keyword pattern
./tests/run_tests.sh -k "cache and not slow" -v

# 6. Exit on first failure (debugging)
./tests/run_tests.sh -x -v

# 7. Generate coverage report (HTML)
./tests/run_tests.sh -c
# Output: htmlcov/index.html (open in browser)
```

### Test Structure

```
apps/shared/
├── cache/
│   ├── __init__.py
│   ├── cache_manager.py
│   └── tests/
│       ├── __init__.py
│       └── test_cache_manager.py     ← Tests for CacheManager class
├── github/
│   ├── __init__.py
│   ├── github_api.py
│   └── tests/
│       ├── __init__.py
│       └── test_github_api.py        ← Tests for GitHubAPI class
└── tests/
    ├── conftest.py                   ← Shared fixtures (mock_azure_blob_client, etc.)
    ├── pytest.ini                    ← Test configuration
    ├── requirements.txt              ← Test dependencies
    └── run_tests.sh                  ← Test runner script
```

**Key insight**: Subdirectories have their own `tests/` folders, which pytest discovers automatically via `pytest.ini`:
```ini
testpaths = 
    tests
    cache/tests
    github/tests
    models/tests
    ai/tests
```

### Common Test Scenarios

#### Scenario 1: Test Cache Manager (Most Commonly Modified)

```bash
# Run all cache tests
./tests/run_tests.sh -f test_cache_manager.py -v

# Run only cache initialization tests
./tests/run_tests.sh -k "test_init" -v

# Expected output:
# tests/cache/tests/test_cache_manager.py::TestCacheManagerInitialization::test_init_with_defaults PASSED
# tests/cache/tests/test_cache_manager.py::TestCacheManagerInitialization::test_init_with_custom_params PASSED
# tests/cache/tests/test_cache_manager.py::TestCacheManagerGet::test_get_valid_cache_entry PASSED
# ... (more tests)
```

#### Scenario 2: Test GitHub API (External Calls)

```bash
# Run GitHub API tests with mocked requests
./tests/run_tests.sh -f test_github_api.py -v

# The `mock_github_response_success` fixture (from conftest.py) intercepts HTTP calls
# No real GitHub API calls are made during unit tests

# To test with real GitHub API (integration test):
# Mark test with @pytest.mark.integration
# Then run: ./tests/run_tests.sh -m integration
# (Requires GITHUB_TOKEN env var)
```

#### Scenario 3: Test with Coverage

```bash
./tests/run_tests.sh -c

# Generates:
# - Terminal output: coverage % per module
# - htmlcov/index.html: interactive coverage report
# 
# Example output:
# Name                                    Stmts   Miss  Cover
# ─────────────────────────────────────────────────────────
# apps/shared/cache/cache_manager.py       150     8    95%
# apps/shared/github/github_api.py          45     2    96%
# apps/shared/ai/ai_assistant.py            80    15    81%
# ─────────────────────────────────────────────────────────
# TOTAL                                    275    25    91%
```

---

## Part 3: Manual Testing of `apps/shared`

### Purpose

Unit tests cover "happy path" + error cases. Manual testing catches edge cases and verifies actual behavior.

### Setup (One-Time)

```bash
cd /home/juk/cloudfolio/apps/shared

# Python 3.11+, venv (optional but recommended)
python3 -m venv venv
source venv/bin/activate

# Install shared package + dependencies
pip install -e .
pip install ipython  # Optional: better REPL
```

### Manual Test 1: CacheManager

```python
# Start Python REPL
python3
```

```python
import os
from apps.shared.cache.cache_manager import CacheManager

# 1. Create cache instance
cache = CacheManager(use_cache=True)

# 2. Check initialization
print(f"Container: {cache.container_name}")
print(f"Cache enabled: {cache.use_cache}")
print(f"Initialized: {cache._initialized}")

# 3. Generate cache keys
key1 = CacheManager.generate_cache_key('bundle', 'yungryce')
print(f"Bundle key: {key1}")
# Expected: repos_bundle_context_yungryce

key2 = CacheManager.generate_cache_key('repo', 'yungryce', 'cloudfolio')
print(f"Repo key: {key2}")
# Expected: repo_context_yungryce_cloudfolio

key3 = CacheManager.generate_cache_key('model', 'abc123')
print(f"Model key: {key3}")
# Expected: model_abc123

# 4. Test with mock data (no real Azure call)
cache_data = {
    'username': 'testuser',
    'repositories': ['repo1', 'repo2'],
    'cached_at': '2025-01-15T12:00:00Z'
}

# Note: Without AZURE_STORAGE connection, actual set/get will fail
# This is expected in local dev. The tests mock Azure Blob Storage.
print(f"Sample data: {cache_data}")
```

Exit: `exit()` or Ctrl+D

### Manual Test 2: GitHubAPI

```python
from apps.shared.github.github_api import GitHubAPI

# 1. Create API client (needs GITHUB_TOKEN)
api = GitHubAPI(token='ghp_xyz...', username='yungryce')
# Or: api = GitHubAPI()  # Uses env var GITHUB_TOKEN

# 2. Test endpoint building
endpoint = f"users/{api.username}/repos"
print(f"Endpoint: {endpoint}")
# Expected: users/yungryce/repos

# 3. Test file content decoding
import base64
encoded_content = base64.b64encode(b"# Hello World").decode()
file_data = {'content': encoded_content, 'encoding': 'base64'}
decoded = api.decode_file_content(file_data)
print(f"Decoded: {decoded}")
# Expected: # Hello World
```

### Manual Test 3: FingerprintManager

```python
from apps.shared.cache.fingerprint_manager import FingerprintManager

# 1. Create manager
fp = FingerprintManager()

# 2. Generate fingerprint from file list
files = ['app.py', 'utils.py', 'config.yml']
fingerprint = fp.compute_fingerprint({'files': files})
print(f"Fingerprint: {fingerprint}")

# 3. Fingerprints are deterministic (same input = same hash)
fingerprint2 = fp.compute_fingerprint({'files': files})
assert fingerprint == fingerprint2
print("✓ Fingerprints are deterministic")

# 4. Change input = different fingerprint
files2 = ['app.py', 'utils.py', 'config.yml', 'secrets.env']
fingerprint3 = fp.compute_fingerprint({'files': files2})
assert fingerprint != fingerprint3
print("✓ Different input produces different fingerprint")
```

### Manual Test 4: Environment Variable Validation

```python
import os
os.environ['GITHUB_TOKEN'] = 'ghp_test123'
os.environ['BLOB_SERVICE_URI'] = 'https://test.blob.core.windows.net'

from apps.shared.config.settings import Settings

settings = Settings()
print(f"GitHub token: {settings.github_token[:10]}...")
print(f"Blob URI: {settings.blob_service_uri}")
print("✓ Settings loaded from env vars")
```

---

## Part 4: Testing Workers with Shared Package

### Sync Worker Test

```bash
cd /home/juk/cloudfolio/apps/sync-worker

# 1. Install dependencies (includes shared as local editable)
pip install -r requirements.txt

# 2. Run sync worker tests
pytest tests/ -v

# Expected: Tests verify queue polling, GitHub API calls, cache updates
```

**Test Structure:**
```python
# tests/test_function_app.py
from apps.shared.cache.cache_manager import CacheManager
from apps.shared.github.github_repo_manager import GitHubRepoManager

def test_sync_worker_processes_message(mock_queue_message):
    """Sync worker should:
    1. Receive message from github-sync queue
    2. Fetch repos using GitHubRepoManager
    3. Cache results with CacheManager
    4. Delete processed message
    """
    # Arrange
    message_body = {'username': 'testuser', 'job_id': 'xyz'}
    
    # Act
    result = sync_worker_main(message_body)
    
    # Assert
    assert result['status'] == 'completed'
    assert result['repos_processed'] > 0
```

### Merge Worker Test

```bash
cd /home/juk/cloudfolio/apps/merge-worker

pytest tests/ -v
# Expected: Tests verify merge logic, deduplication, final bundle creation
```

### Training Worker Test

```bash
cd /home/juk/cloudfolio/apps/training-worker

# Install GPU/Torch dependencies
pip install -r requirements.txt

pytest tests/ -v
# Expected: Tests verify model loading, fine-tuning, metrics
```

---

## Part 5: Integration Testing (Multi-Component)

### Level 1: Shared + Single Worker (In-Memory)

```bash
# Test without real Azure or queues
pytest apps/sync-worker/tests/ -m unit -v
```

**What's tested:**
- Sync worker imports from shared ✓
- GitHub API calls are mocked ✓
- Cache operations are mocked ✓
- Queue operations are mocked ✓

### Level 2: Shared + Worker + Real Azure (Optional)

```bash
# Requires:
# - GITHUB_TOKEN set
# - Azure Storage connection string set
# - Real network access

pytest apps/sync-worker/tests/ -m integration -v

# This actually:
# - Calls GitHub API (real)
# - Writes to Azure Blob Storage (real)
# - Reads from Azure Storage Queue (real)
```

**⚠️ Warning**: Integration tests are slow (~30s per test) and cost money (Azure operations).

### Level 3: Full End-to-End (Manual)

```bash
# 1. Start local Azure Storage emulator (Azurite)
docker run -p 10000:10000 -p 10001:10001 -p 10002:10002 mcr.microsoft.com/azure-storage/azurite

# 2. Start Function App locally
cd apps/api-gateway
func start

# 3. In another terminal, trigger a request
curl -X POST http://localhost:7071/api/bundles/yungryce/refresh

# 4. Observe:
# - API Gateway logs
# - Message sent to azure-sync queue
# - Sync worker processes (if running locally)
# - Cache updated in local storage
```

---

## Part 6: Testing Checklist by Component

### `apps/shared` (Package)

- [ ] Run: `./tests/run_tests.sh -c` (100% pass, >85% coverage)
- [ ] Check: `htmlcov/index.html` (view coverage gaps)
- [ ] Manual: `python3` REPL tests from Part 3
- [ ] No entry point needed; it's imported by workers

### `apps/api-gateway` (HTTP Triggers)

```bash
cd apps/api-gateway
pytest tests/ -v

# Verify:
# - POST /api/bundles/{username}/refresh → enqueues job ✓
# - GET /api/bundles/{username} → returns cached data ✓
# - POST /api/chat/{username} → sends to AI assistant ✓
# - GET /api/status/{job_id} → polls job status ✓
# - GET /api/health → 200 OK ✓
```

### `apps/sync-worker` (Queue Trigger)

```bash
cd apps/sync-worker
pytest tests/ -v

# Verify:
# - Receives message from `github-sync` queue ✓
# - Calls GitHub API ✓
# - Updates cache ✓
# - Marks message as processed ✓
```

### `apps/merge-worker` (Queue Trigger)

```bash
cd apps/merge-worker
pytest tests/ -v

# Verify:
# - Receives message from `merge-results` queue ✓
# - Merges individual repo bundles ✓
# - Deduplicates languages/tech stack ✓
# - Updates final bundle cache ✓
```

### `apps/training-worker` (Container)

```bash
cd apps/training-worker
pytest tests/ -v

# Verify:
# - Receives message from `model-training` queue ✓
# - Downloads training data ✓
# - Fine-tunes semantic model ✓
# - Saves model checkpoint ✓
# - Publishes metrics ✓
```

---

## Part 7: Debugging Tips

### Problem: Tests Pass Locally but Fail in CI/CD

**Cause**: Environment variables not set in CI

**Solution**: Check GitHub Actions secrets
```yaml
# .github/workflows/test.yml
env:
  GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  BLOB_SERVICE_URI: ${{ secrets.BLOB_SERVICE_URI }}
```

### Problem: "ImportError: No module named 'apps.shared'"

**Cause**: Shared package not installed

**Solution**:
```bash
cd apps/shared
pip install -e .
```

### Problem: "Azure authentication failed"

**Cause**: No credentials available (missing GITHUB_TOKEN, BLOB_SERVICE_URI)

**Solution**: For unit tests, use mocks (already in `conftest.py`). For integration tests, set env vars:
```bash
export GITHUB_TOKEN="ghp_..."
export BLOB_SERVICE_URI="https://test.blob.core.windows.net"
export AzureWebJobsStorage="DefaultEndpointsProtocol=..."
```

### Problem: Tests Hang

**Cause**: Waiting for real HTTP requests (no timeout)

**Solution**: 
```bash
# Run with timeout
timeout 10s pytest tests/ -v
# Or add timeout to pytest.ini:
# addopts = --timeout=10
```

---

## Part 8: Quick Reference

### Install & Test in 3 Steps

```bash
# Step 1: Install shared
cd /home/juk/cloudfolio/apps/shared
pip install -e .
pip install -r tests/requirements.txt

# Step 2: Run tests
./tests/run_tests.sh -c

# Step 3: View report
open htmlcov/index.html
```

### Test Each Worker in 3 Steps

```bash
# Step 1: Install worker + shared
cd /home/juk/cloudfolio/apps/api-gateway
pip install -r requirements.txt

# Step 2: Run tests
pytest tests/ -v

# Step 3: Check output
# Should see: PASSED or FAILED for each test
```

### Common Commands

| Command | Purpose |
|---------|---------|
| `./tests/run_tests.sh` | Run all tests |
| `./tests/run_tests.sh -m unit` | Fast tests (mocked) |
| `./tests/run_tests.sh -m integration` | Slow tests (real services) |
| `./tests/run_tests.sh -c` | Generate coverage report |
| `./tests/run_tests.sh -k "cache"` | Run tests matching keyword |
| `./tests/run_tests.sh -x -v` | Verbose, exit on first failure |
| `pytest tests/ --pdb` | Drop into debugger on failure |

---

## Next Steps

1. **Start with**: `cd apps/shared && ./tests/run_tests.sh -c`
2. **Then test**: Each worker individually (`cd apps/api-gateway && pytest tests/`)
3. **Finally**: Integration test (queue + multiple workers together)

**Questions?** Check test files for examples: `apps/shared/cache/tests/test_cache_manager.py`
