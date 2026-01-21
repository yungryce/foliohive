# Queue & Data Processing Architecture Blueprint

**Date**: December 6, 2025  
**Status**: Implemented / Living Document  
**Applies To**: `api/v.../api-gateway`, `api/v.../sync-worker`, `api/v.../merge-worker`, `api/v.../shared`, `api/v.../training-worker`

## Terminology
- **User**: recruiter consuming Cloudfolio APIs to evaluate candidates.
- **Candidate**: GitHub username being queried, cached, and analyzed on behalf of the recruiter.

---

## 1. Goals & Context
- Deliver <5s API responses while long-running enrichment executes asynchronously via Azure Storage Queues.
- Keep queue contracts, cache writes, and AI scoring logic centralized in `cloudfolio_shared` to avoid drift between services.
- Ensure every stage (ingest → merge → train → answer) is observable, retryable, and testable locally with Azurite.
- Separate **hot, queryable metadata** (Azure Tables) from **cold, ephemeral content blobs** (Azure Blobs) for cost and performance.

---

## 2. Request-to-Response Flow
1. **Trigger** – `api/v.../api-gateway/function_app.py` receives `/bundles/{username}/refresh` or `/ai` requests, validates usernames, and persists initial job metadata through `cloudfolio_shared.cache_manager` (internally powered by the shared `table_manager`).
2. **Sync Stage** – `api/v.../sync-worker/function_app.py` consumes `github-sync` queue messages, fetches repo metadata via `GitHubAPI`/`GitHubRepoManager`, fingerprints payloads, and stores **repo-level metadata** in Azure Tables via the shared `table_manager` plus **ephemeral repo content blobs** for training.
3. **Merge Stage** – `api/v.../merge-worker/function_app.py` consumes `merge-results`, merges cached repo **metadata rows** for the job into a consolidated candidate bundle record in Tables using `table_manager` primitives and, when needed, emits a `model-training` message whose payload points at ephemeral blobs.
4. **Training Stage** – `api/v.../training-worker/train_worker.py` (container) processes `model-training` messages, downloads referenced blobs containing full repo content, trains/refreshes models, and writes **long-lived model artifacts** back to blob storage and **lightweight model metadata** to Tables through `table_manager` helpers.
5. **Response** – `api-gateway` reads **only structured metadata** (e.g., candidate sessions, skills, repo summaries, model status) from Tables via `cache_manager`/`table_manager` and combines it with model outputs to serve `/bundles/{username}` and `/ai` without ever loading full repo content.

---

## 3. Stage Responsibilities & Shared Dependencies
| Stage | Service & Key Files | Shared Modules Used | Output |
|------|---------------------|---------------------|--------|
| HTTP ingress | `api-gateway/function_app.py` | `cache_manager`, `table_manager`, `queue_manager`, `FingerprintManager` | Job & session metadata rows in Tables, queue messages |
| Repository sync | `sync-worker/function_app.py` | `GitHubAPI`, `GitHubRepoManager`, `FileTypeAnalyzer`, `cache_manager`, `table_manager` | Per-repo metadata rows in Tables; ephemeral content blobs for training |
| Merge | `merge-worker/function_app.py` | `cache_manager`, `table_manager`, `FingerprintManager`, `queue_manager` | Aggregated candidate bundle row in Tables; optional `model-training` job referencing blobs |
| Model training | `training-worker/train_worker.py`, `models/` | `SemanticModel`, `model_registry`, `table_manager` | Model artifacts in blob storage; model + scoring metadata rows in Tables |
| AI response | `api-gateway/function_app.py` | `RepoScoringService`, `AIAssistant`, `DynamicKeywordExtractor` (from linguist), `table_manager` | Natural language answer + structured skills, drawn purely from Tables + model APIs |

---

## 4. Queue Contracts (Azure Storage Queues)
All messages serialize to JSON and are validated through `cloudfolio_shared.queue.queue_manager` schemas.

### `github-sync`
```json
{
  "job_id": "uuid",
  "username": "candidate",
  "repo_metadata": {"name": "repo", ...},
  "expected_fingerprint": "sha256",
  "force_refresh": false
}
```
- Produced by `api-gateway` when refresh requested.
- Consumed by `sync-worker`; multiple messages per job (one per repo).

