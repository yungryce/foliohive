- codebase root: `/home/juk/DEV/cloudfolio/`
- api root: `/api/v0.3.0/function-app/function_app.py`
- api gateway: `/api/v0.3.0/api_gateway/api_gateway.py`
- shared modules: `/api/v0.3.0/shared/src/cloudfolio_shared/`
- Table Schema: `/api/v0.3.0/shared/src/cloudfolio_shared/table/table_schema.py`
- AI Assistant: `/api/v0.3.0/shared/src/cloudfolio_shared/ai/ai_assistant.py`
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

# Workflow
**Phase 1: Candidate's Github Data Retrieval**
- User (recruiter) inputs a candidate's Github username to `landing.components.ts` in `ui/src/app/landing/` to start a new sync job.
`trigger_candidate_refresh()` in `api_gateway.py` retrieves candidate's Github metadata and enqueues it as a job process to refresh candidate data. 
- `process_sync_job()` in `sync_worker.py` continues job process fetching candidate metadata `_fetch_repo_metadata()` and tracking job progress `_update_job_progress()`. This is sufficient to return candidate's background repositories `job_metadata`, `repo_languages`, and `repo_github_metadata` back to the UI for initial display.
- `process_cache_job` in `cache_worker.py` is responsible for caching additional candidate blob data such as README files, config files, language specific files, etc that provide context to the candidate's coding style and skills. `cache_manager.py` handles the specifics of caching candidate blob data.

**Phase 2: UI Data Retrieval**
- Candidate's summary page: Not implemented yet, but would involve the following flow: 
  - `get_profile()` in `api_gateway.py`calls the Github API to get candidate's metadata from GET https://api.github.com/users/{username} (not yet implemented), aggregates this data with existing job_metadata, repo_github_metadata, and repo_languages data from table database schema `table_manager.py` providing overvieew/summary of candidate's Github profile and repositories for `profile.component.ts`.
  - `profile.component.ts` in `ui/src/app/profile/` retrieves candidate aggregated summary data from `get_profile()` in `api_gateway.py`.
  - `get_candidate_summary()` in `api_gateway.py` also uses aggregated data to query `AIAssistant` in `ai_assistant.py` esponding back to `profile.component.ts` ideally as flashes.
  - Providing a candidates username `landing.component.ts` would link to the candidate summary page.
- Candidates repository list page:
  - `triggerBuild()` polls `get_job_status()` in `api_gateway.py` to get current job status until metadata is ready or job is complete.
  - `get_candiddate()` in `api_gateway.py` retrieves candidate repository list data from the database using `table_manager.py` to access the relevant tables. returns response to `projects.component.ts` in `ui/src/app/profile/` displaying repository details and metadata as cards.
- Candidate's repository detail page:
  - `project.component.ts` retrieves specific repository details. This includes repository metadata or any relevant data fields aggregated from tables. It also retrieves summary returned by `get_repo_readme_summary()` as its main display content.
  - `get_repo_readme_summary()` in `api_gateway.py` uses aggregated relevant metadata for concerned repo, and specific readme/config file content returned during `process_cache_job` as context to query `AIAssistant` in `ai_assistant.py` and build relevant summary of said repo. 
- AI Assistant query page:
  - `table_manager.py` provides database schema used to derive aggregated candidate data for AI assistant queries.
  - `cache_worker.py` ensures candidate blob data is cached for AI assistant queries. Also supports caching additional Github config files/data as needed.
  - `ai.component.ts` in `ui/src/app/ai/` passes user queries to `ai_assistant.py` via `portfolio_query()` in `api_gateway.py`. Queries are matched against candidates data and cached blob data to provide contextually relevant AI-generated responses useful to a recruiter (user).
  
 

Consolidate's required get_profile database schema with existing table fields in `table_manager.py` to support candidate summary page data retrieval.