# Cloudfolio

Cloud-native personal portfolio platform that ingests GitHub activity, enriches it with AI, and publishes curated bundles via an Angular front end and Azure Functions microservices. This repository is a monorepo that houses every component: infrastructure-as-code, backend workers, and the web experience.

---

## Repository Layout

| Path | Description |
|------|-------------|
| `api/v...` | All backend Function Apps (`api-gateway`, `sync-worker`, `merge-worker`, `training-worker`) plus the shared Python package consumed by every service. |
| `api/v.../shared/` | Reusable modules for cache, GitHub integrations, AI helpers, and Pydantic schemas. Packaged as `apps.shared`. |
| `api/v.../tests/` | Central pytest configuration, fixtures, and runner script for every backend component (unit + integration suites). |
| `portfolio/` | Angular SPA that surfaces skills, projects, and the AI assistant. Includes its own Azure Static Web Apps assets and pipelines. |
| `infra/terraform/` | Terraform definitions used for lower-level Azure resources (storage, queues, etc.). |
| `portfolio/infra/` | Bicep templates + deployment docs for the portfolio-hosting stack. |

---

## High-Level Architecture

1. **API Gateway** – HTTP-triggered Azure Function that exposes REST endpoints for refreshing bundles, retrieving cached data, and interacting with the AI assistant. Enqueues work for downstream workers.
2. **Sync Worker** – Queue-triggered Function that clones GitHub metadata, normalizes repositories, and writes annotated bundles to Azure Storage.
3. **Merge Worker** – Aggregates bundles, deduplicates technology tags, and prepares final payloads for the UI.
4. **Training Worker** – Containerized job that fine-tunes semantic models used by the assistant.
5. **Angular Portfolio** – Static web app that reads the cached bundles, renders projects/skills, and hosts the assistant UX.

Shared logic lives under `api/v.../shared` and is imported by all workers. Tests are coordinated via `api/v.../tests` so fixtures, environment variables, and Azurite setup stay consistent.

---

## Quick Start

```bash
# Clone and install base tooling
cd cloudfolio
python -m venv .venv && source .venv/bin/activate
python -m pip install -r apps/tests/requirements.txt
npm install -g azurite

# Run the full backend test suite with coverage
./apps/tests/run_tests.sh -c

# Install Angular dependencies and start the dev server
cd portfolio
npm install
npm run start
```

Additional commands:

| Purpose | Command |
|---------|---------|
| Target a specific backend suite | `./apps/tests/run_tests.sh ../api/v.../api-gateway/tests -m unit` |
| Run integration/E2E tests | `./apps/tests/run_tests.sh -m "integration" apps/tests/integration` |
| Start Azurite manually | `azurite --silent --location .azurite` |
| Terraform init (infra/terraform) | `cd infra/terraform && terraform init -upgrade` |

---

## Testing Strategy

- **Central runner**: `apps/tests/run_tests.sh` wraps `pytest` for every backend module, injects repo paths, seeds environment variables, and ensures Azurite is available.
- **Markers**: use `@pytest.mark.unit` for fast mocked scenarios, `@pytest.mark.integration` for Azurite/remote calls, and `@pytest.mark.slow` for long-lived jobs.
- **Coverage**: add `-c` to collect HTML + terminal coverage across `apps/`.
- **Frontend**: run the Angular spec suite via `npm run test` inside `portfolio/`.

See `TESTING-GUIDE.md` for a deep dive into the suite layout and manual verification tips.

---

## Deployment Notes

- Terraform definitions under `infra/terraform/` manage shared Azure resources.
- The Angular app uses Azure Static Web Apps (see `portfolio/staticwebapp.config.json` and `portfolio/azure-pipelines-artifact.yml`).
- Function Apps deploy through Azure Pipelines defined at the repo root of each service; environment variables are sourced from Azure Key Vault where possible.

Before deploying, ensure your Azure subscription is set, run `terraform plan/apply` for foundational resources, and verify that storage connection strings plus GitHub tokens are available in your deployment environment.

---

## Contribution Workflow

1. **Branching** – Create a feature branch from `pilot`.
2. **Install deps** – Use the virtual environment + `apps/tests/requirements.txt` for backend, and `npm install` under `portfolio/` for frontend.
3. **Testing** – Run `./apps/tests/run_tests.sh -c -m "not slow"` before raising a PR. Include frontend tests if UI changes are present.
4. **Linting/formatting** – Use `ruff` + `black` (Python) and `npm run lint` (Angular). Suggested Python command: `ruff check apps && black apps`.
5. **Pull Request** – Provide architecture context, test evidence, and mention any new infrastructure requirements.

---

## Resources

- `PIPELINE-OPTIMIZATION.md` – Guidance for Azure Pipelines improvements.
- `TESTING-GUIDE.md` – Expanded testing playbook.

For questions, open an issue or reach out via the project discussion channels.terraform init -upgrade
