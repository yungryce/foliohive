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
  success?: boolean;
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
        const data = res?.status === 'success' && res?.data ? res.data : res;
        const metadata = data?.metadata || {};
        const repositoriesUsed = Array.isArray(metadata?.repositories_used)
          ? metadata.repositories_used
          : (Array.isArray(metadata?.selected_repositories) ? metadata.selected_repositories : []);

        return {
          response: data?.response || data?.query_summary || '',
          repositories_used: repositoriesUsed,
          total_repositories: Number(data?.total_repositories || metadata?.total_repositories || repositoriesUsed.length || 0),
          query: data?.query || req.query,
          success: true,
        } as AIAssistantResponse;
      }),
      catchError(err => {
        // Re-throw error for caller to handle (e.g., polling logic)
        return throwError(() => err);
      })
    );
  }
}
