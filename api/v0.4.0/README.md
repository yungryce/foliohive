# FolioHive API (v0.4.0)

Azure Functions backend using the Blueprint pattern.

This service coordinates candidate refresh jobs, GitHub metadata sync, repo micro-summary generation, and the HTTP endpoints consumed by the Angular UI.

## Overview

The backend is a modular monolith split into Azure Functions blueprints:

- API Gateway: HTTP endpoints for refresh, polling, metadata, summaries, and AI queries.
- Sync Worker: queue-triggered metadata sync from GitHub.
- Cache Worker: queue-triggered repo micro-summary generation.
- Reconciliation Worker: timer-triggered cleanup of stale jobs and metadata.

Primary flow:

1. UI calls `POST /api/candidate/{username}/refresh`.
2. API gateway decides whether data is fresh enough to reuse.
3. Stale repos are enqueued to `github-sync`.
4. Sync worker persists repo metadata and languages, then enqueues `github-cache`.
5. Cache worker fetches README and config files in-memory, extracts signals, and stores a repo micro-summary blob.
6. UI reads metadata and AI summaries through API gateway endpoints.

## Function App Structure

Entrypoint: `function_app.py`

Registered blueprints:

- `blueprints/api_gateway.py`
- `blueprints/sync_worker.py`
- `blueprints/cache_worker.py`
- `blueprints/reconciliation_worker.py`

The function app uses `register_blueprint` or `register_functions`, depending on the Azure Functions runtime capability available at startup.

## Blueprint Responsibilities

### API Gateway

File: `function-app/blueprints/api_gateway.py`

Responsibilities:

- Build a candidate request context with trace and job information.
- Start refresh jobs and return polling metadata.
- Serve repo metadata, profile data, repo summaries, and profile summaries.
- Serve AI query responses built from cached micro-summaries.
- Track session candidates.

Key internal helpers:

- `_prepare_candidate_context()`
- `_get_portfolio_bundle()`
- `_get_repos_entries()`
- `_get_single_repo_entry()`
- `_aggregate_portfolio_statistics()`

### Sync Worker

File: `function-app/blueprints/sync_worker.py`

Queue: `github-sync`

Responsibilities:

- Deserialize sync queue messages.
- Fetch repo metadata and language breakdown from GitHub.
- Persist `RepoGitHubMetadata` and `RepoLanguages`.
- Mark repo sync progress and enqueue cache work.

### Cache Worker

File: `function-app/blueprints/cache_worker.py`

Queue: `github-cache`

Responsibilities:

- Fetch README and selected config files from GitHub in-memory.
- Extract structured config signals via `data_filter.py`.
- Generate repo micro-summaries via `summary_manager.py`.
- Persist summary cache records and advance repo/job status.

Important: raw README/config blobs are not persisted as a long-term file cache in this version. The durable artifact is the repo micro-summary blob.

### Reconciliation Worker

File: `function-app/blueprints/reconciliation_worker.py`

Implemented timers:

- `cleanup_old_jobs`: every 3 hours.
- `cleanup_old_repo_github_metadata`: every 2 hours.

Defined but not yet implemented as functions:

- discovered paths cleanup.
- old blob cache cleanup.

## Shared Modules

Shared package root:

`shared/src/foliohive_shared/`

Key modules:

- `ai/ai_assistant.py`: AI API wrapper and usage tracking.
- `ai/summary_manager.py`: repo summary generation, profile aggregation, query summarization.
- `ai/data_filter.py`: typed config extraction.
- `cache/cache_manager.py`: blob save/load helpers for summary artifacts.
- `github/github_repo_manager.py`: GitHub metadata and file retrieval.
- `queue/queue_manager.py`: typed queue enqueue helpers.
- `table/table_manager.py`: table names, row dataclasses, CRUD operations.

## Data Model

Tables are split into data tables and lifecycle tables.

### Data Tables

- `UserProfile`: candidate GitHub profile, keyed by username.
- `RepoGitHubMetadata`: repo metadata, keyed by username + repo name.
- `RepoLanguages`: language rows, keyed by `{username}:{repo_name}` + language.
- `RepoCacheSummary`: fingerprint-addressed repo micro-summary cache, keyed by repo name + fingerprint.

