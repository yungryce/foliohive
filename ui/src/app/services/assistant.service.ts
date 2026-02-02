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

  askPortfolio(req: AIAssistantRequest): Observable<AIAssistantResponse> {
    const url = `${this.config.apiUrl}/ai`;
    return this.http.post<any>(url, req).pipe(
      map(res => {
        if (res?.status === 'success' && res?.data) return res.data as AIAssistantResponse;
        return res as AIAssistantResponse;
      }),
      catchError(err => {
        console.error('AI request failed:', err);
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
        console.error('Readme summary request failed:', err);
        return of({ username, repo, readme_summary_html: '' } as ReadmeSummaryResponse);
      })
    );
  }
}