import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { Subject, takeUntil, switchMap, catchError, of, tap } from 'rxjs';
import { MarkdownModule } from 'ngx-markdown';
import { CandidateContextService } from '../services/candidate-context.service';
import { CandidateListComponent } from '../shared/candidate-list.component';
import { JobStatusBadgeComponent } from '../shared/job-status-badge.component';
import { ProfileService, CandidateProfileResponse, CandidateSummaryResponse } from '../services/profile.service';
import { JobPollingService } from '../services/job-polling.service';
import { CacheService } from '../services/cache.service';

@Component({
  selector: 'app-profile',
  standalone: true,
  imports: [CommonModule, RouterModule, CandidateListComponent, JobStatusBadgeComponent, MarkdownModule],
  templateUrl: './profile.component.html',
  styleUrls: ['./profile.component.css']
})
export class ProfileComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private candidateContext = inject(CandidateContextService);
  private profileService = inject(ProfileService);
  private jobPollingService = inject(JobPollingService);
  private cache = inject(CacheService);

  private readonly destroy$ = new Subject<void>();

  activeUsername: string | null = null;
  profile: CandidateProfileResponse | null = null;
  summaryMarkdown: string | null = null;
  jobStatus: string | null = null;

  loadingProfile = false;
  loadingSummary = false;
  profileError = '';
  summaryError = '';

  ngOnInit(): void {
    const usernameFromUrl = (this.route.snapshot.queryParamMap.get('username') || '').trim();
    if (usernameFromUrl) {
      this.candidateContext.upsertCandidate({ username: usernameFromUrl });
    }

    this.candidateContext.activeUsername$
      .pipe(takeUntil(this.destroy$))
      .subscribe((username) => {
        this.activeUsername = username;
        if (!username) {
          this.profile = null;
          this.summaryMarkdown = null;
          this.profileError = '';
          this.summaryError = '';
          return;
        }
        this.loadProfile(username);
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  selectCandidate(username: string): void {
    this.candidateContext.setActive(username);
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { username },
      queryParamsHandling: 'merge',
    });
  }

  private loadProfile(username: string): void {
    this.loadingProfile = true;
    this.profileError = '';
    this.profile = null;
    this.summaryMarkdown = null;

    this.profileService.getCandidateProfile(username).subscribe({
      next: (profile) => {
        this.profile = profile;
        this.loadingProfile = false;
        const jobId = profile?.job_metadata?.job_id;
        this.loadSummary(username, jobId);
      },
      error: () => {
        this.loadingProfile = false;
        this.profileError = 'Failed to load profile data.';
      }
    });
  }

  private loadSummary(username: string, jobId?: string): void {
    this.loadingSummary = true;
    this.summaryError = '';
    this.summaryMarkdown = null;
    this.jobStatus = null;

    // Check cache first (24 hour TTL for expensive summaries)
    const cacheKey = `profile-summary-${username}-${jobId || 'latest'}`;
    const cached = this.cache.get<CandidateSummaryResponse>(cacheKey);
    
    if (cached) {
      this.summaryMarkdown = cached?.summary_markdown || null;
      this.loadingSummary = false;
      this.jobStatus = null;
      return;
    }

    // Optimistically try to load summary
    this.profileService.getCandidateSummary(username, jobId).pipe(
      switchMap((summary) => {
        // Handle 200+empty case: treat as NOT_READY if jobId is available
        const markdown = summary?.summary_markdown || '';
        if (!markdown && jobId) {
          // Empty response with active job - enter polling chain
          return this.jobPollingService.waitForFilesReady(username, jobId).pipe(
            tap((status) => {
              this.jobStatus = status.status;
            }),
            switchMap(() => this.profileService.getCandidateSummary(username, jobId)),
            catchError(() => {
              return of({ username, summary_markdown: '' } as CandidateSummaryResponse);
            }),
            takeUntil(this.destroy$)
          );
        }
        // Non-empty response or no job_id - return as-is
        return of(summary);
      }),
      catchError((error) => {
        const isNotReady = error?.status === 404 || error?.error?.error_code === 'NOT_READY';
        
        if (isNotReady && jobId) {
          return this.jobPollingService.waitForFilesReady(username, jobId).pipe(
            tap((status) => {
              this.jobStatus = status.status;
            }),
            switchMap(() => this.profileService.getCandidateSummary(username, jobId)),
            catchError(() => {
              return of({ username, summary_markdown: '' } as CandidateSummaryResponse);
            }),
            takeUntil(this.destroy$)
          );
        }
        
        console.warn('Summary not ready or failed to load:', error);
        return of({ username, summary_markdown: '' } as CandidateSummaryResponse);
      }),
      takeUntil(this.destroy$)
    ).subscribe({
      next: (summary: CandidateSummaryResponse) => {
        if (summary?.summary_markdown) {
          this.cache.set(cacheKey, summary, 24 * 60 * 60 * 1000);
        }
        this.summaryMarkdown = summary?.summary_markdown || null;
        this.loadingSummary = false;
        this.jobStatus = null;
      },
      error: () => {
        this.loadingSummary = false;
        this.summaryError = 'Failed to load AI summary.';
        this.jobStatus = null;
      }
    });
  }

  get profileStats(): { label: string; value: number | string }[] {
    const stats = this.profile?.statistics ?? {};
    const github = this.profile?.github_profile ?? {};

    return [
      { label: 'Repos', value: stats.repo_count ?? github.public_repos ?? 0 },
      { label: 'Stars', value: stats.stars_total ?? 0 },
      { label: 'Forks', value: stats.forks_total ?? 0 },
      { label: 'Followers', value: github.followers ?? 0 },
      { label: 'Following', value: github.following ?? 0 },
      { label: 'Gists', value: github.public_gists ?? 0 },
    ];
  }

  get topLanguages(): { language: string; bytes: number }[] {
    return (this.profile?.statistics?.top_languages ?? []) as { language: string; bytes: number }[];
  }

  get topics(): string[] {
    return (this.profile?.statistics?.topics ?? []) as string[];
  }

  get githubProfile(): any {
    return this.profile?.github_profile ?? {};
  }

  formatBlogLink(value?: string): string {
    if (!value) return '';
    if (value.startsWith('http://') || value.startsWith('https://')) return value;
    return `https://${value}`;
  }
}
