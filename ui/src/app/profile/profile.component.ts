import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { Subject, takeUntil, switchMap, catchError, of, tap, filter, first } from 'rxjs';
import { MarkdownModule } from 'ngx-markdown';
import { CandidateContextService } from '../services/candidate-context.service';
import { CandidateListComponent } from '../shared/candidate-list.component';
import { JobStatusBadgeComponent } from '../shared/job-status-badge.component';
import { ProfileService, CandidateProfileResponse, CandidateSummaryResponse } from '../services/profile.service';
import { JobPollingService } from '../services/job-polling.service';

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

  private readonly destroy$ = new Subject<void>();

  activeUsername: string | null = null;
  profile: CandidateProfileResponse | null = null;
  summaryMarkdown: string | null = null;
  jobStatus: string | null = null;

  loadingProfile = false;
  loadingSummary = false;
  profileError = '';
  summaryError = '';

  get activeJobStatusCode(): string | null {
    return this.candidateContext.activeCandidate?.jobStatusCode ?? null;
  }

  ngOnInit(): void {
    const usernameFromUrl = (this.route.snapshot.queryParamMap.get('username') || '').trim();
    if (usernameFromUrl) {
      this.candidateContext.setActive(usernameFromUrl);
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

    // Start polling before deciding whether to wait so whenMetadataReady has live data
    const storedJobId = this.candidateContext.activeCandidate?.jobId;
    if (storedJobId) {
      this.startJobIfNeeded(username, storedJobId);
    }

    this.jobPollingService.whenMetadataReady(username).pipe(
      switchMap(() => this.profileService.getCandidateProfile(username)),
      takeUntil(this.destroy$)
    ).subscribe({
      next: (profile) => {
        this.profile = profile;
        this.loadingProfile = false;
        const jobId = profile?.job_metadata?.job_id;
        if (jobId) {
          this.startJobIfNeeded(username, jobId);
        }
        this.loadProfileSummary(username, jobId);
      },
      error: () => {
        this.loadingProfile = false;
        this.profileError = 'Failed to load profile data.';
      }
    });
  }

  private loadProfileSummary(username: string, jobId?: string): void {
    this.loadingSummary = true;
    this.summaryError = '';
    this.summaryMarkdown = null;
    this.jobStatus = null;

    const current = this.jobPollingService.currentStatus;
    const isSummaryReady =
      current?.summary_ready === true ||
      this.candidateContext.activeCandidate?.buildStatus === 'ready';

    if (isSummaryReady || !jobId) {
      // Summary is already available — fetch immediately
      this.profileService.getCandidateSummary(username, jobId).pipe(
        catchError((err) => {
          if (err?.error?.error?.code === 'CACHE_MISSING') {
            this.router.navigate(['/'], { queryParams: { username } });
          }
          return of({ username, summary_markdown: '' } as CandidateSummaryResponse);
        }),
        takeUntil(this.destroy$)
      ).subscribe((summary) => {
        this.summaryMarkdown = summary?.summary_markdown || null;
        this.loadingSummary = false;
      });
      return;
    }

    // Wait for the centralized poll to signal summary_ready
    this.jobPollingService.status$.pipe(
      tap((s) => { if (s) this.jobStatus = s.status; }),
      filter(s => !!s && s.username === username && !!s.summary_ready),
      first(),
      switchMap(() => this.profileService.getCandidateSummary(username, jobId)),
      catchError((err) => {
        if (err?.error?.error?.code === 'CACHE_MISSING') {
          this.router.navigate(['/'], { queryParams: { username } });
        }
        return of({ username, summary_markdown: '' } as CandidateSummaryResponse);
      }),
      takeUntil(this.destroy$)
    ).subscribe({
      next: (summary: CandidateSummaryResponse) => {
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
