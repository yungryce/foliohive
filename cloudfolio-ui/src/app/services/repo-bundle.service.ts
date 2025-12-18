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
      catchError(err => {
        console.error('getJobStatus error:', err);
        return of(null);
      })
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
      catchError(err => {
        console.error('getUserBundle error:', err);
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
      catchError(err => {
        console.error('getUserSingleRepoBundle error:', err);
        return of({ username, repo, data: null } as SingleRepoBundleResponse);
      })
    );
  }
}
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { map, catchError } from 'rxjs/operators';
import { ConfigService } from './config.service';
import { CacheService } from './cache.service';

export interface RepoBundleResponse {
  username: string;
  fingerprint?: string;
  last_modified?: string;
  size_bytes?: number;
  data: { has_documentation: boolean }[];
}

export interface SingleRepoBundleResponse {
  username: string;
  repo: string;
  fingerprint?: string;
  last_modified?: string;
  size_bytes?: number;
  data: any; // single repository bundle
}

@Injectable({ providedIn: 'root' })
export class RepoBundleService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  private cache = inject(CacheService);

  /** Trigger backend orchestration to (re)build bundles for a user. */
  startBuild(username: string, force = true): Observable<any> {
    const url = `${this.config.apiUrl}/orchestrator_start`;
    return this.http.post(url, { username, force_refresh: force });
  }

  /**
   * GET /bundles/{username}
   * Unwraps { status, data } shapes and guarantees data is an array.
   */
  getUserBundle(username: string, useCache = true): Observable<RepoBundleResponse> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}`;
    const cacheKey = `bundle-${username}`;

    if (useCache) {
      const cached = this.cache.get<RepoBundleResponse>(cacheKey);
      if (cached) {
        console.log('Using cached data for:', cacheKey, cached);
        return of(cached);
      }
    }

    return this.http.get<any>(url).pipe(
      map((res: any) => {
        let payload: RepoBundleResponse | null = null;
        if (res?.status === 'success' && res?.data) {
          payload = res.data as RepoBundleResponse;
        } else if (res?.username && Array.isArray(res?.data)) {
          payload = res as RepoBundleResponse;
        } else if (Array.isArray(res)) {
          payload = { username, data: res };
        } else if (Array.isArray(res?.data)) {
          payload = { username, data: res.data };
        } else {
          payload = { username, data: [] };
        }
        if (!Array.isArray(payload.data)) payload.data = [];
        this.cache.set(cacheKey, payload, 1000 * 60 * 10);
        return payload;
      }),
      catchError(err => {
        console.error('getUserBundle error:', err);
        if (err.status === 404) {
          // On not found, kick off a build with force_refresh
          this.startBuild(username, true).subscribe();
        }
        return of({ username, data: [] } as RepoBundleResponse);
      })
    );
  }

  /**
   * GET /bundles/{username}/{repo}
   * Unwraps { status, data } shapes and returns normalized payload.
   */
  getUserSingleRepoBundle(username: string, repo: string, useCache = true): Observable<SingleRepoBundleResponse> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}/${encodeURIComponent(repo)}`;
    const cacheKey = `repo-bundle-${username}-${repo}`;

    if (useCache) {
      const cached = this.cache.get<SingleRepoBundleResponse>(cacheKey);
      if (cached) return of(cached);
    }

    return this.http.get<any>(url).pipe(
      map((res: any) => {
        let payload: SingleRepoBundleResponse;

        if (res?.status === 'success' && res?.data) {
          payload = res.data as SingleRepoBundleResponse;
        } else if (res?.username && res?.repo && 'data' in res) {
          payload = res as SingleRepoBundleResponse;
        } else {
          payload = { username, repo, data: res?.data ?? null };
        }

        this.cache.set(cacheKey, payload, 1000 * 60 * 10);
        return payload;
      }),
      catchError(err => {
        console.error('getUserSingleRepoBundle error:', err);
        return of({ username, repo, data: null } as SingleRepoBundleResponse);
      })
    );
  }

  /**
   * Alias for convenience.
   */
  getUserSingleRepo(username: string, repo: string, useCache = true): Observable<SingleRepoBundleResponse> {
    return this.getUserSingleRepoBundle(username, repo, useCache);
  }
}
