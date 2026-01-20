# Containerized Model Training Plan

**Date**: December 6, 2025  
**Status**: Working Plan (kept in sync with `plan-dataProcessingArchitecture.prompt.md`)

## 1. Purpose & Guardrails
- Isolate high-cost semantic model training into a containerized worker that can scale CPU/GPU independently from Azure Functions.
- Honour the storage contract: **Tables for hot metadata via `table_manager`**, **Blobs for cold content**, **Queues for orchestration**.
- Keep recruiter-facing APIs unaware of full repo payloads; only the training worker touches ephemeral blobs.
- Ensure the same Docker image can run as an Azure Container Instance (ACI), AKS Job, or local Docker process without code changes.

## 2. Responsibilities & Interactions
| Responsibility | Owner | Notes |
| --- | --- | --- |
| Consume `model-training` queue messages | `apps/training-worker/train_worker.py` | Uses `cloudfolio_shared.queue.queue_manager` schemas for validation.
| Download repo bundles | Training worker | Reads blob references from the queue payload, never from Tables.
'| Train & package semantic model | `models/semantic_model.py` | Loads experiment settings from `models/model_registry.py`.
| Write model artifacts | Training worker | Uploads to `model-artifacts` blob container; older versions trimmed by lifecycle rules.
| Publish metadata | Training worker via `table_manager` | Upserts rows in `ModelMetadata` and updates `JobSessions` status.
| Signal completion | Queue message deletion + table updates | Merge/API workers observe status in Tables; no callback required.

## 3. Interfaces & Contracts
### 3.1 Queue Payload (`model-training`)
```json
{
  "job_id": "uuid",
  "username": "candidate",
  "bundle_cache_key": "bundle:candidate:uuid",
  "experiment_name": "default",
  "training_params": {"max_pairs": 150},
  "blob_batch": [
    {
      "container": "repo-bundles-ephemeral",
      "name": "username/job-id/repo.jsonl",
      "fingerprint": "sha256"
    }
  ]
}
```
- Produced by `merge-worker` only after all repo metadata rows exist in Tables.
- `blob_batch` can be empty to skip training (worker no-ops and updates status accordingly).

### 3.2 Table Access (via `cloudfolio_shared.table.table_manager`)
- `ModelMetadata` row keys: `PartitionKey=username`, `RowKey=model_fingerprint`.
- `JobSessions` update: set `model_status`, `model_fingerprint`, `trained_at`.
- Table operations **must** use the shared `TableManager` helpers to keep retry logic, logging, and schema guarantees aligned with other plans.

### 3.3 Blob Usage
- Reads from `repo-bundles-ephemeral` only; blobs are deleted automatically after lifecycle TTL, so the worker must tolerate `404` and request a re-sync via `table_manager` note if missing.
- Writes to `model-artifacts` with predictable names `model_{fingerprint}.zip` and optional metadata JSON; blob metadata includes `experiment_name`, `created_at`, `username`.

### 3.4 Observability & Telemetry
- Containers log to Application Insights / Container Insights with `{job_id, username, experiment_name}`.
- Emit custom metrics: `training.duration_ms`, `training.retries`, `training.blob_missing`.
- Poison queue: rely on Storage Queue built-in poison queue after 5 attempts; training worker never manually dead-letters.

## 4. Runtime Architecture
```
merge-worker ──► Azure Storage Queue (model-training) ──► Containerized training worker
                                         │                                   │
                                         │                                   ├─► Reads blobs (repo-bundles-ephemeral)
                                         │                                   ├─► Writes artifacts (model-artifacts)
                                         │                                   └─► Upserts Tables via table_manager
api-gateway ◄────────────────────────────┴──────────────────────────── tables/model metadata only
```
- **ACI Path**: `ContainerLauncher` (Function App or management command) spins up an ACI with `restartPolicy=Never`, 4 vCPU / 16GB RAM defaults, optional GPU SKU for experiments.
- **AKS Path**: `K8sJobLauncher` submits a Job with identical image/environment, using node selectors for GPU pools when required. Jobs set `ttlSecondsAfterFinished` to auto-clean resources.
- **Local Dev**: `docker run --env-file .env apps/training-worker` reads from Azurite queues + blobs.

## 5. Implementation Workstreams
1. **Worker Core** (`train_worker.py`)
   - Poll queue (visibility timeout 10 min).
   - Stream blobs to local `/tmp/job_id/` (no in-memory buffering for large repos).
   - Invoke `SemanticModel.train_from_repositories` with experiment config.
   - Upload artifact + metadata, then call `table_manager.upsert_model_metadata` and `table_manager.mark_session_trained` helpers.
2. **Model Layer** (`models/semantic_model.py`, `model_registry.py`)
   - Keep base models + hyperparameters declarative.
   - Support CPU path by default; guard GPU-only experiments via experiment metadata.
3. **Launchers** (`ContainerLauncher`, `K8sJobLauncher`)
   - Wrap platform specifics behind small helpers.
   - Always inject environment variables (storage connection/bus names) via Managed Identity secrets/Key Vault outputs from Terraform.
4. **Shared Contracts**
   - `cloudfolio_shared.queue.queue_manager` contains schema + message builders used by merge worker and tests.
   - `cloudfolio_shared.table` exports strongly typed rows; training worker imports only these DTOs.

## 6. Delivery Plan
| Phase | Scope | Exit Criteria |
| --- | --- | --- |
| 0 – Prep | Finalize queue schema, add `table_manager` helpers, update Terraform variables. | Schema published in shared package; merge worker referencing new payload shape. |
| 1 – Container MVP | Implement worker core, Dockerfile, Azurite-backed unit/integration tests. | `pytest apps/training-worker/tests` passes; manual docker run completes single job locally. |
| 2 – Azure Rollout | Build/push image to ACR, wire `ContainerLauncher`, toggle via `ENABLE_CONTAINER_TRAINING`. | ACI job completes and updates Tables in staging; Function App logs contain container name + job_id. |
| 3 – Production & AKS Option | Enable training container by default, add AKS Job path for sustained workloads or GPU experiments. | 100% of training jobs run via containers; Function App queue trigger removed; documentation updated. |

## 7. Testing & Acceptance
- **Unit**: `test_model_registry.py`, `test_semantic_model.py`, `test_train_worker.py` (mock queue/blob clients, assert table payloads).
- **Integration**: docker compose with Azurite covering queue → blob → table loop; ensure missing blob results in retriable failure without queue poison.
- **Perf Smoke**: at least one 100-repo bundle processed under 2 minutes with CPU SKU.
- **Resiliency**: simulate blob 404, storage throttling, and queue visibility timeout expiration; confirm retries respect exponential backoff and surface telemetry.

## 8. Linked Documents
- `plan-dataProcessingArchitecture.prompt.md` – authoritative source for queue/storage contracts.
- `plan-sharedArchitecture.prompt.md` – details `table_manager`, `cache_manager`, and DTOs used here.
- `plan-frontendSplit.prompt.md` – references how recruiter UI consumes model metadata exposed by this plan.

Keep this file lean: when implementation details drift (e.g., new experiment knobs), update `model_registry.py` + shared schemas and note the change here in a paragraph, not full code listings.