### Lifecycle Tables

- `JobMetadata`: job-level status and trace information.
- `RepoSyncStatus`: per-repo pipeline status inside a job.
- `SessionCandidates`: recently viewed candidates per session.

### Usage Tables

- `RepoAPIUsage`: GitHub API usage tracking.
- `AIRequestUsage`: AI token and model usage tracking.

Status values in active use:

- Job: `queued`, `syncing`, `metadata_ready`, `caching_started`, `completed`, `failed`
- Repo sync: `pending`, `synced`, `summary_ready`, `failed`

## Cache and Fingerprints

- `job_id` is lifecycle provenance for a run.
- `fingerprint` is the content version for GitHub-derived repo/profile data.
- `RepoCacheSummary` is globally shared by repo name + fingerprint.
- Blob keys for micro-summaries remain username-scoped even when the table row is shared.

## API Routes

All routes are served under `/api`.

### Health and Session

- `GET /api/health`
- `GET /api/session/candidates`

### Candidate Refresh and Status

- `POST /api/candidate/{username}/refresh`
- `GET /api/candidate/{username}/status?job_id={job_id}`

Refresh response includes:

- `job_id`
- `status`
- `repos_queued`
- `status_url`

Status response includes:

- `status`
- `metadata_ready`
- `summary_ready`
- `progress`
- `repo_details`

### Metadata Endpoints

- `GET /api/candidate/{username}`
- `GET /api/candidate/{username}/{repo}/metadata`
- `GET /api/candidate/{username}/profile`

Metadata endpoints depend on job status `metadata_ready` or later.

### Summary Endpoints

- `GET /api/candidate/{username}/summary`
- `GET /api/candidate/{username}/{repo}/readme-summary`

Summary endpoints depend on job status `completed`.

Returned fields of interest:

- profile summary: `summary_markdown`
- repo summary: `readme_summary_markdown`

### AI Query Endpoint

- `POST /api/ai`

The current implementation expects `username` and `query` in URL query params, even though the UI sends them in the request body. That mismatch is a known bug.

## Response Shape

The API gateway uses a success/error envelope.

Success:

```json
{
  "status": "success",
  "ok": true,
  "data": {},
  "meta": {
    "api_version": "0.4.0",
    "schema_version": "2026-01-27",
    "request_id": "...",
    "server_time": "..."
  }
}
```

Error:

```json
{
  "status": "error",
  "ok": false,
  "error": {
    "code": "...",
    "message": "..."
  },
  "meta": {
    "api_version": "0.4.0"
  }
}
```

## Local Development

Prerequisites:

- Python 3.12+
- Azure Functions Core Tools v4
- Azurite
- GitHub token
- OpenAI API key

Typical setup:

```bash
cd api/v0.4.0
./setup-dev.sh
./ensure-azurite.sh
cd function-app
source ../venv/bin/activate
func start --python --port 7071
```

Local API base URL:

`http://localhost:7071/api`

Useful checks:

```bash
curl http://localhost:7071/api/health
curl -X POST http://localhost:7071/api/candidate/torvalds/refresh
```

## Testing

Current tests under `api/v0.4.0/tests/` are minimal. This project is still in proof-of-concept mode, and validation is primarily manual:

- run the local stack
- trigger a candidate refresh
- inspect UI output
- inspect table/blob state

Available runner:

```bash
cd api/v0.4.0/tests
./run_tests.sh
```

## Known Issues

- `POST /api/ai` currently parses `username` and `query` from URL params instead of the JSON body used by the UI.
- All summary types are explicitly mapped to `gpt-5-nano` in `summary_manager.py`.
- Cleanup schedules for discovered paths and old blob cache are defined, but the timer-triggered functions are not implemented.

## Related Files

- API entrypoint: `function-app/function_app.py`
- API gateway: `function-app/blueprints/api_gateway.py`
- Sync worker: `function-app/blueprints/sync_worker.py`
- Cache worker: `function-app/blueprints/cache_worker.py`
- Reconciliation worker: `function-app/blueprints/reconciliation_worker.py`
- Shared package: `shared/src/foliohive_shared/`
