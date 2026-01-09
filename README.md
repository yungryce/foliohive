# Cloudfolio

Cloud-native personal portfolio platform that ingests GitHub activity, enriches it with AI, and publishes curated bundles via an Angular front end and Azure Functions microservices.

## Repository Layout

| Path | Description |
|------|-------------|
| `api/v0.3.0/` | Backend (Azure Functions) + shared Python package + centralized tests. |
| `api/v0.3.0/function-app/` | Single Azure Functions app hosting HTTP routes + queue workers via blueprints. |
| `api/v0.3.0/function-app/blueprints/` | API gateway + workers (sync/merge) as blueprints. |
| `api/v0.3.0/training-worker/` | Containerized training job + models (not an Azure Function). |
| `api/v0.3.0/shared/` | Shared Python package used by the Function App (`cloudfolio_shared`). |
| `api/v0.3.0/tests/` | Central pytest config + integration/e2e harness. |
| `ui/` | Angular SPA (deployed to Azure Static Web Apps). |
| `infra/` | Infrastructure-as-code (Bicep + Terraform). |
| `.ado/` | Azure Pipelines definitions and templates. |

## High-Level Architecture

1. **Function App (v0.3.0)** – A single Azure Functions app hosting:
	- HTTP API routes (gateway)
	- Queue-triggered workers (sync + merge)
4. **Training Worker** – Builds/updates semantic models used by the assistant.
5. **Angular UI** – Reads cached bundles and renders projects/skills + assistant UI.

Shared logic lives under `api/v0.3.0/shared/` and is imported by the Function App blueprints.

## Quick Start (Local Dev)

The simplest way to run the full local stack (Azurite + 3 Function Apps + UI) is:

```bash
./run-dev-session.sh
```

Common options:

```bash
./run-dev-session.sh --no-ui
./run-dev-session.sh --run-e2e
```

## Backend Setup & Tests (v0.3.0)

Create the backend virtualenv and install all backend deps (shared + workers):

```bash
cd api/v0.3.0
./setup-dev.sh
```

Run the backend tests:

```bash
cd api/v0.3.0/tests
./run_tests.sh
```

## UI Setup

```bash
cd ui
npm install
npm start
```

## Deployment Notes

- Azure Functions and the UI deploy via Azure Pipelines (see `.ado/`).
- Infrastructure is under `infra/bicep/` and `infra/terraform/`.

If you’re updating CI/CD docs, start with [.ado/README.md](.ado/README.md).
