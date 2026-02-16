- `ui`: refactor opportunities in `ui/src/app` for clear seperation of concerns and implementation precision and synchronization with `api/v0.3.0/function-app/blueprints/api_gateway.py` data responses. Each service in `foliohive/ui/src/app/services` should serve its component. shared services should be in a shared location


 Critical Path Optimization
Rule: Never block critical content behind non-critical operations.

Data Type	Speed	Critical?	Load Strategy
Repository metadata	Fast (50-200ms)	✅ Critical	Load immediately
Profile metadata	Fast (50-200ms)	✅ Critical	Load immediately
Repository list	Fast (100-300ms)	✅ Critical	Load immediately
AI summaries	Slow (2-10s)	❌ Enhancement	Load progressively
File cache	Async (varies)	❌ Background	Poll/wait as needed




[2026-02-16T13:59:00.014Z] [JOB_FAILED] job=23fe8167-93fe-4912-bb2b-a5196240ab5a - All cache jobs failed
[2026-02-16T13:59:00.018Z] Executed 'Functions.process_cache_job' (Failed, Id=26d70db4-da93-4a31-8fd7-4736459f7a3c, Duration=569ms)
[2026-02-16T13:59:00.019Z] System.Private.CoreLib: Exception while executing function: Functions.process_cache_job. System.Private.CoreLib: Result: Failure
[2026-02-16T13:59:00.019Z] Type: 
[2026-02-16T13:59:00.019Z] Exception: TypeError: CacheManager.save() missing 1 required keyword-only argument: 'fingerprint'
[2026-02-16T13:59:00.019Z] Stack:   File "/home/juk/.nvm/versions/node/v24.13.0/lib/node_modules/azure-functions-core-tools/bin/workers/python/3.13/LINUX/X64/azure_functions_runtime/handle_event.py", line 244, in invocation_request
[2026-02-16T13:59:00.019Z]     call_result = await _loop.run_in_executor(
[2026-02-16T13:59:00.019Z]   File "/home/juk/.pyenv/versions/3.13.0/lib/python3.13/concurrent/futures/thread.py", line 58, in run
[2026-02-16T13:59:00.019Z]     result = self.fn(*self.args, **self.kwargs)
[2026-02-16T13:59:00.019Z]   File "/home/juk/.nvm/versions/node/v24.13.0/lib/node_modules/azure-functions-core-tools/bin/workers/python/3.13/LINUX/X64/azure_functions_runtime/utils/executor.py", line 32, in run_sync_func
[2026-02-16T13:59:00.019Z]     return result(params)
[2026-02-16T13:59:00.019Z]   File "/home/juk/.nvm/versions/node/v24.13.0/lib/node_modules/azure-functions-core-tools/bin/workers/python/3.13/LINUX/X64/azure_functions_runtime/utils/executor.py", line 20, in execute_sync
[2026-02-16T13:59:00.019Z]     return function(**args)
[2026-02-16T13:59:00.019Z]   File "/home/juk/cloudfolio/foliohive/api/v0.3.0/function-app/blueprints/cache_worker.py", line 409, in process_cache_job
[2026-02-16T13:59:00.019Z]     _fetch_and_cache_files(username, repo_name, fingerprint, job_id=job_id)
[2026-02-16T13:59:00.019Z]   File "/home/juk/cloudfolio/foliohive/api/v0.3.0/function-app/blueprints/cache_worker.py", line 106, in _fetch_and_cache_files
[2026-02-16T13:59:00.019Z]     discovery = repo_manager.discover_repo_files(
[2026-02-16T13:59:00.019Z]   File "/home/juk/cloudfolio/foliohive/api/v0.3.0/shared/src/foliohive_shared/github/github_repo_manager.py", line 327, in discover_repo_files
[2026-02-16T13:59:00.019Z]     path_index = self.get_repo_path_index(username=username, repo=repo, usage=usage)
[2026-02-16T13:59:00.019Z]   File "/home/juk/cloudfolio/foliohive/api/v0.3.0/shared/src/foliohive_shared/cache/cache_manager.py", line 509, in wrapper
[2026-02-16T13:59:00.019Z]     self.save(cache_key, result, ttl=ttl)
[2026-02-16T13:59:00.019Z] .