### `merge-results`
```json
{
  "job_id": "uuid",
  "username": "candidate",
  "expected_repo_count": 7,
  "completed_repo_names": ["api", "infra"],
  "force_refresh": false
}
```
- Enqueued by `sync-worker` after caching all repos for a job.
- Consumed by `merge-worker`, which reconciles cached entries and writes job completion status.

### `model-training`
```json
{
  "job_id": "uuid",
  "username": "candidate",
  "bundle_cache_key": "bundle:candidate:uuid",
  "experiment_name": "default",
  "training_params": {...}
}
```
- Enqueued by `merge-worker` once bundle ready and AI needs refresh.
- Consumed by containerized training worker; optional or batched in lower environments.

Queue properties: default visibility timeout 30s, poison queue enabled after 5 retries, message TTL 7 days (configurable in Terraform `infra/terraform/variables.tf`).

---

## 5. Data Stores: Tables vs Blobs

### 5.1 Azure Table Storage (Hot, Queryable Metadata)
- **Tables used (logical names)**:
  - `JobSessions` – one row per `(username, job_id)` with job status, timestamps, and high-level summary.
  - `RepoMetadata` – per-repo rows (partitioned by username) containing README summary, tech stack, and fingerprints.
  - `ModelMetadata` – current model version, training fingerprint, experiment name, and last-trained timestamps.
- **Access patterns**:
  - `api-gateway` queries `JobSessions` + `RepoMetadata` for `/bundles/{username}` and `/bundles/{username}/status`.
  - `merge-worker` reads/writes `JobSessions` and aggregates `RepoMetadata` into a consolidated bundle record.
  - `training-worker` updates `ModelMetadata` after successful runs; `api-gateway` reads it to decide whether to fall back or use fresh embeddings.
- **Implementation**:
  - All access funneled through `cloudfolio_shared.table.table_manager` (low-level CRUD + query helpers) and `cloudfolio_shared.cache.cache_manager` (scenario-specific orchestration), preventing raw Table SDK exposure inside Function Apps.

### 5.2 Azure Blob Storage (Cold, Ephemeral Content)
- **Blob containers (logical)**:
  - `repo-bundles-ephemeral` – per-job/per-repo blobs containing full harvested repo content for **training only** (README, key files, embeddings inputs).
  - `model-artifacts` – long-lived zipped model weights and tokenizer data used by `training-worker` and consumed by `api-gateway`/`AIAssistant`.
- **Access patterns**:
  - `sync-worker` uploads blobs to `repo-bundles-ephemeral` and never reads them again.
  - `training-worker` downloads blobs from `repo-bundles-ephemeral`, trains, then deletes blobs or relies on TTL-based lifecycle rules.
  - `api-gateway` **never** reads from `repo-bundles-ephemeral`; it only reads from `model-artifacts` when initializing AI components.
- **Lifecycle / retention**:
  - `repo-bundles-ephemeral` is treated as scratch space: lifecycle management in Storage automatically deletes blobs after N days/hours; workers must be idempotent to blob disappearance.
  - `model-artifacts` retains a small number of recent model versions (e.g., last 3) via lifecycle rules; everything else is pruned.

### 5.3 What Each App Actually Uses
- `api-gateway`:
  - Reads/writes Tables (`JobSessions`, `RepoMetadata`, `ModelMetadata`) strictly through `table_manager` helpers surfaced by `cache_manager`.
  - Reads long-lived model artifacts from `model-artifacts` via AI helpers.
  - Does **not** read full repo file blobs.
- `sync-worker`:
  - Writes `RepoMetadata` rows and `JobSessions` progress by calling `table_manager` upsert helpers; writes **ephemeral** repo content blobs for training.
- `merge-worker`:
  - Reads `RepoMetadata` rows; writes aggregate bundle row + job status to `JobSessions` using `table_manager` batch operations; does not touch blobs directly.
- `training-worker`:
  - Reads ephemeral repo content blobs; writes `ModelMetadata` rows through `table_manager` and stores `model-artifacts` blobs.

