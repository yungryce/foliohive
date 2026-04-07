#  Recruiting analysis tool
**codebase root**: `/home/juk/DEV/foliohive/`
**api root**: `/api/v0.4.0/function-app/function_app.py`
**api gateway**: `/api/v0.4.0/function-app/blueprints/api_gateway.py`
**shared modules**: `/api/v0.4.0/shared/src/foliohive_shared/`
**Table Manager**: `/api/v0.4.0/shared/src/foliohive_shared/table/table_manager.py`
**AI Assistant**: `/api/v0.4.0/shared/src/foliohive_shared/ai/ai_assistant.py`
**Summary Manager**: `/api/v0.4.0/shared/src/foliohive_shared/ai/summary_manager.py`
**Data Filter / Extraction**: `/api/v0.4.0/shared/src/foliohive_shared/ai/data_filter.py`
**Cache Manager**: `/api/v0.4.0/shared/src/foliohive_shared/cache/cache_manager.py`
**Sync Worker**: `/api/v0.4.0/function-app/blueprints/sync_worker.py`
**Cache Worker**: `/api/v0.4.0/function-app/blueprints/cache_worker.py`
**Reconciliation Worker**: `/api/v0.4.0/function-app/blueprints/reconciliation_worker.py`
**ui root**: `/ui/src/app/`
**ui services**: `/ui/src/app/services/`
**venv**: `/api/v0.4.0/venv/`

- index codebase root for ease of reference
- you should assume you have access to all files beyond what is provided in the prompt
- Always strive to reduce complexity and improve code quality
- code smell should be addressed when spotted
- Do not introduce technical debt
- Always prefer explicitness over implicitness
- Always prefer simplicity over complexity
- When my query has a question mark, answer the question first before providing any additional information

## Architectural Overview
The recruiting analysis tool is designed to analyze a candidate's Github activity and provide insights into their coding skills and style. Response is returned as AI generated summaries aggregated from the candidate's Github metadata and select blob files.
The system is built using a microservices architecture, which separates components responsible for data retrieval, processing, caching, and AI summarizations. A Table schema is used to track and sync events.

**Workflow Highlight**
Build: UI `startBuild` -> api `trigger_candidate_refresh` -> `process_sync_job` -> `process_cache_job` -> `generate_repo_micro_summary` -> `summarize_repo_micro_summary_json` -> `call_ai_api`
UI Summaries:
    - UI `loadReadmeSummary` -> api `get_repo_summary` -> `expand_repo_micro_summary(*, repo_name, job_id)` -> `expand_repo` -> `call_ai_api`
    - UI `loadProfileSummary` -> api `get_profile_summary` -> `get_or_generate_profile_summary(profile, job_id)` -> `aggregate_micro_summaries` -> `summarize_profile` -> `call_ai_api`
    - UI `ask` (atomic) -> `portfolio_query` -> `get_or_generate_query_response(*, job_id, query, profile)` -> `aggregate_micro_summaries` -> `summarize_query` -> `call_ai_api`
Metadata
- `loadProfile` -> `get_profile`
- `loadRepoBundle` -> `get_candidate_repos_metadata`
- `loadRepoMetadata` -> `get_candidate_repo_metadata`

**Pipeline**: `api_gateway.py` → `sync_worker.py` → `cache_worker.py`
- Sync fetches repo metadata from GitHub, cache generates micro-summaries per repo
- UI reads all data via `api_gateway.py` (metadata, summaries, AI responses)
- All table row dataclasses and `TableManager` live in `table_manager.py`

