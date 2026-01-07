# Cloudfolio Shared Package

Shared Python utilities for Cloudfolio services.

The package lives under `api/v0.2.0/shared/` and is imported by Function Apps and other backend components as `cloudfolio_shared`.

## Local development

The backend setup script creates a single virtualenv for the backend and installs shared + workers:

```bash
cd api/v0.2.0
./setup-dev.sh
```

If you want to work on *only* the shared package in editable mode:

```bash
cd api/v0.2.0/shared
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```python
from cloudfolio_shared.github.github_api import GitHubAPI

api = GitHubAPI(token="...", username="yungryce")
repos = api.get_user_repos()
```

## Package structure

```
api/v0.2.0/shared/
├── pyproject.toml
├── src/
│   └── cloudfolio_shared/
└── README.md
```

## Tests

Backend tests (unit + integration) are centralized under `api/v0.2.0/tests/`:

```bash
cd api/v0.2.0/tests
./run_tests.sh
```
# Cloudfolio Shared Package
