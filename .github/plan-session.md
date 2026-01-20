# Backend sync: NEW table needed in table_manager.py
@dataclass
class SessionCandidateRow:
    session_id: str        # PK
    username: str          # RK (or part of composite)
    latest_job_id: str
    last_viewed_at: str
    query_count: int = 0

- New table SessionCandidates in `api/v0.3.0/shared/src/cloudfolio_shared/table/table_manager.py` to track candidates associated with sessions.
```
def upsert_session_candidate(self, session_id: str, username: str, job_id: str) -> None:
    # Called on every /bundles/{username} request
    # Updates latest_job_id and last_viewed_at
```

- Middleware in `api/v0.3.0/function-app/blueprints/api_gateway.py` 
```
# In get_repo_bundle, trigger_bundle_refresh, get_job_status:
trace = _get_trace_context(req)
if trace['session_id'] and username:
    table_manager.upsert_session_candidate(
        trace['session_id'], 
        username, 
        job_id or _fetch_latest_job(username)
    )
```

New endpoint GET /session/candidates:
```
@bp.route(route="session/candidates", methods=["GET"])
def get_session_candidates(req: func.HttpRequest) -> func.HttpResponse:
    trace = _get_trace_context(req)
    session_id = trace['session_id']
    if not session_id:
        return _create_error_response("X-Session-Id required", 400)
    
    candidates = table_manager.list_session_candidates(session_id, limit=10)
    return _create_success_response({"candidates": candidates})
```

# Frontend sync strategy:
Changes to `ui/src/app` 
- Session is loaded first
- Then fetch candidates for that session via new endpoint
- Update candidate-context.service.ts to store/retrieve candidates under new session key
- Update UI components to reflect session-based candidate tracking
