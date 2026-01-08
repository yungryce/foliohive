# Cloudfolio

Cloud-native personal portfolio platform that ingests GitHub activity, enriches it with AI, and publishes curated bundles via an Angular front end and Azure Functions microservices.

## Repository Layout

| Path | Description |
|------|-------------|
| `api/v0.2.0/` | Backend (Azure Functions) + shared Python package + backend tests. |
| `api/v0.2.0/api-gateway/` | HTTP API gateway Function App. |
| `api/v0.2.0/sync-worker/` | Queue-trigger worker that syncs GitHub data into storage. |
| `api/v0.2.0/merge-worker/` | Queue-trigger worker that merges/dedupes bundles for the UI. |
| `api/v0.2.0/training-worker/` | Containerized training job + models (not an Azure Function). |
| `api/v0.2.0/shared/` | Shared Python package used by all backend components (`cloudfolio_shared`). |
| `api/v0.2.0/tests/` | Central pytest config + integration/e2e harness. |
| `ui/` | Angular SPA (deployed to Azure Static Web Apps). |
| `infra/` | Infrastructure-as-code (Bicep + Terraform). |
| `.ado/` | Azure Pipelines definitions and templates. |

## High-Level Architecture

1. **API Gateway** – HTTP-triggered Azure Function that exposes endpoints for refresh/retrieval and the assistant UX. It enqueues work for downstream workers.
2. **Sync Worker** – Queue-triggered Function that ingests GitHub metadata and writes normalized bundles to Azure Storage.
3. **Merge Worker** – Queue-triggered Function that aggregates bundles and prepares final payloads for the UI.
4. **Training Worker** – Builds/updates semantic models used by the assistant.
5. **Angular UI** – Reads cached bundles and renders projects/skills + assistant UI.

Shared logic lives under `api/v0.3.0/shared/` and is imported by all backend services.

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

## Backend Setup & Tests

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
