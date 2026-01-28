import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, of } from 'rxjs';
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

export interface SingleRepoBundleResponse {
  username: string;
  repo: string;
  fingerprint?: string;
  primary_readme?: string;      // When type=readme or all
  readme_files?: Record<string, string>;  // When type=readme or all
  config_files?: Record<string, string>;  // When type=config or all
  data?: any;  // Fallback for backward compatibility
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
  status: string;  // "queued" | "syncing" | "metadata_ready" | "completed" | "failed"
  metadata_ready: boolean;  // True when first repo cached (can display metadata)
  files_ready: boolean;     // True when all files cached (can display README)
  progress: {
    total: number;
    completed: number;      // cached + failed (terminal states)
    percentage: number;
    pending: number;        // Waiting to sync
    synced: number;         // Metadata synced, files pending
    cached: number;         // Files cached (ready)
    failed: number;         // Terminal failure state
  };
  created_at?: string;
  repo_details?: {
    pending: string[];      // First 10 repos
    synced: string[];
    cached: string[];
    failed: string[];
  };
}

export interface SessionCandidate {
  username: string;
  latest_job_id?: string;
  last_viewed_at?: string;
  query_count?: number;
}

@Injectable({ providedIn: 'root' })
export class RepoBundleService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  private cache = inject(CacheService);

  /**
   * Trigger portfolio refresh for a user.
   * POST /candidate/{username}/refresh
   * 
   * Backend response structure:
   * - 202 Accepted: {status: "processing", job_id, repos_queued, status_url}
   * - 200 OK: {status: "fresh", repos_count} (when no refresh needed)
   * 
   * @param username GitHub username
   * @param force Whether to force refresh all repos (default: true)
   * @returns Observable<RefreshResponse>
   */
  startBuild(username: string, force = true): Observable<RefreshResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/refresh`;
    console.debug('[startBuild] request', { username, force, url });
    return this.http.post<any>(url, { force_refresh: force }).pipe(
      map((res: any) => {
        console.debug('[startBuild] response', { username, force, res });
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        if (res?.status === 'success' && res?.data) return res.data as RefreshResponse;
        return res as RefreshResponse;
      })
    );
  }

  /**
   * Poll job progress and status.
   * GET /candidate/{username}/status?job_id=...
   * 
   * Backend response structure:
   * {
   *   job_id: string,
   *   username: string,
   *   status: "queued" | "syncing" | "metadata_ready" | "completed" | "failed",
   *   metadata_ready: boolean,
   *   files_ready: boolean,
   *   progress: {total, completed, percentage, pending, synced, cached, failed},
   *   created_at?: string,
   *   repo_details?: {pending, synced, cached, failed}
   * }
   * 
   * Status progression: queued → syncing → metadata_ready → completed (or failed)
   * 
   * @param username GitHub username
   * @param jobId Job ID to check status for
   * @returns Observable<JobStatusResponse | null> (null on 404)
   */
  getJobStatus(username: string, jobId: string): Observable<JobStatusResponse | null> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/status`;
    const params = new HttpParams().set('job_id', jobId);
    return this.http.get<any>(url, { params }).pipe(
      map((res: any) => {
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        if (res?.status === 'success' && res?.data) return res.data as JobStatusResponse;
        return res as JobStatusResponse;
      }),
      catchError((err: any) => {
        if (err?.status !== 404) console.error('getJobStatus error:', err);
        return of(null);
      })
    );
  }

  /**
   * Check if bundle exists for a user (lightweight check, no cache).
   * GET /candidate/{username}
   * 
   * @param username GitHub username
   * @returns Observable<boolean> (true if bundle exists with data)
   */
  checkBundle(username: string): Observable<boolean> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}`;
    console.debug('[checkBundle] request', { username, url });
    return this.http.get<any>(url).pipe(
      map((res: any) => {
        console.debug('[checkBundle] response', { username, res });
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        const payload = res?.status === 'success' && res?.data ? res.data : res;
        return !!payload && Array.isArray(payload.data) && payload.data.length > 0;
      }),
      catchError((err: any) => of(false))
    );
  }

  /**
   * Retrieve candidate portfolio metadata.
   * GET /candidate/{username}?job_id=...
   * 
   * Backend response structure:
   * {
   *   username: string,
   *   job_id: string,
   *   fingerprint: string,
   *   status: "queued" | "syncing" | "metadata_ready" | "completed" | "failed",
   *   data: [repo_metadata...],
   *   repos: [repo_metadata...]  // Alias for data
   * }
   * 
   * Returns 404 while job is building or when no job exists.
   * 
   * @param username GitHub username
   * @param jobId Optional job ID (uses latest if not provided)
   * @param useCache Whether to use client-side cache (default: true)
   * @returns Observable<RepoBundleResponse>
   */
  getUserBundle(username: string, jobId?: string, useCache = true): Observable<RepoBundleResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}`;
    const cacheKey = `bundle-${username}-${jobId || 'latest'}`;

    if (useCache) {
      const cached = this.cache.get<RepoBundleResponse>(cacheKey);
      if (cached) return of(cached);
    }

    let params = new HttpParams();
    if (jobId) params = params.set('job_id', jobId);

    return this.http.get<any>(url, { params }).pipe(
      map((res: any) => {
        let payload: RepoBundleResponse;
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        if (res?.status === 'success' && res?.data) payload = res.data as RepoBundleResponse;
        else payload = (res as RepoBundleResponse) ?? ({ username, data: [] } as RepoBundleResponse);
        if (!Array.isArray(payload.data)) payload.data = [];
        this.cache.set(cacheKey, payload, 1000 * 60 * 5);
        return payload;
      }),
      catchError((err: any) => {
        // v0.3.0 returns 404 while a job is still building or when no bundle exists yet.
        // Treat this as a normal empty state (avoid noisy console errors in the UI).
        if (err?.status !== 404) console.error('getUserBundle error:', err);
        return of({ username, data: [] } as RepoBundleResponse);
      })
    );
  }

  /**
   * Retrieve file contents for a specific repository.
   * GET /candidate/{username}/{repo}/files?type=...
   * 
   * Backend response structure:
   * {
   *   username: string,
   *   repo: string,
   *   fingerprint: string,
   *   primary_readme?: string,              // When type=readme or all
   *   readme_files?: {[path: string]: string},   // When type=readme or all
   *   config_files?: {[filename: string]: string}  // When type=config or all
   * }
   * 
   * Returns 404 if repository not cached (trigger refresh first).
   * Files are immutable per fingerprint (long cache lifetime).
   * 
   * @param username GitHub username
   * @param repo Repository name
   * @param jobId Optional job ID
   * @param fileType File type: 'readme' | 'config' | 'all' (default: 'all')
   * @param useCache Whether to use client-side cache (default: true)
   * @returns Observable<SingleRepoBundleResponse>
   */
  getUserSingleRepoBundle(username: string, repo: string, jobId?: string, fileType: 'readme' | 'config' | 'all' = 'all', useCache = true): Observable<SingleRepoBundleResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/${encodeURIComponent(repo)}/files`;
    const cacheKey = `repo-bundle-${username}-${repo}-${fileType}-${jobId || 'latest'}`;

    if (useCache) {
      const cached = this.cache.get<SingleRepoBundleResponse>(cacheKey);
      if (cached) return of(cached);
    }

    let params = new HttpParams();
    if (jobId) params = params.set('job_id', jobId);
    if (fileType !== 'all') params = params.set('type', fileType);

    return this.http.get<any>(url, { params }).pipe(
      map((res: any) => {
        let payload: SingleRepoBundleResponse;
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        if (res?.status === 'success' && res?.data) {
          payload = res.data as SingleRepoBundleResponse;
        } else if (res?.username && res?.repo) {
          payload = res as SingleRepoBundleResponse;
        } else {
          payload = { 
            username, 
            repo, 
            data: res?.data ?? null 
          } as SingleRepoBundleResponse;
        }
        this.cache.set(cacheKey, payload, 1000 * 60 * 5);
        return payload;
      }),
      catchError((err: any) => {
        if (err?.status !== 404) console.error('getUserSingleRepoBundle error:', err);
        return of({ username, repo, data: null } as SingleRepoBundleResponse);
      })
    );
  }

  /**
   * List usernames recently viewed in this session.
   * GET /session/candidates?limit=...
   * 
   * Backend response structure:
   * {
   *   candidates: [{
   *     username: string,
   *     latest_job_id?: string,
   *     last_viewed_at?: string,
   *     query_count?: number
   *   }]
   * }
   * 
   * Requires X-Session-Id header (set by HTTP interceptor).
   * 
   * @param limit Maximum number of candidates to return (1-50, default: 10)
   * @returns Observable<SessionCandidate[]>
   */
  getSessionCandidates(limit = 10): Observable<SessionCandidate[]> {
    const url = `${this.config.apiUrl}/session/candidates`;
    const params = new HttpParams().set('limit', String(limit));
    return this.http.get<any>(url, { params }).pipe(
      map((res: any) => {
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        const payload = res?.status === 'success' && res?.data ? res.data : res;
        const candidates = payload?.candidates ?? [];
        return Array.isArray(candidates) ? (candidates as SessionCandidate[]) : [];
      }),
      catchError((err: any) => {
        console.warn('getSessionCandidates error:', err);
        return of([] as SessionCandidate[]);
      })
    );
  }
}