## Workflow
**Candidate's Github Data Generation and Processing**
- User (recruiter) inputs a candidate's Github username to `landing.components.ts` in `ui/src/app/landing/` to start a new sync job.
    - `trigger_candidate_refresh()` in `api_gateway.py` starts job process. Generates new job_id, validates freshness of candidate's Github metadata and enqueues job to refresh candidate data.
    - `process_sync_job()` in `sync_worker.py` fetches candidate metadata via `_fetch_repo_metadata()` and tracks job status via `_update_sync_progress()`. Updates repo status to `synced` and enqueues cache worker jobs before exiting.
    - `process_cache_job` in `cache_worker.py` generates and caches repo micro-summaries (zero blob caching for files):
        1. Fetch README and config files from GitHub API in-memory only (no blobs persisted).
        2. Extract config signals in-memory using extractors from `data_filter.py` (no raw config blobs stored).
        3. Generate and persist only the repo micro-summary blob via `generate_repo_micro_summary()` in `summary_manager.py`. Uses README + metadata + extracted configs as input. Marks repo as `summary_ready` in `RepoSyncStatus` on success.
        Job status is updated via `_update_cache_progress()`.

**Data Retrieval**
- `_get_portfolio_bundle()` in `api_gateway.py` is the unified fetcher for portfolio-level data. Returns `{repo_rows, languages_by_repo, statistics}`. Used only by `get_profile()`. Profile summary and query endpoints load micro-summaries directly via `SummaryManager._load_cached_micro_summaries()`.
- `_get_repos_entries(ctx)` in `api_gateway.py` returns all repo metadata entries for a job (used by `get_candidate_repos_metadata()`).
- `_get_single_repo_entry(repo_name, *, ctx)` in `api_gateway.py` returns metadata for a single repository (used by `get_candidate_repo_metadata()`).
- `_build_repo_statistics(languages, github_metadata)` converts raw table rows into the normalized bundle entry structure returned to the UI.
- `_aggregate_portfolio_statistics(repo_rows, languages_by_repo)` computes portfolio-wide metrics (repo_count, stars_total, forks_total, top_languages, topics) separate from per-repo transformation.
- No file blob retrieval is performed by the API. Repo detail views use cached micro-summaries expanded via `expand_repo_micro_summary()` (see AI Summary Pipeline).

**API response for UI**
-  Metadata endpoints returns structured data about candidate's Github activity, including repo metadata, language usage, and portfolio statistics.
    - `get_candidate_repos_metadata()` in `api_gateway.py` retrieves candidate repo metadata for projects view `ui/src/app/projects`.
    -  `get_candidate_repo_metadata()` in `api_gateway.py` retrieves candidate metadata for individual repo view `ui/src/app/projects/project`.
    - `get_profile()` in `api_gateway.py` retrieves candidate profile metadata for profile view `ui/src/app/profile`.
- AI summary endpoints return insights generated via a staged pipeline:
    - `get_repo_summary()` in `api_gateway.py`: expands cached micro-summary into detailed markdown for individual repo view `ui/src/app/projects/project`. Requires job status `completed`. Calls `expand_repo_micro_summary(*, repo_name, job_id)` in `summary_manager.py`; method internally fetches repo metadata and cached micro-summary. Response field: `readme_summary_markdown`.
    - `get_profile_summary()` in `api_gateway.py`: generates profile markdown via `get_or_generate_profile_summary(profile, job_id)` in `summary_manager.py`. Requires job status `completed`. Internally: `_load_cached_micro_summaries()` → `aggregate_micro_summaries()` → `summarize_profile()`. Response field: `summary_markdown`.
    - `portfolio_query()` in `api_gateway.py` (POST `/api/ai`): executes semantic query atomically, returns full JSON. Reads `username` and `query` from POST body.
    - Streaming delivery was evaluated for tier 2 AI responses, but Azure Static Web Apps buffers the Function response path in the current deployment shape. The product path remains atomic JSON delivery unless hosting constraints change in the future.

**Views**
Client has 4 views that retrives and displays data from the server. These views are:
- `ui/src/app/profile`: This expects 2 separate data responses.
    - Metadata: candidate aggregated data (github_profile, statistics, job_metadata) returned via `api_gateway.get_profile()` → `profile.service.getCandidateProfile()`
    - Summary: markdown summary (`summary_markdown`) from aggregated micro-summaries, returned via `api_gateway.get_profile_summary()` → `profile.service.getCandidateSummary()`. Only available when job status is `completed`. UI polls via `waitForFilesReady()` if not yet ready.
