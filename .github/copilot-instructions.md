#  Recruiting analysis tool
**codebase root**: `/home/juk/DEV/foliohive/`
**api root**: `/api/v0.3.0/function-app/function_app.py`
**api gateway**: `/api/v0.3.0/function-app/blueprints/api_gateway.py`
**shared modules**: `/api/v0.3.0/shared/src/foliohive_shared/`
**Table Manager**: `/api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`
**AI Assistant**: `/api/v0.3.0/shared/src/foliohive_shared/ai/ai_assistant.py`
**Summary Manager**: `/api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`
**Data Filter / Extraction**: `/api/v0.3.0/shared/src/foliohive_shared/ai/data_filter.py`
**Cache Manager**: `/api/v0.3.0/shared/src/foliohive_shared/cache/cache_manager.py`
**Sync Worker**: `/api/v0.3.0/function-app/blueprints/sync_worker.py`
**Cache Worker**: `/api/v0.3.0/function-app/blueprints/cache_worker.py`
**Reconciliation Worker**: `/api/v0.3.0/function-app/blueprints/reconciliation_worker.py`
**ui root**: `/ui/src/app/`
**ui services**: `/ui/src/app/services/`
**venv**: `/api/v0.3.0/venv/`

- index codebase root for ease of reference
- you should assume you have access to all files beyond what is provided in the prompt
- Always strive to reduce complexity and improve code quality
- code smell should be addressed when spotted
- Do not introduce technical debt
- Always prefer explicitness over implicitness
- Always prefer simplicity over complexity
- When my query has a question mark, answer the question first before providing any additional information
- plans created as files into `.github/plans/`

**Architectural Overview**
The recruiting analysis tool is designed to analyze a candidate's Github activity and provide insights into their coding skills and style. The system is built using a microservices architecture, which separates components responsible for data retrieval, processing, caching, and UI rendering.
This is an event-based system. The main event is `trigger_candidate_refresh` which starts the process. `process_sync_job` fetches metadata and tracks progress. `process_cache_job` generates and caches repo micro-summaries. All row dataclasses and the `TableManager` class live in the same file: `table_manager.py`.
    > `api_gateway.py` -> `sync_worker.py` -> `cache_worker.py`
The UI retrieves data via endpoints in `api_gateway.py` to display candidate profiles, projects, and AI-generated summaries.
`table_manager.py` defines the table schemas and manager class for storing candidate data, including `SessionCandidates`, `JobMetadata`, `RepoLanguages`, `RepoGitHubMetadata`, `RepoSyncStatus`, `RepoCacheSummary`, `UserProfile`, and others. These tables are used throughout the data processing and retrieval workflow to manage candidate information and track job statuses.

## Workflow
**Candidate's Github Data Generation and Processing**
- User (recruiter) inputs a candidate's Github username to `landing.components.ts` in `ui/src/app/landing/` to start a new sync job.
    - `trigger_candidate_refresh()` in `api_gateway.py` starts job process. Generates new job_id, validates freshness of candidate's Github metadata and enqueues job to refresh candidate data.
    - `process_sync_job()` in `sync_worker.py` fetches candidate metadata via `_fetch_repo_metadata()` and tracks job status via `_update_job_progress()`. Updates repo status to `synced` and enqueues cache worker jobs before exiting.
    - `process_cache_job` in `cache_worker.py` generates and caches repo micro-summaries (zero blob caching for files):
        1. Fetch README and config files from GitHub API in-memory only (no blobs persisted).
        2. Extract config signals in-memory using extractors from `data_filter.py` (no raw config blobs stored).
        3. Generate and persist only the repo micro-summary blob via `generate_repo_micro_summary()` in `summary_manager.py`. Uses README + metadata + extracted configs as input. Marks repo as `summary_ready` in `RepoSyncStatus` on success.
        Job status is updated via `_update_cache_progress()`.

**Data Retrieval**
- `_get_portfolio_bundle()` in `api_gateway.py` is the unified fetcher for all portfolio data. It retrieves aggregated metadata from all repos and candidate profile, returning repo rows, language stats, and portfolio-level statistics. Used by `get_profile()`, `get_profile_summary()`, and `portfolio_query()` endpoints.
- `_build_repo_detail_entry()` in `api_gateway.py` retrieves detailed metadata for individual repos via `_build_repo_statistics()`. Used for single-repo detail views.
- No file blob retrieval is performed by the API. Repo detail views use cached micro-summaries expanded via `expand_repo_micro_summary()` (see AI Summary Pipeline).

**API response for UI**
-  Metadata endpoints returns structured data about candidate's Github activity, including repo metadata, language usage, and portfolio statistics.
    - `get_candidate_repos_metadata()` in `api_gateway.py` retrieves candidate repo metadata for projects view `ui/src/app/projects`.
    -  `get_candidate_repo_metadata()` in `api_gateway.py` retrieves candidate metadata for individual repo view `ui/src/app/projects/project`.
    - `get_profile()` in `api_gateway.py` retrieves candidate profile metadata for profile view `ui/src/app/profile`.
