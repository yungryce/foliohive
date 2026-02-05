import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Subject, takeUntil } from 'rxjs';
import DOMPurify from 'dompurify';
import { CandidateContextService } from '../services/candidate-context.service';
import { CandidateListComponent } from '../shared/candidate-list.component';
import { ProfileService, CandidateProfileResponse, CandidateSummaryResponse } from '../services/profile.service';

@Component({
  selector: 'app-profile',
  standalone: true,
  imports: [CommonModule, RouterModule, CandidateListComponent],
  templateUrl: './profile.component.html',
  styleUrls: ['./profile.component.css']
})
export class ProfileComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private candidateContext = inject(CandidateContextService);
  private profileService = inject(ProfileService);
  private sanitizer = inject(DomSanitizer);

  private readonly destroy$ = new Subject<void>();

  activeUsername: string | null = null;
  profile: CandidateProfileResponse | null = null;
  summaryHtml: SafeHtml | null = null;

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
          this.summaryHtml = null;
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
    this.summaryHtml = null;

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
    this.summaryHtml = null;

    this.profileService.getCandidateSummary(username, jobId).subscribe({
      next: (summary: CandidateSummaryResponse) => {
        const html = summary?.summary_html || '';
        if (html) {
          const cleanHtml = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } }) as string;
          this.summaryHtml = this.sanitizer.bypassSecurityTrustHtml(cleanHtml);
        } else {
          this.summaryHtml = this.sanitizer.bypassSecurityTrustHtml('<p>No summary available yet.</p>');
        }
        this.loadingSummary = false;
      },
      error: () => {
        this.loadingSummary = false;
        this.summaryError = 'Failed to load AI summary.';
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