### 5.4 Table Manager vs Cache Manager (Actionable Shared Design)
- **Location & Scope** – Introduce `apps/shared/src/cloudfolio_shared/table/table_manager.py` with a `TableManager` class that owns Azure Data Tables connections, table name discovery, and resilient CRUD helpers. Keep `cache_manager` focused on business-level workflows that combine Table access with queue emission or blob lookups.
- **Responsibilities**:
  1. Instantiate a single `TableServiceClient` based on `AzureWebJobsStorage` or explicit `TABLE_STORAGE_CONNECTION_STRING`.
  2. Provide typed row operations (`upsert_job_metadata`, `append_repo_metadata`, `get_repo_metadata_by_job`, `update_model_metadata`). Each helper returns normalized dictionaries ready for higher-level cache orchestration.
  3. Handle retries, ETag concurrency, and structured logging; bubble up typed errors for poison queue handling.
- **Public API Sketch**:
  ```text
  TableManager(
    *,
    sessions_table: str,
    repo_table: str,
    model_table: str,
    telemetry: Logger
  )

  upsert_job_metadata(session: JobMetadataRow) -> None
  batch_upsert_repo_metadata(rows: list[RepoMetadataRow]) -> None
  query_repo_metadata(username: str, job_id: str | None = None) -> list[RepoMetadataRow]
  get_bundle_summary(username: str, job_id: str) -> JobMetadataRow | None
  upsert_model_metadata(metadata: ModelMetadataRow) -> None
  ```
  Rows can be Python dataclasses or TypedDicts defined under `cloudfolio_shared.table.schemas` to promote reuse across workers and tests.
- **Integration Path**:
  1. `cache_manager` composes a `TableManager` instance during initialization and delegates all Table access to it.
  2. Function Apps import only `cache_manager` to perform business workflows; direct access to `table_manager` is allowed for new utilities that need row-level control (e.g., migrations).
  3. Tests live under `apps/shared/tests/table/test_table_manager.py`, using Azurite to verify partition/row key behavior and retry policies.
- **Config Contracts** – `local.settings.json` retains existing `CACHE_TABLE_NAMES` values; `table_manager` consumes them via typed settings object to avoid stringly-typed table references.
- **Observability** – Each helper logs `{job_id, username, table_name}` on success/failure and emits metrics for latency + retry count, enabling queue processors to correlate storage latency spikes.
- **Rollout Notes** – Ship `table_manager` before altering Function Apps. Once stable, gradually replace direct Table SDK usage (if any) with `cache_manager` calls to ensure there is exactly one storage abstraction beneath the workloads.

## 6. Data Processing Pipeline (Shared Package Focus)
1. **Query Understanding** – `cloudfolio_shared.linguist.languages.yml` + `DynamicKeywordExtractor` map recruiter prompts to tech keywords.
2. **Repo Harvesting** – `GitHubRepoManager` pulls README and other descriptive repository content, captures language counts and file-type histograms, and **persists summaries/metrics into Tables plus full content into ephemeral blobs**.
3. **Fingerprinting & Cache** – `FingerprintManager` generates SHA fingerprints stored as columns in Tables and blob metadata to detect freshness.
4. **Hybrid Ranking** – `RepoScoringService` reads only from Tables + model APIs, blending keyword scores with semantic embeddings from the fine-tuned `SemanticModel`.
5. **Assistant Response** – `AIAssistant` returns structured answers containing relevant repos, skills, depth scores, and follow-up prompts using only hot metadata.
6. **Model Maintenance** – `training-worker` consumes ephemeral blobs, trains `SemanticModel` definitions from `apps/training-worker/models`, persists model artifacts, and updates `ModelMetadata` rows for use by `api-gateway`.

---

