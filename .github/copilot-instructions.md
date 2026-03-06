#  Recruiting analysis tool
**codebase root**: `/home/juk/DEV/foliohive/`
**api root**: `/api/v0.3.0/function-app/function_app.py`
**api gateway**: `/api/v0.3.0/api_gateway/api_gateway.py`
**shared modules**: `/api/v0.3.0/shared/src/foliohive_shared/`
**Table Schema**: `/api/v0.3.0/shared/src/foliohive_shared/table/table_schema.py`
**AI Assistant**: `/api/v0.3.0/shared/src/foliohive_shared/ai/ai_assistant.py`
**Summary Manager**: `/api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`
**Data Filter / Extraction**: `/api/v0.3.0/shared/src/foliohive_shared/ai/data_filter.py`
**Cache Manager**: `/api/v0.3.0/shared/src/foliohive_shared/cache/cache_manager.py`
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
This is an event based system. The main event is `trigger_candidate_refresh` which starts the process. `process_sync_job` fetches metadata and tracks progress. `process_cache_job` caches additional blob data. 
    > `api_gateway.py` -> `sync_worker.py` -> `cache_worker.py`.
The UI retrieves data via `/api_gateway.py` to display candidate profiles, projects, and AI-generated summaries.
`table_manager.py` defines the table schemas used for storing candidate data, including `SessionCandidates`, `JobMetadata`, `RepoLanguages`, `RepoGitHubMetadata`, and `RepoSyncStatus`. These tables are used throughout the data processing and retrieval workflow to manage candidate information and track job statuses.

## Workflow
**Candidate's Github Data Generation and Processing**
- User (recruiter) inputs a candidate's Github username to `landing.components.ts` in `ui/src/app/landing/` to start a new sync job.
    - `trigger_candidate_refresh()` in `api_gateway.py` starts job process. Generates new job_id, validates freshness of candidate's Github metadata and enqueues job to refresh candidate data.
    - `process_sync_job()` in `sync_worker.py` fetches candidate metadata `_fetch_repo_metadata()` and tracks job status `_update_job_progress()`. 
    - `process_cache_job` in `cache_worker.py` runs three sequential steps per repo:
        1. Fetch README files and cache them as blobs via `cache_manager.py`.
        2. Fetch config files, run extractors from `data_filter.py` (`CONFIG_EXTRACTION_SCHEMAS`), and persist extracted JSON artifacts only (no raw config blobs).
        3. Generate and cache a repo micro-summary (`generate_repo_micro_summary()` in `summary_manager.py`) from README + metadata + extracted configs. Marks repo as `summary_ready` in `RepoSyncStatus` on success.
        Job status is updated via `_update_cache_progress()`.

**Data Retrieval**
-  `_aggregate_portfolio_statistics()` via `_get_portfolio_bundle()` in `api_gateway.py` retrieves aggregated metadata from candidate all repos metadata and candidate profile metadata. Portfolio/Profile level insights in the UI.
- `_build_repo_detail_entry` via `_build_repo_statistics()` in `api_gateway.py` retrieves detailed metadata for individual repos to be used for repo level insights in the UI. 
- `get_repo_files()` in `cache_manager.py` via `_get_repo_files()` in `api_gateway.py` retrieves cached README blobs and extracted config JSON artifacts for repos. Config payloads are structured dicts (not raw text). Missing extractions are skipped; missing README blobs fall through gracefully.

**API response for UI**
-  Metadata endpoints returns structured data about candidate's Github activity, including repo metadata, language usage, and portfolio statistics.
    - `get_candidate_repos_metadata()` in `api_gateway.py` retrieves candidate repo metadata for projects view `ui/src/app/projects`.
    -  `get_candidate_repo_metadata()` in `api_gateway.py` retrieves candidate metadata for individual repo view `ui/src/app/projects/project`.
    - `get_profile()` in `api_gateway.py` retrieves candidate profile metadata for profile view `ui/src/app/profile`.