- `ui/src/app/projects`: This expects 1 data response
    - Metadata: per-repo metadata list returned via `api_gateway.get_candidate_repos_metadata()` → `repo-bundle.service.getCandidateMetadata()`.
- `ui/src/app/projects/project`: This expects 2 data responses
    - Metadata: single-repo metadata via `api_gateway.get_candidate_repo_metadata()` → `repo-bundle.service.getCandidateRepoMetadata()`
    - Summary: repo markdown summary (`readme_summary_markdown`) via `api_gateway.get_repo_summary()` → `repo-bundle.service.getReadmeSummary()`. UI polls via `pollRepoReady(repoName)` if not yet ready.
- `ui/src/app/ai`: This expects 1 data response
    - Summary: `api_gateway.portfolio_query()` → `assistant.service.askPortfolio()`.
    - Local transcript: chat history is replayed from `chat-history.service.ts`, scoped by candidate username and current session ID, with no backend session storage. History entry is appended after a successful atomic response.

**Job Status**
- Job status is tracked and updated by `get_job_status()` in `api_gateway.py` via `table_manager.py` `JobMetadata` and `RepoSyncStatus`. Per-repo statuses updated during `sync_worker._update_sync_progress()` and `cache_worker._update_cache_progress()`.
- `JobMetadata` job-level status transitions: `queued → syncing → metadata_ready → caching_started → completed` (or `failed` at any stage).
  - `queued`: Initial state, job created
  - `syncing`: First repo metadata sync started
  - `metadata_ready`: All metadata synced (independent of summary generation); repo metadata available for UI display
  - `caching_started`: First micro-summary generation started
    - `completed`: All tracked repos have micro-summaries (or failed); job fully processed
- `RepoSyncStatus` per-repo status transitions: `pending → synced → summary_ready` (or `failed` at any stage). `summary_ready` indicates repo is available for profile aggregation and query context. Incremental refresh jobs create `RepoSyncStatus` rows only for stale repos accepted into that refresh.
- Response fields: `metadata_ready: bool` (true when job status is `metadata_ready` or later), `summary_ready: bool` (true only when job status is `completed`).
- This is a backend-only feature used for debugging, monitoring, and progress feedback.

**Fingerprinting**
- Profile fingerprint: `_refresh_user_profile` is a 1-1 mapping to candidate's Github profile. It is used to track the freshness of candidate's profile data and determine if a new sync is needed.
- Repo fingerprint: `_identify_repo_freshness` generates fingerprint based on metadata and validates freshness during `_fetch_repo_metadata()`. It is used to determine if a repo's data needs to be refreshed during sync. Manages stale blob cleanups via `cleanup_old_repo_github_metadata()` via `reconciliation_worker.py` and `table_manager.py`.

**Data Scoping & Fingerprint Philosophy**

Tables fall into two categories:

*Data tables* — content-addressed, scoped per candidate, shared across jobs. Updated only when the fingerprint changes (i.e., GitHub content changed):
- `RepoGitHubMetadata` — PartitionKey: `username`, RowKey: `repo_name` — has `job_id` FK (provenance) + `fingerprint` FK (staleness)
- `RepoLanguages` — PartitionKey: `{username}:{repo_name}`, RowKey: `language` — has `job_id` FK. Composite PartitionKey prevents cross-candidate collision when two candidates share a repo name.
- `UserProfile` — PartitionKey: `username`, RowKey: `"profile"` — has `job_id` FK + `fingerprint` FK. One row per candidate; updated on fingerprint change.
- `RepoCacheSummary` — PartitionKey: `repo_name`, RowKey: `fingerprint` — **globally shared by design**. Same fingerprint = same content = same micro-summary regardless of which recruiter triggered the scan. Has `job_id` FK for provenance. Blob storage key is always username-scoped via `build_repo_micro_summary_cache_key`.