- AI summary endpoints return insights generated via a staged pipeline:
    - `get_repo_summary()` in `api_gateway.py`: expands cached micro-summary into detailed HTML for individual repo view `ui/src/app/projects/project`. Uses cached micro-summary only (no raw file access). Calls `expand_repo_micro_summary(*)` in `summary_manager.py` with signature `(*, repo_name, micro_summary, repo_metadata, fingerprint)`.
    - `get_profile_summary()` in `api_gateway.py`: generates profile HTML via `get_or_generate_profile_summary()` in `summary_manager.py`. Internally: loads cached repo micro-summaries → `aggregate_micro_summaries()` produces profile JSON → `format_profile_html()` renders HTML. Repos missing micro-summaries are skipped. Final HTML is cached separately.
    - `portfolio_query()` in `api_gateway.py` (POST): executes semantic query over candidate portfolio. Loads cached aggregated profile JSON + selected repo micro-summaries filtered by query relevance. Does not re-read raw README or config blobs.

**Views**
Client has 4 views that retrives and displays data from the server. These views are:
- `ui/src/app/profile`: This expects 2 separate data responses.
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate aggregated data returned via `api_gateway.get_profiles()`
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: HTML summary from aggregated micro-summaries, returned via `api_gateway.get_profile_summary()`. Partial results are returned when some repos lack micro-summaries.
- `ui/src/app/projects`: This expects 1 data response
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate per repo metadata used to display repo cards `api_gateway.get_candidate_repos_metadata()`.
- `ui/src/app/projects/project`: This expects 2 data responses
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate per repo metadata already available from `ui/src/app/projects` and `api_gateway.get_candidate_repo_metadata()`. 
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: ai summary of repo details via `api_gateway.get_repo_summary()`
- `ui/src/app/ai`: This expects 1 data response
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: query response from `query_from_summaries()` via `api_gateway.portfolio_query()`. Context is built from cached profile aggregate JSON + query-relevant repo micro-summaries only (no raw files).

**Job Status**
- Job status is tracked and updated by `get_job_status()` in `api_gateway.py` via `table_manager.py` `JobMetadata` and `RepoSyncStatus`. Per-repo statuses updated during `sync_worker._update_job_progress()` and `cache_worker._update_cache_progress()`.
- `JobMetadata` job-level status transitions: `queued → syncing → metadata_ready → caching_started → completed` (or `failed` at any stage).
  - `queued`: Initial state, job created
  - `syncing`: First repo metadata sync started
  - `metadata_ready`: All metadata synced (independent of summary generation); repo metadata available for UI display
  - `caching_started`: First micro-summary generation started
  - `completed`: All repos have micro-summaries (or failed); job fully processed
- `RepoSyncStatus` per-repo status transitions: `pending → synced → summary_ready` (or `failed` at any stage). `summary_ready` indicates repo is available for profile aggregation and query context.
- Response fields: `metadata_ready: bool` (true when job status is `metadata_ready` or later), `summary_ready: bool` (true only when job status is `completed`).
- This is a backend-only feature used for debugging, monitoring, and progress feedback.

**Fingerprinting**
- Profile fingerprint: `_refresh_user_profile` is a 1-1 mapping to candidate's Github profile. It is used to track the freshness of candidate's profile data and determine if a new sync is needed.
- Repo fingerprint: `_identify_repo_freshness` generates fingerprint based on metadata and validates freshness during `_fetch_repo_metadata()`. It is used to determine if a repo's data needs to be refreshed during sync. Manages stale blob cleanups via `cleanup_old_repo_github_metadata()` via `reconciliation_worker.py` and `table_manager.py`.

**Reconciliation and Cleanup** (in `reconciliation_worker.py`)
- `cleanup_old_jobs` (timer trigger): Cleans up inactive jobs in `JobMetadata`, `RepoSyncStatus`, and `SessionCandidates` based on age threshold.
- `cleanup_old_repo_github_metadata` (timer trigger): Cleans up stale repo metadata and associated blobs based on fingerprint validation in `RepoSyncStatus`. Avoids orphaned data and manages storage costs.
- `cleanup_discovered_paths` (timer trigger, every 2 hours): Cleans up cached file paths no longer needed.
- `cleanup_old_blob_cache` (timer trigger, every 6 hours): Cleans up expired blobs from cache storage.


