import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Observable, catchError, finalize, map, of } from 'rxjs';
import DOMPurify from 'dompurify';
import { AIAssistantService, ReadmeSummaryResponse } from '../../services/assistant.service';
import { CandidateContextService } from '../../services/candidate-context.service';

/**
 * Aligned with backend schema from get_repo_files in api_gateway.py
 */
interface RepoDetailVM {
  name: string;
  description?: string;
  updatedAt?: string;
  languagesPct: { k: string; pct: number }[];
  htmlUrl?: string;
  stars: number;
  forks: number;
  topics: string[];
}

@Component({
  selector: 'app-project',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './project.component.html',
  styleUrls: ['./project.component.css']
})
export class ProjectComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private ai = inject(AIAssistantService);
  private sanitizer = inject(DomSanitizer);
  private candidateContext = inject(CandidateContextService);

  contentHtml: SafeHtml = '';
  summaryLoading = false;
  summaryError = '';

  username = '';
  repoName = '';
  repo$!: Observable<RepoDetailVM | null>;

  ngOnInit(): void {
    this.repoName = this.route.snapshot.paramMap.get('repo') || '';

    const active = this.candidateContext.activeCandidate;
    this.username = active?.username ?? '';

    if (!this.username || !this.repoName) {
      this.summaryError = 'Missing candidate or repository.';
      this.repo$ = of(this.toVM(null));
      return;
    }

    this.summaryLoading = true;
    
    // Use polling to wait for cache to be ready
    this.repo$ = this.ai.pollForReadme(this.username, this.repoName).pipe(
      map((res: ReadmeSummaryResponse) => {
        const summaryHtml = res?.readme_summary_html || '';
        if (summaryHtml) {
          const cleanHtml = DOMPurify.sanitize(summaryHtml, { USE_PROFILES: { html: true } }) as string;
          this.contentHtml = this.sanitizer.bypassSecurityTrustHtml(cleanHtml);
        } else {
          this.contentHtml = this.sanitizer.bypassSecurityTrustHtml('<p>No README summary available.</p>');
        }

        return this.toVM(res?.repo_entry ?? null);
      }),
      catchError((err) => {
        // Extract meaningful error message from HttpErrorResponse or generic error
        const errorMsg = err?.message || err?.error?.message || 'Failed to load README summary.';
        this.summaryError = errorMsg;
        this.contentHtml = this.sanitizer.bypassSecurityTrustHtml('<p>README summary unavailable.</p>');
        return of(this.toVM(null));
      }),
      finalize(() => {
        this.summaryLoading = false;
      })
    );
  }

  /**
   * Transform backend bundle entry to detail view model.
   * Backend structure (from _repo_row_to_bundle_entry):
   * {
   *   name: string,
   *   languages: {lang: bytes},
   *   urls: {github, homepage},
   *   stats: {stars, forks},
   *   timestamps: {pushed_at, updated_at, created_at},
   *   metadata: {description, fingerprint, topics, ...}
   * }
   */
  private toVM(r: any | null): RepoDetailVM | null {
    if (!r?.name) {
      return {
        name: this.repoName,
        description: 'Repository details',
        languagesPct: [],
        updatedAt: undefined,
        htmlUrl: undefined,
        stars: 0,
        forks: 0,
        topics: [],
      };
    }

    const langs = r?.languages ?? {};
    const total = Object.values(langs).reduce((a: number, b: any) => a + Number(b), 0) || 1;
    const languagesPct = Object.entries(langs)
      .map(([k, v]) => ({ k, pct: Math.round((Number(v) / total) * 100) }))
      .sort((a, b) => b.pct - a.pct);

    return {
      name: r.name,
      description: r?.metadata?.description ?? 'No description',
      languagesPct,
      updatedAt: r?.timestamps?.updated_at ?? r?.timestamps?.pushed_at,
      htmlUrl: r?.urls?.github,
      stars: r?.stats?.stars ?? 0,
      forks: r?.stats?.forks ?? 0,
      topics: Array.isArray(r?.metadata?.topics) ? r.metadata.topics : [],
    };
  }

}
