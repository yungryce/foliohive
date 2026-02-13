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
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate per repo metadata used to display repo details `api_gateway.get_candidate_repo_metadata()`. This is a missing gap that has not been implemented
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: ai summary of repo details via `api_gateway.get_repo_summary()`
- `ui/src/app/ai`: This expects 1 data response
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: ai summary provided based on user query. All of candidate's data is used as context for query via `api_gateway.portfolio_query()`

**Helper functions:**
- `getJobStatus`: Works alongside `api_gateway.get_job_status()`to return job `pending`, `synced`, `cached` and `failed`. This is used to return repo metadata produced by trigger `api_gateway.trigger_candidate_refresh()`using table `RepoSyncStatusRow`
- `getCacheStatus`: Works alongside `api_gateway.get_repo_cache_status` This function checks the blob cache status for a given candidate's repo using `RepoSyncStatusRow` (quetionable redundancy with get_job_status). 