**Table Schema Overview**
- `SessionCandidates`: Stores candidate information and their associated session data.
- `JobMetadata`: Tracks metadata for each sync job. Fields: job ID, candidate username, created_at, updated_at, status (`queued | syncing | metadata_ready | caching_started | completed | failed`), force_refresh flag, trace_id, request_id.
- `RepoLanguages`: Stores programming languages used in repositories with percentages of code written in each language.
- `RepoGitHubMetadata`: Stores GitHub metadata for repositories: name, description, stars, forks, topics, default branch, fingerprint, last accessed timestamp.
- `RepoSyncStatus`: Tracks per-repo pipeline status. Valid values: `pending | synced | summary_ready | failed`. `summary_ready` indicates micro-summary generation succeeded and repo is available for profile aggregation and query context.
- `RepoCacheSummary`: Stores cached repo micro-summaries with fingerprint validation. Fields: username, repo_name, fingerprint, cache_key (blob storage key), cache_status, generated_at.
- `UserProfile`: Stores cached GitHub user profile metadata for candidates. Fields: username, profile data JSON, fingerprint, last_accessed.
- `RepoAPIUsage`: Tracks GitHub API usage per operation (metadata fetch, file fetch, etc.).
- `AIRequestUsage`: Tracks AI model request statistics including tokens used, model tier, cost estimation.

**AI Summary Pipeline Overview**
- Config extraction: `data_filter.py` hosts `CONFIG_EXTRACTION_SCHEMAS` mapping filenames to typed extractor functions. Extractors return structured dicts (not raw text). Cache worker applies extractors in-memory during micro-summary generation. No raw config blobs are persisted.
- Repo micro-summary: `generate_repo_micro_summary(*, repo_name, fingerprint, job_id, repo_metadata, primary_readme_content, config_content, secondary_readme_content)` runs per-repo in cache worker. Consumes README + metadata + extracted configs. Produces a JSON analysis artifact cached in blob storage via `cache_manager.save()`. Fingerprint-based cache validation avoids regeneration if repo unchanged. Output schema: `{overview, key_features, tech_stack, architecture_patterns, skill_signals}`.
- Repo detail expansion: `expand_repo_micro_summary(*, repo_name, micro_summary, repo_metadata, fingerprint)` expands concise micro-summary into detailed HTML for single-repo view via AI call.
- Profile aggregation: `get_or_generate_profile_summary(*, job_id, profile, repo_rows, statistics)` internally calls `aggregate_micro_summaries()` to aggregate micro-summary collection into profile JSON (skill dedup + scoring), then `format_profile_html()` to render HTML from aggregated data only.
- Query: `query_from_summaries(*, query, profile_aggregate, repo_micro_summaries, max_repos)` filters micro-summaries by semantic relevance, builds context from profile aggregate + filtered summaries. No raw file access during query.


**API Routes Summary**
- `POST /api/candidate/{username}/refresh` - Trigger candidate data refresh, returns job_id and status polling URL
- `GET /api/candidate/{username}/status?job_id={job_id}` - Poll job status and per-repo progress
- `GET /api/candidate/{username}` - Get candidate repo metadata (projects view)
- `GET /api/candidate/{username}/{repo}/metadata` - Get single repo metadata (project detail)
- `GET /api/candidate/{username}/profile` - Get candidate profile and aggregated statistics
- `GET /api/candidate/{username}/summary` - Get candidate profile HTML summary (generated when job completed)
- `GET /api/candidate/{username}/{repo}/readme-summary` - Get expanded repo HTML summary (using cached micro-summary)
- `POST /api/ai` - Execute semantic query over portfolio
- `GET /api/session/candidates` - Get recently viewed candidates for a session

**UI Services** (in `/ui/src/app/services/`)
- `repo-bundle.service.ts` - Fetches repo metadata via `/candidate/{username}` and `/candidate/{username}/{repo}/metadata`
- `profile.service.ts` - Fetches profile metadata and summary via `/candidate/{username}/profile` and `/candidate/{username}/summary`
- `assistant.service.ts` - Handles AI query and repo summary via `/ai` (POST) and `/{repo}/readme-summary`
- `job-polling.service.ts` - Manages job status polling via `/candidate/{username}/status`
- `candidate-context.service.ts` - Tracks active username/job context across views
- `cache.service.ts` - In-memory client-side response caching
- `session-id.service.ts` - Manages session ID tracking
- `request-id.interceptor.ts` - Injects `X-Request-Id` header on all HTTP requests
- `session-id.interceptor.ts` - Injects `X-Session-Id` header on all HTTP requests

**Known Issues / Technical Notes**
- UI service `assistant.service.ts` expects response field `readme_summary_html` from `get_repo_summary()` route, but backend returns `summary_html`. This field name mismatch causes the UI to receive `undefined`.
- AI model assignments: all summary types (`profile`, `readme`, `query`, `initial_summary`) default to `gpt-5-nano` tier.

### NOTES
This project is currently at a proof-of-concept stage. 
- Features are changing rapidly during this early stage, so formal test coverage is not yet a priority. The focus is on iterating quickly and validating end-to-end functionality via manual testing and inspection. As the system stabilizes, more formal unit and integration tests will be added.
- Tests are mostly being done with one Candidate at a time and manually inspecting results in the UI, tables and blob containers.
- Tables are purged between test runs to reset state. This is done via `table_manager.py` functions that delete all rows in `SessionCandidates`, `JobMetadata`, `RepoLanguages`, `RepoGitHubMetadata`, and `RepoSyncStatus`.
- Blob containers are also purged between test runs to reset state. This is done via `cache_manager.py` functions that delete all blobs in the relevant containers.

