# Multi-App Orchestration Playbook

**Date**: December 6, 2025  
**Status**: Implemented / Living Document  
**Scope**: `apps/api-gateway`, `apps/sync-worker`, `apps/merge-worker`, `apps/training-worker`, `apps/shared`

## Terminology
- **User**: recruiter leveraging Cloudfolio services to refresh bundles and chat about candidates.
- **Candidate**: GitHub username associated with a bundle, metadata rows, and training job.

---

## 1. Architecture Tenets
- Single source of truth lives in `cloudfolio_shared`; services should never duplicate cache, GitHub, queue, or AI logic.
- All Function Apps run inside the consolidated `.venv` locally and ship with the same shared package wheel in CI/CD to prevent dependency drift.
- Queue-based fan-out isolates latency: HTTP returns immediately, background workers retry independently, and poison queues capture failures without blocking recruiters.
- Every artifact (repo bundle, merge payload, AI answer, model weights) is fingerprinted so workers can short-circuit when nothing changed.
- Hot, recruiter-facing data (jobs, sessions, repo summaries, model status) lives in Azure Tables; cold, heavy content (full repo context and model weights) lives in Azure Blobs, with ephemeral blobs used only for training.

---

## 2. Service Inventory & Shared Touchpoints
| Service | Runtime | Primary Responsibilities | Shared Modules Consumed |
|---------|---------|--------------------------|-------------------------|
| `api-gateway/` | Azure Functions (HTTP) | Handle `/health`, `/bundles/{username}`, `/bundles/{username}/refresh`, `/bundles/{username}/status`, `/ai`, `/media/*`; enqueue jobs and surface cached data **from Tables + model artifacts**, never full repo blobs | `cache_manager`, `queue_manager`, `FingerprintManager`, `RepoScoringService`, `AIAssistant`, `DynamicKeywordExtractor` |
| `sync-worker/` | Azure Functions (Queue) | Consume `github-sync`, fetch repo context, categorize file types, cache repo **metadata rows** in Tables, upload **ephemeral** repo content blobs for training, update progress counters | `GitHubAPI`, `GitHubRepoManager`, `FileTypeAnalyzer`, `cache_manager`, `queue_manager` |
| `merge-worker/` | Azure Functions (Queue) | Consume `merge-results`, reconcile repo fingerprints, assemble aggregate bundle in Tables, enqueue `model-training` when needed with references to blobs | `cache_manager`, `FingerprintManager`, `queue_manager` |
| `training-worker/` | Container (Docker, optional GPU) | Consume `model-training`, download repo content blobs, train/refresh `SemanticModel`, upload model artifacts to blob storage and update model metadata in Tables | `model_registry`, `SemanticModel`, `cache_manager` (read/write) |
| `apps/shared/` | Python package | Hosts reusable implementations for cache, GitHub, AI, queue schemas, linguist tools, models, tests | n/a |

---

## 3. Cross-App Flow (Happy Path)
1. Recruiter calls `POST /api/bundles/{username}/refresh`.
2. `api-gateway` validates username, fingerprints current cache entry, enqueues one `github-sync` message per repo, and writes job metadata (`job:{id}`) to cache.
3. `sync-worker` fetches repo artifacts, stores `repo:{username}:{repo}` entries, and marks repos complete. Once target count reached it emits `merge-results`.
4. `merge-worker` builds the aggregate bundle, sets job status to `completed`, and optionally writes a `model-training` message when heuristics demand retraining.
5. `training-worker` refreshes embeddings/models asynchronously; completion updates metadata used by `AIAssistant` responses.
6. Recruiter polls `GET /api/bundles/{username}/status?job_id=...` for progress and hits `GET /api/bundles/{username}` or `POST /api/ai` for results drawn from cache.

Failure handling: each worker logs to App Insights/Container Insights with `job_id`, queue retries escalate to poison queues after 5 attempts, and API marks job as `failed` with reason string so UI can recover.

---

## 4. Shared Package Integration Rules
- Imports must come from `cloudfolio_shared` only; Function Apps should not reach into sibling directories.
- `setup-dev.sh` installs the package editable locally; CI builds wheels via `python -m build apps/shared` and publishes to the artifact feed consumed by deployments.
- Shared modules and their consumers:
  - `cloudfolio_shared.cache` → used by all apps for key generation, TTL management, and Azure Table/Blob persistence.
  - `cloudfolio_shared.github` → used exclusively inside `sync-worker` (and occasionally `api-gateway` health checks) to communicate with GitHub.
  - `cloudfolio_shared.queue` → defines queue names + message serializers; all enqueue/dequeue operations must go through these helpers.
  - `cloudfolio_shared.ai` → `api-gateway` uses `RepoScoringService` and `AIAssistant`; training worker uses `fine_tuning` utilities.
  - `cloudfolio_shared.linguist` → powers keyword extraction for `/ai` queries.

