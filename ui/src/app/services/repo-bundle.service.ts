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
  size_bytes?: number;
  status?: string;
  expected_repos?: string[];
  queued_repos?: string[];
  synced_repos?: string[];
  data: any[];
}

export interface SingleRepoBundleResponse {
  username: string;
  repo: string;
  fingerprint?: string;
  last_modified?: string;
  size_bytes?: number;
  data: any;
}

export interface RefreshResponse {
  status: string;
  job_id?: string;
  repos_queued?: number;
  status_url?: string;
}

export interface JobStatusResponse {
  job_id: string;
  username: string;
  status: string;
  progress: { total: number; completed: number; percentage: number };
  expected_repos?: string[];
  queued_repos?: string[];
  synced_repos?: string[];
  created_at?: string;
}

@Injectable({ providedIn: 'root' })
export class RepoBundleService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  private cache = inject(CacheService);

  /** POST /bundles/{username}/refresh */
  startBuild(username: string, force = true): Observable<RefreshResponse> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}/refresh`;
    return this.http.post<any>(url, { force_refresh: force }).pipe(
      map((res: any) => {
        if (res?.status === 'success' && res?.data) return res.data as RefreshResponse;
        return res as RefreshResponse;
      })
    );
  }

  /** GET /bundles/{username}/status?job_id=... */
  getJobStatus(username: string, jobId: string): Observable<JobStatusResponse | null> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}/status`;
    const params = new HttpParams().set('job_id', jobId);
    return this.http.get<any>(url, { params }).pipe(
      map((res: any) => {
        if (res?.status === 'success' && res?.data) return res.data as JobStatusResponse;
        return res as JobStatusResponse;
      }),
      catchError((err: any) => {
        if (err?.status !== 404) console.error('getJobStatus error:', err);
        return of(null);
      })
    );
  }

  /** GET /bundles/{username} - check if bundle exists (no cache) */
  checkBundle(username: string): Observable<boolean> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}`;
    return this.http.get<any>(url).pipe(
      map((res: any) => {
        const payload = res?.status === 'success' && res?.data ? res.data : res;
        return !!payload && Array.isArray(payload.data) && payload.data.length > 0;
      }),
      catchError((err: any) => of(false))
    );
  }

  /** GET /bundles/{username}?job_id=... */
  getUserBundle(username: string, jobId?: string, useCache = true): Observable<RepoBundleResponse> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}`;
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

  /** GET /bundles/{username}/{repo}?job_id=... */
  getUserSingleRepoBundle(username: string, repo: string, jobId?: string, useCache = true): Observable<SingleRepoBundleResponse> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}/${encodeURIComponent(repo)}`;
    const cacheKey = `repo-bundle-${username}-${repo}-${jobId || 'latest'}`;

    if (useCache) {
      const cached = this.cache.get<SingleRepoBundleResponse>(cacheKey);
      if (cached) return of(cached);
    }

    let params = new HttpParams();
    if (jobId) params = params.set('job_id', jobId);

    return this.http.get<any>(url, { params }).pipe(
      map((res: any) => {
        let payload: SingleRepoBundleResponse;
        if (res?.status === 'success' && res?.data) payload = res.data as SingleRepoBundleResponse;
        else if (res?.username && res?.repo && 'data' in res) payload = res as SingleRepoBundleResponse;
        else payload = { username, repo, data: res?.data ?? null } as SingleRepoBundleResponse;
        this.cache.set(cacheKey, payload, 1000 * 60 * 5);
        return payload;
      }),
      catchError((err: any) => {
        if (err?.status !== 404) console.error('getUserSingleRepoBundle error:', err);
        return of({ username, repo, data: null } as SingleRepoBundleResponse);
      })
    );
  }
}
