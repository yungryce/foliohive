# Multi-App Architecture Implementation Guide

**Date**: December 6, 2025  
**Status**: Implemented / Living Document  
**Objective**: Describe the deployed multi-Function-App architecture, shared package workflows, and deployment story for Cloudfolio.

## Terminology
- **User**: recruiter consuming shared modules through Function Apps and interacting with candidate bundles.
- **Candidate**: GitHub username whose repository data is aggregated, cached, and processed by the shared package.

---

## 1. Overview
- Project is composed of independent Azure Function Apps plus a containerized training worker, all sharing the `cloudfolio_shared` package.
- Queue-based communication (`github-sync`, `merge-results`, `model-training`) decouples HTTP latency from enrichment workloads.
- The shared package acts as a single source of truth for cache management, GitHub access, queue schemas, AI scoring, and linguist helpers.

### Current Component Map
| Component | Runtime | Responsibility | Key Queues / Storage |
|-----------|---------|----------------|----------------------|
| `api-gateway/` | Azure Functions (HTTP) | Exposes `/health`, `/bundles/{username}`, `/ai`, orchestrates jobs, surfaces cached responses | Reads/writes hot metadata in Tables, enqueues `github-sync` & `merge-results`, reads model artifacts from blobs |
| `sync-worker/` | Azure Functions (Queue) | Consumes `github-sync`, fetches GitHub metadata, caches repo bundles | `github-sync` queue, writes repo metadata Tables + **ephemeral** repo content blobs |
| `merge-worker/` | Azure Functions (Queue) | Consumes `merge-results`, merges repo bundles, signals training | `merge-results`, `model-training`, reads/writes Tables only |
| `training-worker/` | Container (Docker, optional GPU) | Consumes `model-training`, trains semantic models, uploads weights & metadata | Reads ephemeral repo content blobs, writes model artifacts blobs + model metadata Tables, queue, model registry |
| `apps/shared/` | Python package | Reusable cache, GitHub, AI, queue, model helpers | Published via editable install and packaged artifact |

---

## 2. Shared Package Implementation (`apps/shared`)
- **Structure**: `src/cloudfolio_shared/{ai,cache,github,models,queue,linguist}` with pytest suites colocated inside each subpackage.
- **Packaging**: Managed by `pyproject.toml` (PEP 621). Editable install handled by `setup-dev.sh`; release artifacts built via `python -m build` inside CI to publish wheels for Function App deployments.
- **Versioning**: Semantic version stored in `pyproject.toml`. Function App requirements pin to the local path (dev) or exact version (CI release).
- **Local Editing Loop**:
  1. Edit modules inside `apps/shared/src/cloudfolio_shared`.
  2. Run targeted tests (`pytest shared/src/cloudfolio_shared/<module>/tests`).
  3. Re-run `setup-dev.sh --shared-only` if dependencies change; otherwise, editable install auto-reflects changes.
  4. Downstream apps import from the installed package (`from cloudfolio_shared import GitHubRepoManager, ...`).

---

## 3. Local Setup & Tooling
1. `apps/setup-dev.sh`
  - Selects Python 3.11–3.14 (default 3.11), recreates `.venv` when interpreter changes, and logs installed packages for debugging.
  - Installs shared package + each Function App's `requirements.txt` into the consolidated venv.
  - Optional `--run-tests` flag triggers `apps/tests/run_tests.sh` (unit + integration) after setup.
2. `apps/run-dev-session.sh`
  - Activates `.venv`, validates `cloudfolio_shared` import, starts api-gateway/sync-worker/merge-worker via `func start`, tails logs to `apps/logs/<worker>.log`, runs `tests/e2e_curl_tests.sh`, and keeps workers alive until interrupted.
3. Azurite / Storage Emulation
  - `apps/tests/run_tests.sh` ensures Azurite is running for integration tests; stores data under `../.azurite`.
4. Training Worker
  - Built and run via Docker (`docker build -t cloudfolio-training apps/training-worker`); `.env` supplies storage connection strings for local runs.

---

## 4. Configuration & Settings

### Function App Settings
| Setting | Description | Source |
|---------|-------------|--------|
| `AzureWebJobsStorage` | Primary storage account for queues + Tables/Blobs | Terraform output `storage_connection_string` |
| `FUNCTIONS_WORKER_RUNTIME=python` | Required for all Function Apps | `host.json` / Azure portal |
| `GITHUB_TOKEN` | PAT for GitHub API requests | Key Vault/Secrets; fallback `.env` for local |
| `CACHE_TABLE_NAMES` (sessions, repos, models) | Hot metadata tables used by `cache_manager` | `local.settings.json` + infra outputs |
| `EPHEMERAL_REPO_BLOB_CONTAINER`, `MODEL_ARTIFACTS_CONTAINER` | Blob containers used by cache layer for scratch repo content vs long-lived model artifacts | `local.settings.json` + infra outputs |
| `MODEL_TRAINING_QUEUE` | Name for training queue | Must align with queue declarations in shared constants |

