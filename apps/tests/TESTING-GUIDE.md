# Testing Guide: Cloudfolio Components

Centralized instructions for executing unit, integration, and manual tests across the Cloudfolio monorepo. Every runnable surface ultimately depends on the `apps/shared` package, so the testing workflow now lives beside the applications in `apps/tests`.

---

## 1. Architecture & Test Surfaces

- **`apps/shared`** – importable package that provides cache, GitHub, AI, and schema utilities. Ships no entry point; it is exercised through the other apps and has targeted tests for each submodule.
- **Function Apps** – `api-gateway`, `sync-worker`, `merge-worker`, and `training-worker` live under `apps/` and each import from `apps.shared`.
- **Cross-App flows** – queue-driven orchestration plus end-to-end scenarios (e.g., API Gateway → Sync Worker → Cache write).

| Surface | Example entry point | Typical marker |
|---------|--------------------|----------------|
| Shared package modules | `apps/shared/cache/cache_manager.py` | `unit` |
| Function apps | `apps/api-gateway/function_app.py` | `unit`, `integration` |
| End-to-end orchestration | `apps/tests/integration/test_e2e_flow.py` (placeholder) | `integration`, `slow` |

---

## 2. Central Test Harness (`apps/tests`)

```
apps/tests/
├── conftest.py        # Repo-wide fixtures, env defaults, Azurite wiring
├── pytest.ini         # Test discovery for shared modules + every function app
├── requirements.txt   # Unified testing dependencies
├── run_tests.sh       # Single entry point for all suites
└── integration/       # E2E scenarios (currently scaffolding)
```

### Quick Start

```bash
cd /home/juk/cloudfolio

# Install/refresh test dependencies once
python -m pip install -r apps/tests/requirements.txt

# Run everything with coverage
./apps/tests/run_tests.sh -c
```

The runner automatically:
1. Adds `repo_root` and `apps/` to `PYTHONPATH` so imports like `from apps.shared ...` succeed.
2. Sets safe defaults for `GITHUB_TOKEN`, `AzureWebJobsStorage`, `GROQ_API_KEY`, etc., via the autouse fixture in `conftest.py`.
3. Checks for Azurite; if absent, it attempts to start a local instance (needed for any queue/blob interaction).
4. Invokes `pytest` with `apps/tests/pytest.ini`, which enumerates all suite locations.

### Runner Flags

| Flag | Description |
|------|-------------|
| `-m/--marker unit|integration|slow` | Filter by pytest marker. `unit` skips external calls, `integration` enables Azurite + optional live services. |
| `-k/--keyword "expr"` | Pytest keyword expression (e.g., `"cache and not slow"`). |
| `-c/--coverage` | Emits terminal + HTML coverage (`apps/htmlcov/index.html`). |
| `-v/--verbose` or `-q/--quiet` | Control verbosity (`-vv` or `-q`). |
| `-x/--exitfirst` | Fail fast, handy for TDD. |
| positional path | Limit run to a directory or file (e.g., `../api-gateway/tests`). |

> Tip: Because the script `pushd`'s into `apps/`, any relative paths you pass are resolved from there. Example: `./apps/tests/run_tests.sh ../shared/github/tests -k "decode"`.

---

## 3. Running Targeted Suites

### 3.1 Shared Package Modules

| Goal | Command |
|------|---------|
| All shared tests | `./apps/tests/run_tests.sh ../shared` |
| Cache-only | `./apps/tests/run_tests.sh ../shared/cache/tests` |
| GitHub API | `./apps/tests/run_tests.sh ../shared/github/tests -k github_api` |
| AI helpers | `./apps/tests/run_tests.sh ../shared/ai/tests -m unit` |

The fixtures in `apps/tests/conftest.py` provide:
- Default Azurite connection string + credentials.
- `mock_azure_blob_client`, `mock_default_credential`, `sample_repo_metadata`, etc.
- Environment patching via `mock_env_vars` for tests that rely on `Settings`.

### 3.2 Function Apps

All application suites are registered inside `pytest.ini`, so you do not need bespoke configs in each folder. Examples:

```bash
# API Gateway
./apps/tests/run_tests.sh ../api-gateway/tests -m unit

# Sync worker queue trigger
./apps/tests/run_tests.sh ../sync-worker/tests

# Merge worker slow merge scenarios
./apps/tests/run_tests.sh ../merge-worker/tests -k "merge" -v

# Training worker CPU-only smoke
./apps/tests/run_tests.sh ../training-worker/tests -m unit
```

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
