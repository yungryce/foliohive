# foliohive Shared Package (v0.3.0)

Shared Python utilities for foliohive services.

The package lives under `api/v0.3.0/shared/` and is imported by Function Apps and other backend components as `foliohive_shared`.

## Local development

The backend setup script creates a single virtualenv for the backend and installs shared + workers:

```bash
cd api/v0.3.0
./setup-dev.sh
```

If you want to work on *only* the shared package in editable mode:

```bash
cd api/v0.3.0/shared
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```python
from foliohive_shared.github.github_api import GitHubAPI

api = GitHubAPI(token="...", username="yungryce")
repos = api.get_user_repos()
```

## Package structure

```
api/v0.3.0/shared/
├── pyproject.toml
├── src/
│   └── foliohive_shared/
└── README.md
```

## Tests

Backend tests (unit + integration) are centralized under `api/v0.3.0/tests/`:

```bash
cd api/v0.3.0/tests
./run_tests.sh
```
# foliohive Shared Package
