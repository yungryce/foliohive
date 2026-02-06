import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';
import { ConfigService } from './config.service';

export interface AIAssistantRequest {
  query: string;
  username?: string;
  instance_id?: string;
  status_query_url?: string;
}

export interface AIAssistantResponse {
  response: string; // Markdown text
  repositories_used: { name: string; relevance_score: number }[];
  total_repositories: number;
  query: string;
}

export interface CacheStatusResponse {
  status: 'cached' | 'processing' | 'pending' | 'failed' | 'not_found';
  message: string;
  job_id?: string;
  cache_message_id?: string;
  error?: string;
}

export interface ReadmeSummaryResponse {
  username: string;
  repo: string;
  job_id?: string;
  repo_entry?: any;
  readme_summary_html?: string;
}

@Injectable({ providedIn: 'root' })
export class AIAssistantService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);

  /** Trigger backend orchestration to (re)build bundles for a user. */
  startBuild(username: string, force = true): Observable<any> {
    const url = `${this.config.apiUrl}/bundles/${encodeURIComponent(username)}/refresh`;
    return this.http.post(url, { force_refresh: force });
  }

  /** Check cache status for a repository */
  getCacheStatus(username: string, repo: string): Observable<CacheStatusResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/${encodeURIComponent(repo)}/cache-status`;
    return this.http.get<any>(url).pipe(
      map(res => {
        if (res?.status === 'success' && res?.data) return res.data as CacheStatusResponse;
        return res as CacheStatusResponse;
      }),
      catchError(err => {
        return of({
          status: 'not_found',
          message: 'Failed to check cache status'
        } as CacheStatusResponse);
      })
    );
  }

  /** Poll for readme summary with automatic retry until cached */
  pollForReadme(
    username: string,
    repo: string,
    maxAttempts: number = 30,
    intervalMs: number = 2000
  ): Observable<ReadmeSummaryResponse> {
    let attempts = 0;

    return new Observable(observer => {
      const checkAndFetch = () => {
        attempts++;

        // First check cache status
        this.getCacheStatus(username, repo).subscribe({
          next: (status) => {
            
            if (status.status === 'cached') {
              // Cache is ready - fetch the readme
              this.getReadmeSummary(username, repo).subscribe({
                next: (summary) => {
                  observer.next(summary);
                  observer.complete();
                },
                error: (err) => {
                  // Fetch failed even though cache showed ready - this is a fatal error
                  observer.error(new Error(`Failed to fetch README: ${err.statusText || err.message}`));
                }
              });
            } else if (status.status === 'processing' || status.status === 'pending') {
              // Still processing - poll again if we haven't exceeded max attempts
              if (attempts < maxAttempts) {
                setTimeout(checkAndFetch, intervalMs);
              } else {
                observer.error(new Error(`Cache still not ready after ${maxAttempts} attempts`));
              }
            } else if (status.status === 'not_found') {
              // No job found - trigger refresh first
              observer.error(new Error('No job found. Please trigger a refresh first.'));
            } else if (status.status === 'failed') {
              // Failed - but backend re-enqueues, so poll again
              if (attempts < maxAttempts) {
                setTimeout(checkAndFetch, intervalMs);
              } else {
                observer.error(new Error(`Cache job failed: ${status.error || 'unknown error'}`));
              }
            }
          },
          error: (err) => {
            observer.error(err);
          }
        });
      };

      // Start polling
      checkAndFetch();
    });
  }

  askPortfolio(req: AIAssistantRequest): Observable<AIAssistantResponse> {
    const url = `${this.config.apiUrl}/ai`;
    return this.http.post<any>(url, req).pipe(
      map(res => {
        if (res?.status === 'success' && res?.data) return res.data as AIAssistantResponse;
        return res as AIAssistantResponse;
      }),
      catchError(err => {
        if (err.status === 404 && req.username) {
          // On not found, kick off a build with force_refresh
          this.startBuild(req.username, true).subscribe();
        }
        return of({
          response: 'AI service failed or is unavailable. Please try again.',
          repositories_used: [],
          total_repositories: 0,
          query: req.query
        } as AIAssistantResponse);
      })
    );
  }

  getReadmeSummary(username: string, repo: string): Observable<ReadmeSummaryResponse> {
    const url = `${this.config.apiUrl}/candidate/${encodeURIComponent(username)}/${encodeURIComponent(repo)}/readme-summary`;
    return this.http.get<any>(url).pipe(
      map(res => {
        if (res?.status === 'success' && res?.data) return res.data as ReadmeSummaryResponse;
        return res as ReadmeSummaryResponse;
      }),
      catchError(err => {
        // Re-throw the error instead of silently returning empty response
        // This allows polling to detect and handle failures appropriately
        throw err;
      })
    );
  }
}