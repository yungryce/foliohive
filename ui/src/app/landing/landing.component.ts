import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Subject, takeUntil, tap } from 'rxjs';
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
  
  // Progress tracking
  buildProgress = 0;
  statusMessage = '';
  jobStatus: string | null = null;
  
  ngOnInit(): void {
    this.syncStoredCandidates();

    const params = this.route.snapshot.queryParamMap;
    const usernameParam = params.get('username')?.trim();
    if (usernameParam) {
      this.username = usernameParam;
      if (params.get('autostart') === 'true') {
        this.start();
      }
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
    this.error = '';
    this.buildProgress = 0;
    this.statusMessage = '';
    
    const username = (this.username || '').trim();
    if (!username) {
      this.error = 'Enter a GitHub username.';
      return;
    }

    this.loading = true;
    this.statusMessage = 'Starting build...';

    // Always trigger fresh build
    this.repoService.startBuild(username, true).subscribe({
      next: (jobId: string) => {
        this.candidateContext.upsertCandidate({ username });
        this.statusMessage = 'Syncing repositories...';
        this.pollUntilMetadataReady(username, jobId);
      },
      error: (err: any) => {
        this.loading = false;
        this.buildProgress = 0;
        this.statusMessage = '';
        this.error = 'Failed to start build. Check if API is running.';
      },
    });
  }

  /**
   * Poll job status until metadata_ready, showing progress.
   * Redirects to profile when first repo is cached.
   */
  private pollUntilMetadataReady(username: string, jobId: string): void {
    this.jobPollingService.waitForMetadataReady(username, jobId)
      .pipe(
        tap((status: JobStatusResponse) => {
          if (status) {
            this.jobStatus = status.status;
          }
        }),
        takeUntil(this.destroy$)
      )
      .subscribe({
        next: (status: JobStatusResponse) => {
          if (!status) return;
          
          // Update progress UI
          this.buildProgress = status.progress?.percentage ?? 0;
          const cached = status.progress?.summary_ready ?? 0;
          const total = status.progress?.total ?? 0;
          this.statusMessage = `Synced ${cached} of ${total} repositories...`;
          
          // Redirect when metadata is ready
          if (status.metadata_ready || status.status === 'completed') {
            this.loading = false;
            this.buildProgress = 100;
            this.statusMessage = 'Ready!';
            this.router.navigate(['/projects'], { 
              queryParams: { username, job_id: jobId } 
            });
          }
        },
        error: () => {
          this.loading = false;
          this.buildProgress = 0;
          this.statusMessage = '';
          this.jobStatus = null;
          this.error = 'Build timed out. Please try again.';
        },
        complete: () => {
          // Polling completed without metadata_ready (timeout or failed)
          if (this.loading) {
            this.loading = false;
            this.buildProgress = 0;
            this.statusMessage = '';
            this.jobStatus = null;
            this.error = 'Build did not complete in time. Please try again.';
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
