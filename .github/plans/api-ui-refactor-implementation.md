# API Gateway & UI Refactor Implementation Guide

**Goal**: Eliminate repeated operations across functions, optimize data retrieval, separate concerns clearly, and align UI services with API responses.

**Design Decisions**:
- Split metadata/summary endpoints (avoid metadata waiting for AI summary)
- Status endpoints are read-only; no side-effect enqueueing
- Workers (sync_worker, cache_worker) own all job enqueueing
- Each UI service serves its feature; shared infra lives in core/shared

---

## Current Issues

### API Gateway Problems

1. **N+1 Query Pattern**: `get_candidate_bundle()` calls `get_repo_metadata()` in a loop (1 query per repo)
   - Location: [api/v0.3.0/function-app/blueprints/api_gateway.py](api/v0.3.0/function-app/blueprints/api_gateway.py#L200-L220)
   - Impact: O(N) table queries for every bundle/profile request

2. **Status Side Effects**: `get_repo_cache_status()` enqueues cache jobs when state is `synced` or `cache_in_progress`
   - Location: [api/v0.3.0/function-app/blueprints/api_gateway.py](api/v0.3.0/function-app/blueprints/api_gateway.py#L350)
   - Impact: Aggressive polling causes repeated enqueueing; logic belongs in workers

3. **Overlapping Status Endpoints**: Both `get_job_status()` and `get_repo_cache_status()` read `RepoSyncStatusRow`
   - Confusion: When is job "done"? What's the difference between endpoints?
   - Impact: UI polls both for same underlying state

4. **Repeated Request Parsing**: Username/repo validation, session resolution, job resolution duplicated across 8+ endpoints
   - No shared helpers for: param validation, error responses, session/job lookup
   - Impact: Code duplication, inconsistent error messages

5. **Metadata Retrieval Inefficiency**: `_batch_get_repo_metadata()` does linear search per repo (O(N²))
   - Location: [api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py](api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py)
   - Should use dict/lookup for O(1) access

### UI Issues

1. **Endpoint Name Mismatch**: UI calls `getRepoMetadata()` but API implements `get_candidate_repos_metadata()`
   - Location: [ui/src/app/services/repo-bundle.service.ts](ui/src/app/services/repo-bundle.service.ts)
   - Impact: 404 errors or incorrect fallback behavior

2. **Response Shape Mismatch**: API returns `cache_metadata` but UI expects `cacheStatus`
   - Impact: Data not accessible to components

3. **Service Organization**: Cross-cutting concerns mixed with feature logic
   - ConfigService, SessionIdService, CacheService used everywhere
   - No clear "core API client" pattern

4. **Aggressive Polling**: 2-5 second intervals without backoff or conditional requests
   - AI page: 3s × 40 attempts
   - Projects: 5s × 24 attempts
   - Repo detail: 2s × 30 attempts per repo

---

## Implementation Plan

### Phase 1: API Gateway - Eliminate Repeated Operations

#### Step 1.1: Create Request Helpers Module

**File**: `api/v0.3.0/function-app/blueprints/request_helpers.py`

```python
"""Shared request parsing and validation helpers."""
from azure.functions import HttpRequest, HttpResponse
import logging

logger = logging.getLogger(__name__)

def validate_username(username: str) -> HttpResponse | None:
    """Validate GitHub username format. Returns error response or None."""
    if not username or not username.strip():
        return HttpResponse(
            json.dumps({"error": "Username is required"}),
            status_code=400,
            mimetype="application/json"
        )
    if len(username) > 39:  # GitHub limit
        return HttpResponse(
            json.dumps({"error": "Username exceeds maximum length"}),
            status_code=400,
            mimetype="application/json"
        )
    return None

def validate_repo_name(repo_name: str) -> HttpResponse | None:
    """Validate repository name format. Returns error response or None."""
    if not repo_name or not repo_name.strip():
        return HttpResponse(
            json.dumps({"error": "Repository name is required"}),
            status_code=400,
            mimetype="application/json"
        )
    return None

def get_session_id(req: HttpRequest) -> tuple[str | None, HttpResponse | None]:
    """
    Extract and validate session ID from request headers.
    Returns: (session_id, error_response)
    If error_response is not None, caller should return it immediately.
    """
    session_id = req.headers.get("X-Session-Id")
    if not session_id:
        error = HttpResponse(
            json.dumps({"error": "X-Session-Id header is required"}),
            status_code=400,
            mimetype="application/json"
        )
        return None, error
    return session_id, None

def resolve_job(
    table_manager,
    username: str,
    job_id: str | None = None,
    allow_latest: bool = True
) -> tuple[dict | None, HttpResponse | None]:
    """
    Resolve job by explicit job_id or latest job for username.
    Returns: (job_dict, error_response)
    If error_response is not None, caller should return it immediately.
    """
    if job_id:
        job = table_manager.get_job_metadata(job_id)
        if not job:
            error = HttpResponse(
                json.dumps({"error": f"Job {job_id} not found"}),
                status_code=404,
                mimetype="application/json"
            )
            return None, error
        if job.get("candidate_username") != username:
            error = HttpResponse(
                json.dumps({"error": "Job does not belong to this candidate"}),
                status_code=403,
                mimetype="application/json"
            )
            return None, error
        return job, None
    
    if allow_latest:
        job = table_manager.get_latest_job_for_candidate(username)
        if not job:
            error = HttpResponse(
                json.dumps({"error": f"No jobs found for candidate {username}"}),
                status_code=404,
                mimetype="application/json"
            )
            return None, error
        return job, None
    
    error = HttpResponse(
        json.dumps({"error": "job_id parameter is required"}),
        status_code=400,
        mimetype="application/json"
    )
    return None, error

def error_response(message: str, status_code: int = 500) -> HttpResponse:
    """Create standardized error response."""
    return HttpResponse(
        json.dumps({"error": message}),
        status_code=status_code,
        mimetype="application/json"
    )

def success_response(data: dict, status_code: int = 200) -> HttpResponse:
    """Create standardized success response."""
    return HttpResponse(
        json.dumps(data),
        status_code=status_code,
        mimetype="application/json"
    )
```

**Changes in `api_gateway.py`**: Replace all inline validation/session/job lookups with calls to these helpers.

---

#### Step 1.2: Fix N+1 Metadata Query

**File**: `api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`

**Add new method**:
```python
def batch_get_repos_metadata(self, job_id: str, repo_names: list[str]) -> dict[str, dict]:
    """
    Fetch metadata for multiple repos in a single query.
    Returns: dict mapping repo_name -> metadata_dict
    """
    if not repo_names:
        return {}
    
    # Query all repos for this job at once
    partition_key = job_id
    all_repos = self.query_entities(
        table_name=self.REPO_METADATA_TABLE,
        query_filter=f"PartitionKey eq '{partition_key}'"
    )
    
    # Build lookup dict
    result = {}
    for entity in all_repos:
        repo_name = entity.get("RowKey", "")
        if repo_name in repo_names:
            result[repo_name] = {
                "repo_name": repo_name,
                "languages": entity.get("languages"),
                "github_metadata": entity.get("github_metadata"),
                "readme_blob_url": entity.get("readme_blob_url"),
                "discovered_paths": entity.get("discovered_paths"),
                # ... other fields
            }
    
    return result
```

**Update `get_candidate_bundle()`** in `api_gateway.py`:
```python
def get_candidate_bundle(username: str, job_id: str) -> dict:
    # ... existing job/status lookup ...
    
    # Get all repos for job (just names)
    repo_status_list = table_manager.get_repos_for_job(job_id)
    repo_names = [r["repo_name"] for r in repo_status_list]
    
    # Single batch query instead of N queries
    repos_metadata_dict = table_manager.batch_get_repos_metadata(job_id, repo_names)
    
    # Assemble bundle
    bundle = {
        "candidate_username": username,
        "job_id": job_id,
        "repositories": [repos_metadata_dict.get(name, {}) for name in repo_names]
    }
    return bundle
```

---

#### Step 1.3: Make Status Endpoints Read-Only

**Current**: `get_repo_cache_status()` enqueues cache jobs as side effect

**Change**: Remove all `queue_manager.enqueue_*` calls from status endpoints

**File**: `api/v0.3.0/function-app/blueprints/api_gateway.py`

**Before**:
```python
@bp.route("/candidate/<username>/<repo>/cache-status")
def get_repo_cache_status(req: HttpRequest) -> HttpResponse:
    # ... validation ...
    status = table_manager.get_repo_sync_status(job_id, repo_name)
    
    if status.cache_state == "synced":
        # DON'T DO THIS:
        queue_manager.enqueue_cache_job(job_id, repo_name)
    
    return success_response({"cache_state": status.cache_state})
```

**After**:
```python
@bp.route("/candidate/<username>/<repo>/cache-status")
def get_repo_cache_status(req: HttpRequest) -> HttpResponse:
    """Read-only status check. Does NOT enqueue work."""
    session_id, err = get_session_id(req)
    if err:
        return err
    
    err = validate_username(username)
    if err:
        return err
    
    err = validate_repo_name(repo)
    if err:
        return err
    
    job, err = resolve_job(table_manager, username, req.params.get("job_id"))
    if err:
        return err
    
    status = table_manager.get_repo_sync_status(job["job_id"], repo)
    
    return success_response({
        "repo_name": repo,
        "sync_state": status.sync_state,
        "cache_state": status.cache_state,
        "cache_progress": status.cache_progress,
        "updated_at": status.timestamp.isoformat()
    })
```

**Worker Responsibility**: Ensure `sync_worker.py` enqueues cache jobs when sync completes.

---

#### Step 1.4: Consolidate Status Endpoints

**Design**: Single endpoint returning job-level status + per-repo rollup

**New Endpoint**: `GET /candidate/{username}/status?job_id={id}`

**File**: `api/v0.3.0/function-app/blueprints/api_gateway.py`

```python
@bp.route("/candidate/<username>/status")
def get_candidate_status(req: HttpRequest) -> HttpResponse:
    """
    Get comprehensive status for a candidate's sync job.
    Returns job-level status + per-repo sync/cache states.
    """
    session_id, err = get_session_id(req)
    if err:
        return err
    
    err = validate_username(username)
    if err:
        return err
    
    job, err = resolve_job(table_manager, username, req.params.get("job_id"))
    if err:
        return err
    
    job_id = job["job_id"]
    
    # Get job-level metadata
    job_meta = table_manager.get_job_metadata(job_id)
    
    # Get all repo statuses in one query
    repo_statuses = table_manager.get_repos_for_job(job_id)
    
    # Compute rollup
    total_repos = len(repo_statuses)
    synced_count = sum(1 for r in repo_statuses if r["sync_state"] == "synced")
    cached_count = sum(1 for r in repo_statuses if r["cache_state"] == "cached")
    failed_count = sum(1 for r in repo_statuses if r["sync_state"] == "failed")
    
    overall_state = "pending"
    if failed_count > 0:
        overall_state = "failed"
    elif cached_count == total_repos:
        overall_state = "cached"
    elif synced_count == total_repos:
        overall_state = "synced"
    elif synced_count > 0:
        overall_state = "syncing"
    
    return success_response({
        "job_id": job_id,
        "candidate_username": username,
        "overall_state": overall_state,
        "progress": {
            "total_repos": total_repos,
            "synced": synced_count,
            "cached": cached_count,
            "failed": failed_count
        },
        "repositories": [
            {
                "repo_name": r["repo_name"],
                "sync_state": r["sync_state"],
                "cache_state": r["cache_state"],
                "cache_progress": r.get("cache_progress", 0)
            }
            for r in repo_statuses
        ],
        "started_at": job_meta.get("started_at"),
        "updated_at": job_meta.get("updated_at")
    })
```

**Deprecation Path**:
- Mark old `get_job_status()` and `get_repo_cache_status()` as deprecated
- Update UI to use new consolidated endpoint
- Remove old endpoints in next major version

---

#### Step 1.5: Add Conditional Request Support

**For metadata endpoints that are stable after sync**, add ETag support:

**File**: `api/v0.3.0/function-app/blueprints/api_gateway.py`

```python
import hashlib

def compute_etag(data: dict) -> str:
    """Compute ETag from response data."""
    content = json.dumps(data, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]

@bp.route("/candidate/<username>/repos")
def get_candidate_repos_metadata(req: HttpRequest) -> HttpResponse:
    """Get metadata for all candidate repos with ETag support."""
    # ... validation and data retrieval ...
    
    response_data = {
        "candidate_username": username,
        "job_id": job_id,
        "repositories": repos_metadata
    }
    
    etag = compute_etag(response_data)
    
    # Check If-None-Match
    if_none_match = req.headers.get("If-None-Match")
    if if_none_match == etag:
        return HttpResponse(status_code=304)  # Not Modified
    
    return HttpResponse(
        json.dumps(response_data),
        status_code=200,
        mimetype="application/json",
        headers={"ETag": etag, "Cache-Control": "private, max-age=60"}
    )
```

---

### Phase 2: UI Service Reorganization

#### Step 2.1: Create Core API Client

**New File**: `ui/src/app/core/services/api-client.service.ts`

```typescript
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { ConfigService } from './config.service';
import { SessionIdService } from './session-id.service';

export interface ApiResponse<T> {
  data?: T;
  error?: string;
}

@Injectable({
  providedIn: 'root'
})
export class ApiClientService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  private session = inject(SessionIdService);
  
  private get baseUrl(): string {
    return this.config.getApiUrl();
  }
  
  private getHeaders(): HttpHeaders {
    return new HttpHeaders({
      'Content-Type': 'application/json',
      'X-Session-Id': this.session.getSessionId()
    });
  }
  
  get<T>(path: string, params?: HttpParams, etag?: string): Observable<T> {
    let headers = this.getHeaders();
    if (etag) {
      headers = headers.set('If-None-Match', etag);
    }
    
    return this.http.get<T>(`${this.baseUrl}${path}`, { 
      headers, 
      params,
      observe: 'response'
    }).pipe(
      map(response => {
        if (response.status === 304) {
          // Return cached data, handled by caller
          return null as T;
        }
        return response.body as T;
      }),
      catchError(error => {
        console.error(`API GET ${path} failed:`, error);
        return throwError(() => error);
      })
    );
  }
  
  post<T>(path: string, body: any): Observable<T> {
    return this.http.post<T>(`${this.baseUrl}${path}`, body, {
      headers: this.getHeaders()
    }).pipe(
      catchError(error => {
        console.error(`API POST ${path} failed:`, error);
        return throwError(() => error);
      })
    );
  }
}
```

---

#### Step 2.2: Reorganize Services by Feature

**New Structure**:
```
ui/src/app/
  core/
    services/
      api-client.service.ts          # HTTP wrapper with session/headers
      config.service.ts                # Environment config
      session-id.service.ts            # Session management
      cache.service.ts                 # Local storage/caching
  features/
    candidate/
      services/
        candidate-status.service.ts    # Job/cache status polling
        candidate-metadata.service.ts  # Profile + repos metadata
    profile/
      services/
        profile.service.ts             # Profile-specific logic
        profile-summary.service.ts     # AI profile summary
    projects/
      services/
        repo-metadata.service.ts       # Repo list metadata
        repo-detail.service.ts         # Single repo detail
        repo-summary.service.ts        # AI repo summary
    ai/
      services/
        ai-assistant.service.ts        # Portfolio query
```

---

#### Step 2.3: Fix Endpoint Name Mismatches

**File**: `ui/src/app/features/projects/services/repo-metadata.service.ts`

**Before**:
```typescript
getRepoMetadata(username: string): Observable<any> {
  return this.http.get(`/candidate/${username}/repo`);  // WRONG
}
```

**After**:
```typescript
getReposMetadata(username: string, jobId?: string): Observable<RepoMetadata[]> {
  const params = jobId ? new HttpParams().set('job_id', jobId) : undefined;
  return this.apiClient.get<{repositories: RepoMetadata[]}>(
    `/candidate/${username}/repos`,
    params
  ).pipe(
    map(response => response.repositories)
  );
}
```

---

#### Step 2.4: Fix Response Shape Mismatches

**API returns** `cache_metadata` but **UI expects** `cacheStatus`.

**Option A**: Map in service layer (preferred for backward compat):

```typescript
// ui/src/app/features/profile/services/profile-summary.service.ts
getProfileSummary(username: string): Observable<ProfileSummary> {
  return this.apiClient.get<any>(`/candidate/${username}/summary`).pipe(
    map(response => ({
      ...response,
      cacheStatus: response.cache_metadata  // Map field name
    }))
  );
}
```

**Option B**: Update API to return `cache_status` (breaking change, coordinate with UI):

```python
# In api_gateway.py summary endpoints
return success_response({
    "summary": summary_text,
    "cache_status": cache_meta,  # Rename from cache_metadata
    "generated_at": timestamp
})
```

Choose Option A for quick fix, Option B for consistency.

---

#### Step 2.5: Implement Polling with Backoff

**File**: `ui/src/app/features/candidate/services/candidate-status.service.ts`

```typescript
import { Injectable, inject } from '@angular/core';
import { Observable, timer, of, EMPTY } from 'rxjs';
import { switchMap, retry, catchError, takeWhile, tap } from 'rxjs/operators';
import { ApiClientService } from '../../../core/services/api-client.service';

export interface CandidateStatus {
  job_id: string;
  overall_state: 'pending' | 'syncing' | 'synced' | 'cached' | 'failed';
  progress: {
    total_repos: number;
    synced: number;
    cached: number;
    failed: number;
  };
  repositories: Array<{
    repo_name: string;
    sync_state: string;
    cache_state: string;
  }>;
}

@Injectable({
  providedIn: 'root'
})
export class CandidateStatusService {
  private apiClient = inject(ApiClientService);
  
  /**
   * Poll candidate status with exponential backoff until completion.
   * Stops when overall_state is 'cached' or 'failed'.
   */
  pollStatus(
    username: string, 
    jobId?: string,
    maxAttempts: number = 40
  ): Observable<CandidateStatus> {
    let attempt = 0;
    
    return timer(0, 1000).pipe(  // Check every 1s
      switchMap(() => {
        attempt++;
        
        // Exponential backoff: 2s, 4s, 8s, max 15s
        const backoffDelay = Math.min(Math.pow(2, Math.floor(attempt / 3)) * 1000, 15000);
        
        if (attempt > 1) {
          return timer(backoffDelay).pipe(
            switchMap(() => this.getStatus(username, jobId))
          );
        }
        
        return this.getStatus(username, jobId);
      }),
      takeWhile((status, index) => {
        // Stop if complete or max attempts reached
        const isComplete = status.overall_state === 'cached' || 
                          status.overall_state === 'failed';
        return !isComplete && index < maxAttempts;
      }, true),  // Inclusive: emit final value
      catchError(error => {
        console.error('Status polling error:', error);
        return EMPTY;
      })
    );
  }
  
  private getStatus(username: string, jobId?: string): Observable<CandidateStatus> {
    const params = jobId ? new HttpParams().set('job_id', jobId) : undefined;
    return this.apiClient.get<CandidateStatus>(`/candidate/${username}/status`, params);
  }
}
```

**Usage in components**:
```typescript
// In profile.component.ts
this.statusService.pollStatus(this.username, this.jobId).subscribe({
  next: (status) => {
    this.jobStatus = status;
    if (status.overall_state === 'cached') {
      this.loadProfileData();  // Fetch final data
    }
  },
  error: (err) => console.error('Polling failed:', err)
});
```

---

### Phase 3: Testing & Validation

#### Step 3.1: API Unit Tests

**File**: `api/v0.3.0/tests/test_request_helpers.py`

```python
import pytest
from blueprints.request_helpers import (
    validate_username, 
    validate_repo_name,
    resolve_job
)

def test_validate_username_success():
    result = validate_username("octocat")
    assert result is None

def test_validate_username_empty():
    response = validate_username("")
    assert response.status_code == 400
    assert "required" in response.get_body().decode()

def test_resolve_job_with_explicit_id(mock_table_manager):
    job, err = resolve_job(mock_table_manager, "user1", job_id="job123")
    assert err is None
    assert job["job_id"] == "job123"

def test_resolve_job_latest(mock_table_manager):
    job, err = resolve_job(mock_table_manager, "user1", allow_latest=True)
    assert err is None
    assert job is not None
```

#### Step 3.2: API Integration Tests

**File**: `api/v0.3.0/tests/integration/test_status_endpoints.py`

```python
def test_get_candidate_status_comprehensive(api_client, sample_job):
    response = api_client.get(
        f"/candidate/{sample_job.username}/status",
        params={"job_id": sample_job.job_id}
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "overall_state" in data
    assert "progress" in data
    assert data["progress"]["total_repos"] > 0
    assert "repositories" in data
    
    # Verify no side effects (no new queue messages)
    queue_count_before = get_queue_message_count()
    api_client.get(f"/candidate/{sample_job.username}/status")
    queue_count_after = get_queue_message_count()
    assert queue_count_before == queue_count_after
```

#### Step 3.3: UI Service Tests

**File**: `ui/src/app/features/candidate/services/candidate-status.service.spec.ts`

```typescript
describe('CandidateStatusService', () => {
  it('should poll until status is cached', fakeAsync(() => {
    const mockResponses = [
      { overall_state: 'pending', progress: { synced: 0 } },
      { overall_state: 'syncing', progress: { synced: 5 } },
      { overall_state: 'cached', progress: { synced: 10, cached: 10 } }
    ];
    
    let emitCount = 0;
    service.pollStatus('octocat').subscribe(status => {
      expect(status).toEqual(mockResponses[emitCount]);
      emitCount++;
    });
    
    tick(10000);  // Advance time
    expect(emitCount).toBe(3);
  }));
});
```

---

## Success Criteria

### API Gateway
- [ ] No function has duplicated validation/session/job resolution logic
- [ ] `get_candidate_bundle()` makes O(1) queries (not O(N))
- [ ] Status endpoints are purely read-only (no enqueuing)
- [ ] Single `/status` endpoint returns both job + repo-level state
- [ ] ETag support added to stable metadata endpoints
- [ ] All helpers in `request_helpers.py` are tested

### UI Services
- [ ] All services use `ApiClientService` for HTTP calls
- [ ] Feature services live under `features/*/services/`
- [ ] Core/shared services live under `core/services/`
- [ ] No endpoint name mismatches (API implements what UI calls)
- [ ] Response field names match or are mapped in service layer
- [ ] Status polling uses exponential backoff (max 15s interval)
- [ ] ETag headers sent for cacheable requests

### Performance
- [ ] Profile view loads metadata in <500ms after sync (was >2s with N+1)
- [ ] Status polling reduced from 40 calls/2min to ~12 calls/2min
- [ ] 304 Not Modified responses for unchanged repo lists
- [ ] Zero redundant cache job enqueues during polling

### Code Quality
- [ ] No TODOs or code smells introduced
- [ ] All new functions have docstrings
- [ ] Test coverage >80% for new helper modules
- [ ] Linter passes (Pylance, ESLint)

---

## Migration Path

1. **Week 1**: Implement API helpers + N+1 fix (non-breaking)
2. **Week 2**: Add new `/status` endpoint; keep old endpoints (deprecated)
3. **Week 3**: Refactor UI services to use ApiClient + new endpoints
4. **Week 4**: Update polling logic with backoff + ETag support
5. **Week 5**: Remove deprecated status endpoints after UI migration confirmed

---

## Rollback Plan

- All changes are backward-compatible until Week 5
- If issues arise, UI can revert to old service implementations
- API keeps both old and new status endpoints until UI fully migrated
- N+1 fix is transparent (same response shape, just faster)

---

## Open Questions

1. Should we add WebSocket/SSE for status instead of polling? (Future consideration)
2. Should metadata responses include `Retry-After` header when state is not final?
3. Do we want batch status endpoint for multiple candidates? (Admin view use case)
4. Should we version the API (`/v1/candidate/...`) before making these changes?

---

**Last Updated**: 2026-02-13  
**Status**: Ready for Implementation  
**Estimated Effort**: 3-4 weeks (1 backend dev + 1 frontend dev)