### Training Worker Variables
- `AZURE_STORAGE_CONNECTION_STRING`
- `MODEL_REGISTRY_PATH` (points to `apps/training-worker/models/model_registry.py` via env override when shipping custom experiments)
- `TRAINING_MODE` (`serverless`, `gpu`, or `offline` for CI dry runs)
- `LOG_LEVEL`, `BLOB_CONTAINER`, `EXPERIMENT_NAME`

Secrets live in Azure Key Vault for cloud runs; local development uses `.env` files referenced by `local.settings.json` (not committed).

---

## 5. Packaging & Pipeline Flow
1. **Shared Package Build** (Azure Pipelines `azure-pipelines-artifact.yml`)
  - Install poetry/pip build toolchain.
  - Run `python -m build apps/shared` → produce `.whl` + `.tar.gz` artifacts.
  - Publish artifacts for downstream jobs and register version metadata.
2. **Function App Artifacts**
  - Each worker zips `function_app.py`, `host.json`, `requirements.txt`, and compiled shared wheel.
  - Deploy via `func azure functionapp publish <app>` or ZIP Deploy using Azure Pipelines tasks.
3. **Training Worker Image**
  - `docker build` targets `base` (CPU) and `gpu-base`; final image `app` copies models + `train_worker.py`.
  - Push to Azure Container Registry (ACR) with `az acr build` or pipeline `Docker@2` task.
4. **Infra Pipeline (`azure-pipelines-infra.yml`)**
  - Runs Terraform in `infra/terraform`, provisions storage accounts, queues, Function Apps, Application Insights, and outputs connection strings for app pipelines.

---

## 6. Deployment Strategy
- **Dev**: Manual `func azure functionapp publish` or `az functionapp deployment source config-zip` using artifacts built locally. Training worker deployed to Azure Container Instances pointing at dev storage.
- **Test/Staging**: Pipelines consume shared wheel + zipped Function Apps, set slot-specific configuration, and run smoke tests (curl suite) post-deploy.
- **Production**:
  1. Infra pipeline ensures App Service Plan + Storage + Monitoring are up to date.
  2. Artifact pipeline publishes shared wheel and Function App zips; release pipeline deploys in order: api-gateway → sync-worker → merge-worker → training-worker image.
  3. Rollback = redeploy previous artifact versions (retained for 30 days) or redeploy previous container tag.

Observability via Application Insights (Function Apps) and Container Insights/Log Analytics (training worker). Logging uses structured `logger = logging.getLogger('portfolio.api')` with propagation into App Insights traces.

---

## 7. Critical Tests & Edge Cases

### Shared Package Suites
- Cache layer: TTL expiration, fingerprint mismatches, storage connection loss.
- GitHub clients: PAT missing, rate limit HTTP 403, archived/private repo guard rails.
- AI components: Fall back when the fine-tuned model is unavailable, verify deterministic ordering for semantic ranking.
- Queue schemas: Validation for required fields (`job_id`, `username`, repo metadata) before serialization.

### Worker Test Coverage
- `api-gateway/tests/test_function_app.py`: HTTP contract, invalid usernames, stale job status updates, `/ai` handler model failure fallback.
- `sync-worker/tests/test_function_app.py`: queue message deserialization, repo fetch errors, retry/backoff logic, empty repo set.
- `merge-worker/tests/test_function_app.py`: missing repo bundles, conflicting fingerprints, cache save failures, job completion signals.
- `training-worker/tests/*`: queue polling loops, blob metadata writes, experiment selection, partial failure retry.
- `apps/tests/integration/*`: End-to-end queue propagation, Azurite-backed cache checks, e2e curl smoke tests.

### Edge Cases to Monitor
1. **GitHub rate limiting** – shared GitHub client performs exponential backoff; ensure secrets include `GITHUB_TOKEN` with sufficient quota.
2. **Large organizations (>100 repos)** – sync worker paginates and enqueues follow-up batches; confirm queue visibility timeouts are tuned.
3. **Poison queue messages** – rely on Azure Storage Queue poison handling; add logging for payload dumps in App Insights.
4. **Cache invalidation** – merge worker must detect missing per-repo fingerprints and re-trigger sync to avoid stale bundles.
5. **Training restarts** – training worker writes checkpoints + metadata so reruns pick up last good state.
6. **Configuration drift** – `run-dev-session.sh` validates `cloudfolio_shared` import before starting workers to catch missing installs.

---

## 8. Next Steps / Open Items
- Align `plan-aksDeployment.prompt.md` with the finalized shared package so that AKS deployment mirrors Function App packaging.
- Document Key Vault secret names per environment to remove ad-hoc local overrides.
- Expand automated load tests for queue throughput (sync + merge) before scaling beyond current usage.

This document now mirrors the implemented multi-app + shared package solution and should be updated whenever new services, queues, or packaging flows are introduced.
