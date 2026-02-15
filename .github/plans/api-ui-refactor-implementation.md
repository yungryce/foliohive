UI Refactor Implementation Guide

**Goal**: Eliminate repeated operations across functions, optimize data retrieval, separate concerns clearly, and align UI services with API responses.
- Each UI service serves its feature; shared infra lives in core/shared

---


### UI Issues

1. **Endpoint Name Mismatch**: UI calls `getRepoMetadata()` but API implements `get_candidate_repos_metadata()`
   - Location: [ui/src/app/services/repo-bundle.service.ts](ui/src/app/services/repo-bundle.service.ts)
   - Impact: 404 errors or incorrect fallback behavior

2. **Response Shape Mismatch**: API returns `cache_metadata` but UI expects `cacheStatus`
   - Impact: Data not accessible to components

3. **Service Organization**: Cross-cutting concerns mixed with feature logic
   - ConfigService, SessionIdService, CacheService used everywhere
   - No clear "core API client" pattern


---

## Implementation Plan

### Phase 1: UI Service Reorganization

#### Step 1.1: Create Core API Client

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

#### Step 1.2: Reorganize Services by Feature

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

#### Step 1.3: Fix Endpoint Name Mismatches

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

#### Step 1.4: Fix Response Shape Mismatches

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

#### Step 1.5: Implement Polling with Backoff

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
