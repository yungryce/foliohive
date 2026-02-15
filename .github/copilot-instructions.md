#  Recruiting analysis tool

- codebase root: `/home/juk/DEV/cloudfolio/`
- api root: `/api/v0.3.0/function-app/function_app.py`
- api gateway: `/api/v0.3.0/api_gateway/api_gateway.py`
- shared modules: `/api/v0.3.0/shared/src/foliohive_shared/`
- Table Schema: `/api/v0.3.0/shared/src/foliohive_shared/table/table_schema.py`
- AI Assistant: `/api/v0.3.0/shared/src/foliohive_shared/ai/ai_assistant.py`
- Summary Manager: `/api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`
- ui root: `/ui/src/app/`
- ui services: `/ui/src/app/services/`

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
The recruiting analysis tool is designed to analyze a candidate's Github activity and provide insights into their coding skills and style. The system is built using a microservices architecture, with separate components responsible for data retrieval, processing, caching, and UI rendering.
This is an event based system. The main event is `trigger_candidate_refresh` which starts the process. `process_sync_job` fetches metadata and tracks progress. `process_cache_job` caches additional blob data. The UI retrieves data via API calls to display candidate profiles, projects, and AI-generated summaries.
`table_manager.py` defines the table schemas used for storing candidate data, including `SessionCandidates`, `JobMetadata`, `RepoLanguages`, `RepoGitHubMetadata`, and `RepoSyncStatus`. These tables are used throughout the data processing and retrieval workflow to manage candidate information and job statuses.

## Workflow
**Phase 1: Candidate's Github Data Generation and Processing**
- User (recruiter) inputs a candidate's Github username to `landing.components.ts` in `ui/src/app/landing/` to start a new sync job.
`trigger_candidate_refresh()` in `api_gateway.py` retrieves candidate's Github metadata and enqueues it as a job process to refresh candidate data.
- `process_sync_job()` in `sync_worker.py` continues job process fetching candidate metadata `_fetch_repo_metadata()` and tracking job progress `_update_job_progress()`. This is sufficient to return candidate's background repositories `job_metadata`, `repo_languages`, and `repo_github_metadata` back to the UI for initial display.
- `process_cache_job` in `cache_worker.py` is responsible for caching additional candidate blob data such as README files, config files, language specific files, etc that provide context to the candidate's coding style and skills. `cache_manager.py` handles the specifics of caching candidate blob data.

**Phase 2: UI Data Retrieval**
Client has 4 views that retrives and displays data from the server. These views are:
- `ui/src/app/profile`: This expects 2 separate data responses. metadata
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate aggregated data returned via `api_gateway.get_profiles()`
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`:  ai summary returned via `api_gateway.get_profile_summary()`
- `ui/src/app/projects`: This expects 1 data response
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate per repo metadata used to display repo cards `api_gateway.get_candidate_repos_metadata()`.
- `ui/src/app/projects/project`: This expects 2 data responses
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate per repo metadata already available from `ui/src/app/projects` and `api_gateway.get_candidate_repo_metadata()`. 
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: ai summary of repo details via `api_gateway.get_repo_summary()`
- `ui/src/app/ai`: This expects 1 data response
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: ai summary provided based on user query. All of candidate's data is used as context for query via `api_gateway.portfolio_query()`

**Phase 3: Job Status**
- Job status is tracked and updated in `table_manager.py` tables `JobMetadata` and `RepoSyncStatus`. Status are updated during `sync_worker._update_job_progress()`, `cache_worker._update_cache_progress()` and `reconciliation_worker._reconcile_session()`. This allows event tracking of jobs and their progress

 the UI to update components provide real-time feedback to the user on the progress of their candidate data sync and processing. The UI can query job status via `api_gateway.get_job_status()` which retrieves data from these tables to inform the user of the current state of their candidate's data processing.