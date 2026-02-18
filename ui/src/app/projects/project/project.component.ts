import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Observable, Subject, catchError, map, of, switchMap, takeUntil } from 'rxjs';
import DOMPurify from 'dompurify';
import { CandidateContextService } from '../../services/candidate-context.service';
import { RepoBundleService, ReadmeSummaryResponse } from '../../services/repo-bundle.service';
import { JobPollingService } from '../../services/job-polling.service';

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
export class ProjectComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private sanitizer = inject(DomSanitizer);
  private candidateContext = inject(CandidateContextService);
  private repoBundle = inject(RepoBundleService);
  private jobPollingService = inject(JobPollingService);

  private readonly destroy$ = new Subject<void>();

  contentHtml: SafeHtml = '';
  summaryLoading = false;
  summaryError = '';

  username = '';
  repoName = '';
  jobId: string | null = null;
  repo$!: Observable<RepoDetailVM | null>;

  ngOnInit(): void {
    const { username, repoName } = this.extractRouteParams();

    if (!username || !repoName) {
      this.summaryError = 'Missing candidate or repository.';
      this.repo$ = of(this.toVM(null));
      return;
    }

    this.username = username;
    this.repoName = repoName;
    this.loadRepoMetadata(username, repoName);
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
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
   * Also triggers summary loading once job_id is available.
   */
  private loadRepoMetadata(username: string, repoName: string): void {
    this.repo$ = this.repoBundle.getCandidateRepoMetadata(username, repoName).pipe(
      map((res) => {
        this.jobId = res?.job_id || null;
        // Trigger summary load once we have job_id
        if (this.jobId) {
          this.loadReadmeSummary(username, repoName, this.jobId);
        }
        return this.toVM(res?.repo_entry ?? res?.data ?? null);
      }),
      catchError(() => of(this.toVM(null)))
    );
  }

  /**
   * Load README summary using optimistic approach.
   * Attempts immediate fetch, polls if data not ready.
   */
  private loadReadmeSummary(username: string, repoName: string, jobId: string): void {
    this.summaryLoading = true;
    this.summaryError = '';

    // Optimistically try to load summary
    this.repoBundle.getReadmeSummary(username, repoName).pipe(
      catchError((error) => {
        // Check if error is NOT_READY (404) and we have a job_id
        const isNotReady = error?.status === 404 || error?.error?.error_code === 'NOT_READY';
        
        if (isNotReady && jobId) {
          // Show generating message and poll until files are ready
          this.contentHtml = this.sanitizer.bypassSecurityTrustHtml('<p>Generating summary… Please wait.</p>');
          
          // Poll until files are ready, then retry
          return this.jobPollingService.waitForFilesReady(username, jobId).pipe(
            switchMap(() => this.repoBundle.getReadmeSummary(username, repoName)),
            catchError(() => {
              // Failed even after polling
              return of({ readme_summary_html: '' } as ReadmeSummaryResponse);
            }),
            takeUntil(this.destroy$)
          );
        }
        
        // Not a NOT_READY error or no job_id - return empty
        return of({ readme_summary_html: '' } as ReadmeSummaryResponse);
      }),
      takeUntil(this.destroy$)
    ).subscribe({
      next: (res: ReadmeSummaryResponse) => {
        const summaryHtml = res?.readme_summary_html || '';
        if (summaryHtml) {
          const cleanHtml = DOMPurify.sanitize(summaryHtml, { USE_PROFILES: { html: true } }) as string;
          this.contentHtml = this.sanitizer.bypassSecurityTrustHtml(cleanHtml);
        } else {
          this.contentHtml = this.sanitizer.bypassSecurityTrustHtml('<p>No README summary available yet.</p>');
        }
        this.summaryLoading = false;
      },
      error: (err) => {
        this.summaryLoading = false;
        this.summaryError = 'Failed to load README summary.';
        this.contentHtml = this.sanitizer.bypassSecurityTrustHtml('<p>README summary unavailable.</p>');
      }
    });
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
