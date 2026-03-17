import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, of, throwError } from 'rxjs';
import { map, catchError } from 'rxjs/operators';
import { ConfigService } from './config.service';
import { CacheService } from './cache.service';

export interface RepoBundleResponse {
  username: string;
  job_id?: string;
  fingerprint?: string;
  last_modified?: string;
  status?: string;  // "queued" | "syncing" | "metadata_ready" | "completed" | "failed"
  data: any[];
  repos?: any[];  // Alias for data (backend includes both)
}

export interface RefreshResponse {
  status: string;  // "processing" | "fresh"
  job_id?: string;
  repos_queued?: number;
  repos_count?: number;  // Only present when status is "fresh"
  status_url?: string;
}

export interface JobStatusResponse {
  job_id: string;
  username: string;
  status: string;  // "queued" | "syncing" | "metadata_ready" | "caching_started" | "completed" | "failed"
  metadata_ready: boolean;  // True when metadata available (metadata_ready or later)
  summary_ready: boolean;   // True when summaries generated (completed)
  progress?: {
    total: number;
    completed: number;      // summary_ready + failed (terminal states)
    percentage: number;
    pending: number;        // Waiting to process
    synced: number;         // Metadata synced, summary pending
    summary_ready: number;  // Micro-summary generated
    failed: number;         // Terminal failure state
  };
  created_at?: string;
  repo_details?: {
    pending: string[];
    synced: string[];
    summary_ready: string[];
    failed: string[];
  };
}

export interface SessionCandidate {
  username: string;
  latest_job_id?: string;
  last_viewed_at?: string;
  query_count?: number;
}

export interface SingleRepoBundleResponse {
  username: string;
  repo: string;
  job_id?: string;
  last_modified?: string;
  status?: string;
  fingerprint?: string;
  repo_entry?: any;
  primary_readme?: string;
  readme_files?: { [path: string]: string };
  config_files?: { [filename: string]: string };
  data?: any;
}

export interface ReadmeSummaryResponse {
  username: string;
  repo: string;
  job_id?: string;
  repo_entry?: any;
  readme_summary_markdown?: string;
  cache_metadata?: any;
}

@Injectable({
  providedIn: 'root'
})
export class RepoBundleService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  private cache = inject(CacheService);

  /**
   * Trigger backend orchestration to (re)build bundles for a user.
   * Returns job_id for status polling.
   * POST /candidate/{username}/refresh
   * 
   * @param username - GitHub username
   * @param force - Force refresh even if recent data exists
   * @returns Observable<string> - Job ID for polling
   * @throws Error if no job_id returned or request fails
   */
  startBuild(username: string, force = true): Observable<string> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/refresh`;
    return this.http.post<any>(url, { force_refresh: force }).pipe(
      map(res => {
        const data = res?.status === 'success' && res?.data ? res.data : res;
        const jobId = data?.job_id;
        
        if (!jobId) {
          throw new Error('No job_id returned from refresh endpoint');
        }
        
        return jobId as string;
      }),
      catchError(err => {
        const errorMsg = err?.error?.message || err?.message || 'Failed to start build';
        return throwError(() => new Error(errorMsg));
      })
    );
  }

  /**
   * Get job status for a user and job ID.
   * GET /candidate/{username}/status?job_id=...
   */
  getJobStatus(username: string, jobId: string): Observable<JobStatusResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/status`;
    const params = new HttpParams().set('job_id', jobId);
    return this.http.get<any>(url, { params }).pipe(
      map(res => {
        const payload = (res?.status === 'success' && res?.data) ? res.data : res;
        return payload as JobStatusResponse;
      }),
      catchError(err => {
        return of(null as any);
      })
    );
  }


  /**
   * Retrieve candidate portfolio metadata.
   * GET /candidate/{username}?job_id=...
   */
  getCandidateMetadata(username: string, jobId?: string, useCache = true): Observable<RepoBundleResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}`;
    const cacheKey = `bundle-${username}-${jobId || 'latest'}`;

    if (useCache) {
      const cached = this.cache.get<RepoBundleResponse>(cacheKey);
      if (cached) return of(cached);
    }

    let params = new HttpParams();
    if (jobId) params = params.set('job_id', jobId);

    return this.http.get<any>(url, { params }).pipe(
      map(res => {
        let payload: RepoBundleResponse;
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        if (res?.status === 'success' && res?.data) payload = res.data as RepoBundleResponse;
        else payload = (res as RepoBundleResponse) ?? ({ username, data: [] } as RepoBundleResponse);
        if (!Array.isArray(payload.data)) payload.data = [];
        this.cache.set(cacheKey, payload, 1000 * 60 * 5); // 5 min cache
        return payload;
      }),
      catchError(err => {
        return of({ username, data: [] } as RepoBundleResponse);
      })
    );
  }

  /**
   * Retrieve metadata for a single repository.
   * GET /candidate/{username}/{repo}/metadata
   */
  getCandidateRepoMetadata(username: string, repo: string, useCache = true): Observable<SingleRepoBundleResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/${encodeURIComponent(repo)}/metadata`;
    const cacheKey = `bundle-repo-${username}-${repo}`;

    if (useCache) {
      const cached = this.cache.get<SingleRepoBundleResponse>(cacheKey);
      if (cached) return of(cached);
    }

    return this.http.get<any>(url).pipe(
      map(res => {
        const payload = (res?.status === 'success' && res?.data ? res.data : res) as SingleRepoBundleResponse;
        const normalized = payload ?? ({ username, repo, data: null } as SingleRepoBundleResponse);
        this.cache.set(cacheKey, normalized, 1000 * 60 * 5); // 5 min cache
        return normalized;
      }),
      catchError(err => {
        return of({ username, repo, data: null } as SingleRepoBundleResponse);
      })
    );
  }

  /**
   * Gets summary query for a candidate.
   * Throws error for caller to handle (e.g., polling logic).
   */
  getReadmeSummary(username: string, repo: string): Observable<ReadmeSummaryResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/${encodeURIComponent(repo)}/readme-summary`;
    return this.http.get<any>(url).pipe(
      map(res => {
        if (res?.status === 'success' && res?.data) return res.data as ReadmeSummaryResponse;
        return res as ReadmeSummaryResponse;
      }),
      catchError(err => {
        // Re-throw error for caller to handle (e.g., polling logic)
        return throwError(() => err);
      })
    );
  }

  /**
   * List usernames recently viewed in this session.
   * GET /session/candidates
   */
  getSessionCandidates(limit = 10): Observable<SessionCandidate[]> {
    const url = `${this.config.apiUrl}/session/candidates`;
    const params = new HttpParams().set('limit', String(limit));
    return this.http.get<any>(url, { params }).pipe(
      map(res => {
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        const payload = res?.status === 'success' && res?.data ? res.data : res;
        const candidates = payload?.candidates ?? [];
        return Array.isArray(candidates) ? (candidates as SessionCandidate[]) : [];
      }),
      catchError(err => {
        return of([] as SessionCandidate[]);
      })
    );
  }
}