---

## 5. Local Development Workflow
1. `./apps/setup-dev.sh --python-version 3.13 --run-tests`
   - Ensures `.venv` matches requested interpreter, installs shared + individual app requirements, and executes pytest (unit + integration) when requested.
2. `./apps/run-dev-session.sh [--skip-e2e]`
   - Activates `.venv`, validates `cloudfolio_shared` import, starts api-gateway/sync-worker/merge-worker via `func start`, captures logs (`apps/logs/*.log`), and optionally runs `tests/e2e_curl_tests.sh`.
3. `pytest apps/tests/integration` for queue + cache end-to-end verification (requires Azurite started automatically by the test runner when integration tests are invoked).
4. Training worker quick loop: `docker build -t cloudfolio-training apps/training-worker && docker run --env-file .env.local cloudfolio-training`.

---

## 6. Deployment & Packaging
- **Shared package**: Build wheel/tarball via `python -m build apps/shared` in `portfolio/azure-pipelines-artifact.yml`; publish artifact versioned as `cloudfolio-shared-x.y.z`.
- **Function Apps**: Each worker bundles `function_app.py`, `host.json`, `requirements.txt`, and references the shared wheel via `pip install cloudfolio-shared==x.y.z` during deployment jobs. Publish with `func azure functionapp publish` or ZIP deploy tasks.
- **Training worker**: Dockerfile contains CPU base (default) and GPU base (optional). Pipelines build/push to ACR; AKS/ACI runs container with `AZURE_STORAGE_CONNECTION_STRING`, `TRAINING_MODE`, and `LOG_LEVEL` env vars.
- **Infra**: Terraform (`infra/terraform`) provisions storage account, queues, Function Apps, Application Insights, and outputs connection strings stored in `infra/terraform/outputs.env` for local use.

---

## 7. Testing & Observability Matrix
| Layer | Location | Key Scenarios |
|-------|----------|---------------|
| Shared unit tests | `apps/shared/src/cloudfolio_shared/*/tests` | Cache TTL rollover, GitHub rate limit handling, queue schema validation, linguist parsing |
| Function tests | `apps/api-gateway/tests`, `apps/sync-worker/tests`, `apps/merge-worker/tests` | HTTP contracts, queue message emissions/consumption, fingerprint mismatch recovery |
| Integration tests | `apps/tests/integration` | github-sync → merge-results workflow using Azurite; cache sync accuracy |
| Unit curl | `apps/api-gateway/tests`, `apps/sync-worker/tests`, `apps/merge-worker/tests` | (To be implemented) targeted curl requests per worker for faster feedback 
| E2E curl | `apps/tests/e2e_curl_tests.sh` | `/health`, `/bundles/{username}`, refresh polling, `/ai` response shape |
| Training tests | `apps/training-worker/tests` | Queue polling loop, blob upload failures, model registry selection |
| Monitoring | App Insights + Log Analytics | Queue depth alerts, worker failures, latency budgets |

Edge cases to keep on radar:
- GitHub secondary rate limits → `sync-worker` must honor retry-after headers; instrumentation verifies throttle frequency.
- Large orgs (>100 repos) → ensure `github-sync` messages chunked to avoid queue lock timeouts.
- Cache eviction vs stale fingerprints → `merge-worker` should requeue when repo cache missing or fingerprint mismatch occurs.
- Poison queue hygiene → run `az storage message peek` checks via automation and alert when >0 poison messages exist.
- Training restarts mid-run → metadata checkpoints allow idempotent reruns keyed by `job_id`.

---

## 8. Linked References
- `plan-sharedArchitecture.prompt.md` – Shared package structure, packaging, and local tooling.
- `plan-dataProcessingArchitecture.prompt.md` – Data pipeline internals and storage strategies.
- `plan-modelTraining.prompt.md` – Training worker decoupling and model refresh logic.
- `plan-frontendSplit.prompt.md` – UI separation strategy for recruiter tools.
- `plan-aksDeployment.prompt.md` – Future container platform convergence.

Keep this playbook updated when queue names, shared package APIs, or deployment mechanics change.

