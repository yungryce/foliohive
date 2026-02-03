<!-- - part of your process should not involve executing scripts, except they are path verificatations or file create or renames. -->
- codebase root: `/home/juk/DEV/cloudfolio`
- index codebase root for ease of reference
- you should assume you have access to all files beyond what is provided in the prompt
- Always strive to reduce complexity and improve code quality
- code smell should be addressed when spotted
- Do not introduce technical debt
- Always prefer explicitness over implicitness
- Always prefer simplicity over complexity
- When my query has a question mark, answer the question first before providing any additional information

# Workflow
User (recruiter) inputs a candidate's Github username to `landing.components.ts` in `ui/src/app/landing/` to start a new sync job.
`trigger_candidate_refresh()` in `api_gateway.py` for starts job operation triggering . This performs reository discovery and then enqueues `process_sync_job()` in `sync_worker.py`
`process_sync_job()` fetches all repo metadata, updates table metadata and status fields and then enqueues `process_cache_job()` in `cache_worker.py`
`process_cache_job()` in `cache_worker.py` performs repository discovery, identifies primary readme, and caches readme files using `cache_manager`