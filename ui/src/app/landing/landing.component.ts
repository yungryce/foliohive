import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { Subject, takeUntil, filter, first } from 'rxjs';
import { CandidateContextService } from '../services/candidate-context.service';
import { RepoBundleService, JobStatusResponse } from '../services/repo-bundle.service';
import { JobPollingService } from '../services/job-polling.service';
import { CandidateListComponent } from '../shared/candidate-list.component';
import { JobStatusBadgeComponent } from '../shared/job-status-badge.component';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [CommonModule, FormsModule, CandidateListComponent, JobStatusBadgeComponent],
  templateUrl: './landing.component.html',
  styleUrls: ['./landing.component.css'],
})
export class LandingComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private repoService = inject(RepoBundleService);
  private jobPollingService = inject(JobPollingService);
  private candidateContext = inject(CandidateContextService);
  private destroy$ = new Subject<void>();

  username = '';
  loading = false;
  error = '';

  get activeJobStatus(): string | null {
    const username = (this.username || '').trim();
    if (!username) return null;
    return this.candidateContext.storedCandidates.find(c => c.username === username)?.jobStatusCode ?? null;
  }

  get isAtLimit(): boolean {
    return this.candidateContext.isAtLimit;
  }

  get isExistingCandidate(): boolean {
    const username = (this.username || '').trim();
    if (!username) return false;
    return this.candidateContext.storedCandidates.some(c => c.username === username);
  }
  
  ngOnInit(): void {
    this.syncStoredCandidates();

    const params = this.route.snapshot.queryParamMap;
    const usernameParam = params.get('username')?.trim();
    if (usernameParam) {
      this.username = usernameParam;
      this.start();
    }
  }
  
  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  /**
   * Trigger fresh build and poll until metadata is ready.
   * Redirects to profile page once repositories are synced.
   */
  start(): void {
    if (this.loading) return;

    this.error = '';

    const username = (this.username || '').trim();
    if (!username) {
      this.error = 'Enter a GitHub username.';
      return;
    }

    if (!this.isExistingCandidate && this.isAtLimit) {
      this.error = `Candidate limit reached (${this.candidateContext.storedCandidates.length}/5). Remove a candidate to add a new one.`;
      return;
    }

    this.loading = true;

    // Always trigger fresh build
    this.repoService.startBuild(username).subscribe({
      next: (jobId: string) => {
        this.candidateContext.updateProgress(username, { jobId, buildStatus: 'building', jobStatusCode: 'queued' });
        this.jobPollingService.startJob(username, jobId);
        this.watchForMetadataReady(username, jobId);
      },
      error: (err: unknown) => {
        this.loading = false;
        if (err instanceof HttpErrorResponse && err.status === 429) {
          this.error = `Candidate limit reached (5/5). Remove a candidate to add a new one.`;
        } else {
          this.error = err instanceof Error ? err.message : 'Failed to start build. Check if API is running.';
        }
      },
    });
  }

  /**
   * Subscribe to centralized status$ and navigate to /projects once metadata is ready.
   * The poll itself runs in JobPollingService and outlives this component's lifecycle.
   */
  private watchForMetadataReady(username: string, jobId: string): void {
    this.jobPollingService.status$.pipe(
      filter((s): s is JobStatusResponse =>
        !!s && s.username === username && (!!s.metadata_ready || s.status === 'completed')
      ),
      first(),
      takeUntil(this.destroy$)
    ).subscribe({
      next: () => {
        this.loading = false;
        this.router.navigate(['/projects'], { queryParams: { username, job_id: jobId } });
      },
      error: () => {
        this.loading = false;
        this.candidateContext.updateProgress(username, { buildStatus: 'failed', jobStatusCode: 'failed' });
        this.error = 'Build failed. Please try again.';
      },
      complete: () => {
        // takeUntil(destroy$) fired before metadata_ready — component was destroyed
        // before the condition was met (expected on fast navigation). No-op.
        if (this.loading) {
          this.loading = false;
        }
      }
    });
  }

  /**
   * Sync stored candidates from session storage.
   * Only keeps candidates, no validation calls.
   */
  private syncStoredCandidates(): void {
    const candidates = this.candidateContext.storedCandidates.slice(0, 5);
    
    if (!candidates.length) return;

    // Set first candidate as active if none selected
    if (!this.candidateContext.activeUsername) {
      this.candidateContext.setActive(candidates[0].username);
    }
  }
}
