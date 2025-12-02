# Cloudfolio Shared Package

Shared utilities for Cloudfolio microservices architecture.

## Installation

### Local Development (Editable Mode)

```bash
# From repo root
cd apps
./setup-dev.sh
```

Or manually:

```bash
cd apps/shared
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Build Wheel (for Deployment)

```bash
cd apps/shared
pip install build
python -m build --wheel
# Wheel will be in dist/cloudfolio_shared-1.0.0-py3-none-any.whl
```

## Usage

```python
# Clean imports - no sys.path manipulation needed
from cloudfolio_shared import cache_manager, GitHubAPI, queue_manager
from cloudfolio_shared.ai import AIAssistant, RepoScoringService

# Use directly
api = GitHubAPI(token="...", username="yungryce")
repos = api.get_user_repos()
```

## Package Structure

```
apps/shared/
├── pyproject.toml              # Modern Python packaging
├── src/
│   └── cloudfolio_shared/      # Installable package
│       ├── __init__.py         # Lazy-loading exports
│       ├── ai/                 # AI/ML utilities
│       │   ├── ai_assistant.py
│       │   ├── repo_scoring_service.py
│       │   ├── type_analyzer.py
│       │   └── fine_tuning.py
│       ├── cache/              # Azure Blob storage
│       │   ├── cache_manager.py
│       │   └── fingerprint_manager.py
│       ├── github/             # GitHub API client
│       │   ├── github_api.py
│       │   └── github_repo_manager.py
│       ├── linguist/           # Language detection
│       │   └── languages.yml
│       ├── models/             # Pydantic schemas
│       │   └── schemas.py
│       └── queue/              # Azure Storage Queues
│           └── queue_manager.py
```

## Running Tests

```bash
# From apps directory
source tests/.venv/bin/activate
./tests/run_tests.sh -m unit  # Fast unit tests
./tests/run_tests.sh          # All tests (requires Azurite)
```

## Dependencies

Core dependencies (installed automatically):
- `azure-storage-blob`, `azure-storage-queue`, `azure-identity` - Azure SDK
- `requests` - GitHub API client
- `pyyaml` - Language detection
- `pydantic` - Data validation
- `openai`, `groq` - AI/ML inference

Optional dependencies:
- `[dev]` - Testing and linting tools
- `[ml]` - PyTorch, sentence-transformers for local model training
