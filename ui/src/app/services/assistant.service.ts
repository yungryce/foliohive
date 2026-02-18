import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, map, throwError } from 'rxjs';
import { ConfigService } from './config.service';

export interface AIAssistantRequest {
  query: string;
  username: string;
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

  /**
   * Query candidate portfolio with AI.
   * Throws error for caller to handle (e.g., polling logic).
   */
  askPortfolio(req: AIAssistantRequest): Observable<AIAssistantResponse> {
    const url = `${this.config.apiUrl}/ai`;
    return this.http.post<any>(url, req).pipe(
      map(res => {
        if (res?.status === 'success' && res?.data) return res.data as AIAssistantResponse;
        return res as AIAssistantResponse;
      }),
      catchError(err => {
        // Re-throw error for caller to handle (e.g., polling logic)
        return throwError(() => err);
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
}