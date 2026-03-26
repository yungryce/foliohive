import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { MarkdownModule } from 'ngx-markdown';
import { Observable, Subject, catchError, filter, map, of, switchMap, takeUntil } from 'rxjs';
import { CandidateContextService } from '../../services/candidate-context.service';
import { RepoBundleService, ReadmeSummaryResponse } from '../../services/repo-bundle.service';
import { JobPollingService } from '../../services/job-polling.service';
import { JobStatusBadgeComponent } from '../../shared/job-status-badge.component';

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
  imports: [CommonModule, RouterModule, JobStatusBadgeComponent, MarkdownModule],
  templateUrl: './project.component.html',
  styleUrls: ['./project.component.css']
})
export class ProjectComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private candidateContext = inject(CandidateContextService);
  private repoBundle = inject(RepoBundleService);
  private jobPollingService = inject(JobPollingService);

  private readonly destroy$ = new Subject<void>();

  contentMarkdown = '';
  summaryLoading = false;
  summaryError = '';
  jobStatus: string | null = null;

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
    // Start polling before the gate so whenMetadataReady has live status data
    const storedJobId = this.candidateContext.activeCandidate?.jobId;
    if (storedJobId) {
      this.startJobIfNeeded(username, storedJobId);
    }
    const bypassCache = this.jobPollingService.isPolling;

    this.repo$ = this.jobPollingService.whenMetadataReady(username).pipe(
      switchMap(() => this.repoBundle.getCandidateRepoMetadata(username, repoName, !bypassCache)),
      map((res) => {
        this.jobId = res?.job_id || null;
        if (this.jobId) {
          this.startJobIfNeeded(this.username, this.jobId);
          this.loadReadmeSummary(this.username, repoName, this.jobId);
        }
        return this.toVM(res?.repo_entry ?? res?.data ?? null);
      }),
      catchError(() => of(this.toVM(null)))
    );
  }

  private loadReadmeSummary(username: string, repoName: string, jobId: string): void {
    this.summaryLoading = true;
    this.summaryError = '';
    this.contentMarkdown = '';
    this.jobStatus = null;

    const current = this.jobPollingService.currentStatus;
    const isRepoReady =
      current?.repo_details?.summary_ready?.includes(repoName) === true ||
      current?.summary_ready === true ||
      this.candidateContext.activeCandidate?.buildStatus === 'ready';

    if (isRepoReady) {
      // Summary is already available — fetch immediately
      this.repoBundle.getReadmeSummary(username, repoName, jobId).pipe(
        catchError((err) => {
          if (err?.error?.error?.code === 'CACHE_MISSING') {
            this.router.navigate(['/'], { queryParams: { username } });
          }
          return of({ readme_summary_markdown: '' } as ReadmeSummaryResponse);
        }),
        takeUntil(this.destroy$)
      ).subscribe({
        next: (res) => {
          this.contentMarkdown = res?.readme_summary_markdown || '';
          this.summaryLoading = false;
        },
        error: () => {
          this.summaryLoading = false;
          this.summaryError = 'Failed to load README summary.';
        }
      });
      return;
    }

    // Track real-time job status for badge while waiting
    this.jobPollingService.status$.pipe(
      filter(s => !!s && s.username === username),
      takeUntil(this.destroy$)
    ).subscribe(s => {
      if (this.summaryLoading) {
        this.jobStatus = s!.status;
      }
    });

    // Wait for this specific repo to reach summary_ready via the centralized poll
    this.jobPollingService.pollRepoReady(username, jobId, repoName).pipe(
      switchMap(() => this.repoBundle.getReadmeSummary(username, repoName, jobId)),
      catchError((err) => {
        if (err?.error?.error?.code === 'CACHE_MISSING') {
          this.router.navigate(['/'], { queryParams: { username } });
        }
        return of({ readme_summary_markdown: '' } as ReadmeSummaryResponse);
      }),
      takeUntil(this.destroy$)
    ).subscribe({
      next: (res: ReadmeSummaryResponse) => {
        this.contentMarkdown = res?.readme_summary_markdown || '';
        this.summaryLoading = false;
        this.jobStatus = null;
      },
      error: () => {
        this.summaryLoading = false;
        this.summaryError = 'Failed to load README summary.';
        this.jobStatus = null;
      }
    });
  }

  private startJobIfNeeded(username: string, jobId: string): void {
    const current = this.jobPollingService.currentStatus;
    // Already have status for this exact job (any state)
    if (current?.job_id === jobId) return;
    // A different job is actively being polled — don't interrupt
    if (this.jobPollingService.isPolling) return;
    // Stored context indicates the job was already completed
    const stored = this.candidateContext.activeCandidate;
    if (stored?.buildStatus === 'ready' || stored?.buildStatus === 'failed') return;
    this.jobPollingService.startJob(username, jobId);
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