*Lifecycle tables* — per-job or per-session, track backend events and processing status only:
- `JobMetadata`, `RepoSyncStatus`, `SessionCandidates`

`job_id` = backend lifecycle tracer. Tracks which sync job last wrote data, enables UI data queries, and provides data provenance. Does **not** track GitHub API payloads.
`fingerprint` = content version hash. Updated on demand when GitHub content changes. Drives cache reuse and staleness detection.

**Reconciliation and Cleanup** (in `reconciliation_worker.py`)
- `cleanup_old_jobs` (timer trigger, every 3 hours): Cascade-deletes completed/failed job artifacts after retention period. Preserves at least one job per candidate. Deletes from `JobMetadata`, `RepoLanguages`, `RepoSyncStatus`.
- `cleanup_old_repo_github_metadata` (timer trigger, every 2 hours): Removes `RepoGitHubMetadata` entries not accessed within retention period. Access tracking prevents deletion of frequently validated repos.
- `cleanup_discovered_paths` and `cleanup_old_blob_cache` schedule constants are defined but timer functions are not yet implemented.


**Table Schema Overview**
- `SessionCandidates`: Stores candidate information and their associated session data.
- `JobMetadata`: Tracks metadata for each sync job. Fields: job ID, candidate username, created_at, updated_at, status (`queued | syncing | metadata_ready | caching_started | completed | failed`), trace_id, request_id.
- `RepoLanguages`: Stores programming languages used in repositories with percentages of code written in each language. PartitionKey: `{username}:{repo_name}` (prevents cross-candidate collision), RowKey: `language`. Includes `job_id` FK.
- `RepoGitHubMetadata`: Stores GitHub metadata for repositories: name, description, stars, forks, topics, default branch, fingerprint, last accessed timestamp. PartitionKey: `username`, RowKey: `repo_name`. Includes `job_id` FK.
- `RepoSyncStatus`: Tracks per-repo pipeline status. Valid values: `pending | synced | summary_ready | failed`. `summary_ready` indicates micro-summary generation succeeded and repo is available for profile aggregation and query context.
- `RepoCacheSummary`: Globally shared cache tracking for repo micro-summaries. PartitionKey: `repo_name`, RowKey: `fingerprint`. Same fingerprint = same content = one shared row regardless of candidate. Includes `job_id` FK for provenance. Blob storage is still username-scoped.
- `UserProfile`: Stores cached GitHub user profile metadata for candidates. Fields: username, fingerprint, job_id, full GitHub profile fields (name, bio, company, location, avatar_url, etc.), cached_at.
- `RepoAPIUsage`: Tracks GitHub API usage per operation (metadata fetch, file fetch, etc.).
- `AIRequestUsage`: Tracks AI model request statistics including tokens used, model tier, cost estimation.

**AI Summary Pipeline Overview**
- Config extraction: `data_filter.py` hosts `CONFIG_EXTRACTION_SCHEMAS` mapping filenames to typed extractor functions. Extractors return structured dicts (not raw text). Cache worker applies extractors in-memory during micro-summary generation. No raw config blobs are persisted.
- Repo micro-summary: `generate_repo_micro_summary(*, username, repo_name, fingerprint, job_id, repo_metadata, primary_readme_content, config_content, secondary_readme_content)` runs per-repo in cache worker. Consumes README + metadata + extracted configs. Produces a JSON analysis artifact cached in blob storage via `cache_manager.save()`. Fingerprint-based cache validation avoids regeneration if repo unchanged. Output schema: `{overview, key_features, tech_stack, architecture_patterns, skill_signals}`.
- Repo detail expansion: `expand_repo_micro_summary(*, repo_name, job_id)` expands concise micro-summary into detailed **markdown** for single-repo view via AI call. Internally fetches repo metadata from table and cached micro-summary from blob storage, and reuses a fingerprinted expanded-summary blob cache before regenerating. Returns `{summary_markdown, metadata}`.
- Profile aggregation: `get_or_generate_profile_summary(profile, job_id)` calls `_load_cached_micro_summaries()` and first attempts to reuse a fingerprinted cached aggregate payload before recomputing `aggregate_micro_summaries()`. The aggregate step still feeds `summarize_profile()` to render **markdown**. Returns `{summary_markdown, metadata}`.
- Query: `get_or_generate_query_response(*, job_id, query, profile)` calls `_load_cached_micro_summaries()` → `aggregate_micro_summaries()` → `summarize_query()`. Returns `{response, repositories_used, total_repositories, query, metadata}`. No raw file access during query.


