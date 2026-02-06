import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';
import { ConfigService } from './config.service';
import { CacheService } from './cache.service';

export interface CandidateProfileResponse {
  username: string;
  github_profile?: any;
  job_metadata?: any;
  statistics?: {
    repo_count?: number;
    stars_total?: number;
    forks_total?: number;
    top_languages?: { language: string; bytes: number }[];
    topics?: string[];
  };
}

export interface CandidateSummaryResponse {
  username: string;
  job_id?: string;
  summary_html?: string;
  based_on?: {
    profile_fingerprint?: string;
    job_id?: string;
  };
}

@Injectable({ providedIn: 'root' })
export class ProfileService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  private cache = inject(CacheService);

  getCandidateProfile(username: string, jobId?: string, useCache = true): Observable<CandidateProfileResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/profile`;
    const cacheKey = `profile-${username}-${jobId || 'latest'}`;

    if (useCache) {
      const cached = this.cache.get<CandidateProfileResponse>(cacheKey);
      if (cached) return of(cached);
    }

    let params = new HttpParams();
    if (jobId) params = params.set('job_id', jobId);

    return this.http.get<any>(url, { params }).pipe(
      map(res => {
        const payload = res?.status === 'success' && res?.data ? res.data : res;
        const profile = (payload ?? { username }) as CandidateProfileResponse;
        this.cache.set(cacheKey, profile, 1000 * 60 * 5);
        return profile;
      }),
      catchError(err => {
        return of({ username } as CandidateProfileResponse);
      })
    );
  }

  getCandidateSummary(username: string, jobId?: string): Observable<CandidateSummaryResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/summary`;
    let params = new HttpParams();
    if (jobId) params = params.set('job_id', jobId);

    return this.http.get<any>(url, { params }).pipe(
      map(res => {
        const payload = res?.status === 'success' && res?.data ? res.data : res;
        return (payload ?? { username }) as CandidateSummaryResponse;
      }),
      catchError(err => {
        return of({ username, summary_html: '' } as CandidateSummaryResponse);
      })
    );
  }
}
