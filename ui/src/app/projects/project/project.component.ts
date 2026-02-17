import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Observable, catchError, finalize, map, of } from 'rxjs';
import DOMPurify from 'dompurify';
import { AIAssistantService, ReadmeSummaryResponse } from '../../services/assistant.service';
import { CandidateContextService } from '../../services/candidate-context.service';
import { RepoBundleService } from '../../services/repo-bundle.service';

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
  private repoBundle = inject(RepoBundleService);

  contentHtml: SafeHtml = '';
  summaryLoading = false;
  summaryError = '';

  username = '';
  repoName = '';
  repo$!: Observable<RepoDetailVM | null>;

  ngOnInit(): void {
    const { username, repoName } = this.extractRouteParams();

    if (!username || !repoName) {
      this.summaryError = 'Missing candidate or repository.';
      this.repo$ = of(this.toVM(null));
      return;
    }

    this.loadRepoMetadata(username, repoName);
    this.loadReadmeSummary(username, repoName);
  }

  /**
   * Extract username from context and repo name from route params.
   */
  private extractRouteParams(): { username: string; repoName: string } {
    const repoName = this.route.snapshot.paramMap.get('repo') || '';
    const active = this.candidateContext.activeCandidate;
    const username = active?.username ?? '';
    return { username, repoName };
  }

  /**
   * Load repository metadata immediately for quick display.
   * Uses single-repo endpoint to fetch only what we need.
   */
  private loadRepoMetadata(username: string, repoName: string): void {
    this.repo$ = this.repoBundle.getCandidateRepoMetadata(username, repoName).pipe(
      map((res) => this.toVM(res?.repo_entry ?? res?.data ?? null)),
      catchError(() => of(this.toVM(null)))
    );
  }

  /**
   * Load README summary in parallel with metadata.
   * Polls for cache readiness and updates content once available.
   */
  private loadReadmeSummary(username: string, repoName: string): void {
    this.summaryLoading = true;

    this.ai.pollForReadme(username, repoName).pipe(
      map((res: ReadmeSummaryResponse) => {
        const summaryHtml = res?.readme_summary_html || '';
        if (summaryHtml) {
          const cleanHtml = DOMPurify.sanitize(summaryHtml, { USE_PROFILES: { html: true } }) as string;
          this.contentHtml = this.sanitizer.bypassSecurityTrustHtml(cleanHtml);
        } else {
          this.contentHtml = this.sanitizer.bypassSecurityTrustHtml('<p>No README summary available.</p>');
        }
      }),
      catchError((err) => {
        const errorMsg = err?.message || err?.error?.message || 'Failed to load README summary.';
        this.summaryError = errorMsg;
        this.contentHtml = this.sanitizer.bypassSecurityTrustHtml('<p>README summary unavailable.</p>');
        return of(null);
      }),
      finalize(() => {
        this.summaryLoading = false;
      })
    ).subscribe();
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