## 7. Configuration & Observability
| Setting | Location | Notes |
|---------|---------|-------|
| `AzureWebJobsStorage` | `local.settings.json`, Azure Function App config | Must match queue account used by all workers; exported via Terraform `outputs.env`. |
| `FUNCTIONS_WORKER_RUNTIME` | `host.json` / Azure portal | `python` for all Function Apps. |
| `GITHUB_TOKEN` | Key Vault secret, `.env` for local | Required to avoid GitHub rate limits in `sync-worker`. |
| `CACHE_TABLE_NAMES` (sessions, repos, models) | `local.settings.json` | Logical table names used by `cache_manager` for hot metadata. |
| `EPHEMERAL_REPO_BLOB_CONTAINER`, `MODEL_ARTIFACTS_CONTAINER` | `local.settings.json` | Separate containers for scratch repo content vs long-lived model artifacts. |
| `MODEL_TRAINING_QUEUE`, `MERGE_RESULTS_QUEUE`, `GITHUB_SYNC_QUEUE` | `local.settings.json`, Terraform vars | Must stay in sync with shared constants in `cloudfolio_shared.queue`. |
| Logging | App Insights + Log Analytics | `logger = logging.getLogger('portfolio.api')` already propagates to Insights; training worker sends to Container Insights. |

Observability tips:
- Enable Application Insights sampling for HTTP + queue triggers.
- Monitor queue depth via Azure Monitor metrics; scale rules follow `ApproximateMessageCount`.
- Training worker logs include `job_id` and blob keys for correlation back to Function Apps.

---

## 8. Local Development & Testing
1. `./apps/setup-dev.sh --run-tests` – creates `.venv`, installs shared + worker deps, runs pytest (unit + integration) using Azurite if needed.
2. `./apps/run-dev-session.sh [--skip-e2e]` – activates venv, launches Function Apps via `func start`, logs output to `apps/logs/`, and runs curl-based smoke tests.
3. Targeted tests:
   - `pytest api/v.../api-gateway/tests` – HTTP contract & queue emission.
   - `pytest api/v.../sync-worker/tests` – message deserialization, GitHub fetch mocks.
   - `pytest api/v.../merge-worker/tests` – bundle merge logic, fingerprint drift.
   - `pytest api/v.../shared/src/cloudfolio_shared/*/tests` – shared utilities.
   - `pytest api/v.../tests/integration` – queue → cache → response loop.
4. Training worker: `docker build -t cloudfolio-training api/v.../training-worker && docker run --env-file .env cloudfolio-training` for manual job processing.
5. Storage emulation: Azurite provides both Table and Blob APIs; integration tests must cover Table row lifecycle and blob lifecycle assumptions (e.g., missing ephemeral blobs).

---

## 9. Critical Tests & Edge Cases
- **Rate Limits** – Simulate GitHub 403 in `sync-worker` tests to confirm retry/backoff logic and poison queue escalation.
- **Stale Cache vs Fresh Data** – `merge-worker` tests ensure stale fingerprints in Tables trigger resync before marking jobs complete.
- **Queue Poison Handling** – Validate `cloudfolio_shared.queue.QueueManager` sends malformed payloads to `*-poison` queues and logs job IDs for triage.
- **AI Fallbacks** – `/ai` endpoint should gracefully degrade when embeddings/model artifacts are missing, returning cached summaries only.
- **Large Repo Sets** – Stress tests for >100 repos (pagination + batching) to ensure we do not exceed queue visibility timeout.
- **Training Recovery** – Training worker writes checkpoints; rerunning with same `job_id` must be idempotent even if some ephemeral blobs are missing.
- **Ephemeral Blob Lifecycle** – Ensure system behaves when repo content blobs have been auto-deleted: training jobs either skip or re-request sync, while recruiter-facing APIs continue to work from Tables.

---

## 10. Linked Plans & References
- `plan-multiAppArchitecture.prompt.md` – overall service + shared package orchestration.
- `plan-sharedArchitecture.prompt.md` – deep dive on shared package + cache layer (Tables vs Blobs); update this next with the concrete `table_manager` module blueprint referenced above.
- `plan-modelTraining.prompt.md` – training worker design and its use of ephemeral repo blobs.
- `plan-main.prompt.md` – product scope and recruiter workflow overview.

This blueprint replaces `plan-dataProcessingModel.prompt.md` and `plan-azureStorageQueuesArchitecture.prompt.md`; keep it updated whenever queue schemas, data stores, or data-processing stages change.