**API Routes Summary**
- `POST /api/candidate/{username}/refresh` - Trigger candidate data refresh, returns job_id and status polling URL
- `GET /api/candidate/{username}/status?job_id={job_id}` - Poll job status and per-repo progress
- `GET /api/candidate/{username}` - Get candidate repo metadata (projects view); requires job status `metadata_ready` or later
- `GET /api/candidate/{username}/{repo}/metadata` - Get single repo metadata (project detail); requires job status `metadata_ready` or later
- `GET /api/candidate/{username}/profile` - Get candidate profile and aggregated statistics
- `GET /api/candidate/{username}/summary` - Get candidate profile markdown summary (`summary_markdown`); requires job status `completed`
- `GET /api/candidate/{username}/{repo}/readme-summary` - Get expanded repo markdown summary (`readme_summary_markdown`); requires job status `completed`
- `POST /api/ai` - Execute semantic query over portfolio; requires job status `completed`; reads `username` and `query` from POST body; returns full JSON
- `GET /api/session/candidates` - Get recently viewed candidates for a session
- `GET /api/health` - Health check

**UI Services** (in `/ui/src/app/services/`)
- `repo-bundle.service.ts` - Fetches repo metadata via `/candidate/{username}` and `/candidate/{username}/{repo}/metadata`. Also handles `getReadmeSummary()` via `/{repo}/readme-summary` and `getSessionCandidates()` via `/session/candidates`.
- `profile.service.ts` - Fetches profile metadata and summary via `/candidate/{username}/profile` and `/candidate/{username}/summary`
- `assistant.service.ts` - AI portfolio queries via atomic POST to `/ai`.
- `chat-history.service.ts` - Stores per-candidate AI chat transcripts in `localStorage`, scoped by session ID. Owns append/load/clear behavior for the AI view MVP.
- `job-polling.service.ts` - Manages job status polling via `/candidate/{username}/status`. Methods: `pollJobStatus()` (poll until terminal), `waitForMetadataReady()` (complete when `metadata_ready=true`), `waitForFilesReady()` (complete when `summary_ready=true`), `pollRepoReady(repoName)` (complete when specific repo is in `summary_ready` state).
- `candidate-context.service.ts` - Tracks active username and candidate list across views; persists to `localStorage`; syncs from session via `getSessionCandidates()`
- `cache.service.ts` - In-memory client-side response caching
- `session-id.service.ts` - Manages session ID tracking
- `request-id.interceptor.ts` - Injects `X-Request-Id` header on all HTTP requests
- `session-id.interceptor.ts` - Injects `X-Session-Id` header on all HTTP requests

**Known Issues / Technical Notes**
- AI model assignments: all summary types (`profile`, `readme`, `query`, `initial_summary`) are explicitly mapped to `gpt-5-nano` tier via `MODEL_ASSIGNMENTS` dict in `summary_manager.py`.
- **Streaming evaluation**: streaming delivery was explored for AI responses, but the current Azure Static Web Apps + Function App deployment path buffers the response. Streaming is documented for future planning only and is not part of the active runtime architecture.
- There currently is no authentication implementation for users and users are rate limit implemented to 5 replacable candidates. Likelihood of hitting both Github and ChatGPT API limits if user grows. 