- AI summary endpoints return insights generated via a staged pipeline:
    - `get_repo_summary()` in `api_gateway.py`: retrieves AI-generated HTML summary for individual repo view `ui/src/app/projects/project`. Uses README + extracted configs as context.
    - `get_profile_summary()` in `api_gateway.py`: two-stage aggregation. Loads cached repo micro-summaries → `aggregate_profile_from_summaries()` produces profile JSON → `format_profile_html()` renders HTML. Repos missing micro-summaries are skipped. Final HTML is cached separately.
    - `portfolio_query()` in `api_gateway.py`: loads cached aggregated profile JSON + selected repo micro-summaries filtered by query relevance. Calls `query_from_summaries()` in `summary_manager.py`. Does not re-read raw README or config blobs.

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
- Job status is tracked and updated by `api_gateway.job_job_status()` via `table_manager.py` `JobMetadata` and `RepoSyncStatus`. Status are updated during `sync_worker._update_job_progress()` and `cache_worker._update_cache_progress()`.
- `RepoSyncStatus` per-repo status transitions: `pending → synced → summary_ready` (or `failed` at any stage). `summary_ready` is set after micro-summary generation succeeds.
- This is a backend-only feature used for debugging, monitoring, and progress feedback.

**Fingerprinting**
- Profile fingerprint: `_refresh_user_profile` is a 1-1 mapping to candidate's Github profile. It is used to track the freshness of candidate's profile data and determine if a new sync is needed.
- Repo fingerprint: `_identify_repo_freshness` generates fingerprint based on metadata and validates freshness during `_fetch_repo_metadata()`. It is used to determine if a repo's data needs to be refreshed during sync. Manages stale blob cleanups via `cleanup_old_repo_github_metadata()` via `reconciliation_worker.py` and `table_manager.py`.

**reconciliation and Cleanup**
- `cleanup_old_jobs`: Cron job that cleans up inactive jobs in `JobMetadata`, `RepoSyncStatus`, and `SessionCandidates`.
- `cleanup_old_repo_github_metadata`: Cron job that cleans up stale repo metadata and associated blobs based on fingerprint validation in `RepoSyncStatus`. This ensures we don't keep outdated data and helps manage storage costs.


**Table Schema Overview**
- `SessionCandidates`: Stores candidate information and their associated session data.
- `JobMetadata`: Tracks metadata for each sync job, including job ID, candidate username, start time, end time, and status.
- `RepoLanguages`: Stores information about the programming languages used in each repository, including language name and percentage of code written in that language.
- `RepoGitHubMetadata`: Stores GitHub metadata for each repository, such as stars, forks, issues, and pull requests.
- `RepoSyncStatus`: Tracks per-repo pipeline status. Valid values: `pending | synced | summary_ready | failed`. `summary_ready` indicates micro-summary generation succeeded and the repo is available for profile aggregation and query context.
- `RepoCacheSummaryRow`: Stores cached repo micro-summaries with fingerprint validation.
- `UserProfileRow`: Stores candidate GitHub profile metadata.

**AI Summary Pipeline Overview**
- Config extraction: `data_filter.py` hosts `CONFIG_EXTRACTION_SCHEMAS` mapping filenames to typed extractor functions. Extractors return structured dicts (not raw text). Cache worker applies extractors during config caching; extracted artifacts are stored as JSON blobs. No raw config blobs are persisted.
- Repo micro-summary: `summary_manager.generate_repo_micro_summary()` runs per-repo in cache worker, consuming README + metadata + extracted configs. Produces a JSON analysis artifact cached in blob storage. Output schema: `{overview, key_features, tech_stack, architecture_patterns, skill_signals}`.
- Profile aggregation: `summary_manager.aggregate_profile_from_summaries()` aggregates micro-summary collection into a profile JSON (skill dedup + scoring). `summary_manager.format_profile_html()` renders HTML from that JSON only.
- Query: `summary_manager.query_from_summaries()` filters micro-summaries by query relevance then builds context from profile aggregate + filtered summaries. No raw file access during query.


### NOTES
This project is currently at a proof-of-concept stage. 
- Features are changing rapidly during this early stage, so formal test coverage is not yet a priority. The focus is on iterating quickly and validating end-to-end functionality via manual testing and inspection. As the system stabilizes, more formal unit and integration tests will be added.
- Tests are mostly being done with one Candidate at a time and manually inspecting results in the UI, tables and blob containers.
- Tables are purged between test runs to reset state. This is done via `table_manager.py` functions that delete all rows in `SessionCandidates`, `JobMetadata`, `RepoLanguages`, `RepoGitHubMetadata`, and `RepoSyncStatus`.
- Blob containers are also purged between test runs to reset state. This is done via `cache_manager.py` functions that delete all blobs in the relevant containers.

