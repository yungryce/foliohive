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
    pending: string[];
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

export interface SingleRepoBundleResponse {
  username: string;
  repo: string;
  fingerprint?: string;
  primary_readme?: string;
  readme_files?: { [path: string]: string };
  config_files?: { [filename: string]: string };
  data?: any;
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
   * POST /candidate/{username}/refresh
   */
  startBuild(username: string, force = true): Observable<RefreshResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/refresh`;
    return this.http.post<any>(url, { force_refresh: force }).pipe(
      map(res => {
        if (res?.status === 'success' && res?.data) return res.data as RefreshResponse;
        return res as RefreshResponse;
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
        if (res?.status === 'success' && res?.data) return res.data as JobStatusResponse;
        return res as JobStatusResponse;
      }),
      catchError(err => {
        return of(null as any);
      })
    );
  }

  checkBundle(username: string): Observable<boolean> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}`;
    return this.http.get<any>(url).pipe(
      map(res => {
        // Backend wraps response: {status: "success", ok: true, data: {...}, meta: {...}}
        const payload = res?.status === 'success' && res?.data ? res.data : res;
        return payload && Array.isArray(payload.data) && payload.data.length > 0;
      }),
      catchError(() => of(false))
    );
  }

  /**
   * Retrieve candidate portfolio metadata.
   * GET /candidate/{username}?job_id=...
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
