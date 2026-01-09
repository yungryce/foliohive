# Cloudfolio API (v0.3.0)

v0.3.0 consolidates the backend into a single Azure Functions app (flex consumption) using blueprints:

- HTTP routes: API gateway blueprint
- Queue workers: sync + merge worker blueprints

## Local development

### Prerequisites

- Python 3.12–3.14
- Azure Functions Core Tools (`func`)
- Node.js + Angular CLI (`ng`) if you want to run the UI
- Azurite (`azurite`) for local Storage Queue/Blob/Table emulation

### Setup

```bash
cd api/v0.3.0
./setup-dev.sh
```

### Start Azurite

```bash
cd api/v0.3.0
./ensure-azurite.sh
```

### Start the Function App

Create a local settings file (dev-only):

```bash
cd api/v0.3.0/function-app
cp local.settings.example.json local.settings.json
# edit local.settings.json to set GITHUB_TOKEN
```

Run the Functions host:

```bash
cd api/v0.3.0
source .venv/bin/activate
cd function-app
func start --python --port 7071
```

Local base URL:

- `http://localhost:7071/api`

### Run tests

```bash
cd api/v0.3.0/tests
./run_tests.sh
```

## Key folders

- `function-app/` – consolidated Functions entrypoint
- `function-app/blueprints/` – API gateway + workers
- `shared/` – `cloudfolio_shared` package
- `tests/` – unit + integration tests
