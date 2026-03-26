import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, EMPTY, Observable, Subscription, of, timer } from 'rxjs';
import { filter, first, map, switchMap } from 'rxjs/operators';
import { RepoBundleService, JobStatusResponse } from './repo-bundle.service';
import { CandidateContextService } from './candidate-context.service';

export interface PollOptions {
  intervalMs?: number;      // Default: 3000ms (3 seconds)
  maxAttempts?: number;     // Default: 200 attempts (~10 minutes)
}

/**
 * Centralized job polling service. Runs a single persistent poll per job that all
 * components share. Components subscribe to status$ rather than spawning their own polls.
 *
 * Usage:
 * - startJob(username, jobId) — start or continue polling a job (idempotent)
 * - stopJob()                 — explicit cleanup
 * - status$                   — shared observable of the latest job status
 * - currentStatus             — synchronous read of the latest status value
 * - isPolling                 — true while a poll subscription is active
 * - pollRepoReady(...)        — emits once when a specific repo reaches summary_ready
 */
@Injectable({ providedIn: 'root' })
export class JobPollingService implements OnDestroy {
  private readonly repoBundleService = inject(RepoBundleService);
  private readonly candidateContext = inject(CandidateContextService);

  private readonly _status$ = new BehaviorSubject<JobStatusResponse | null>(null);
  readonly status$ = this._status$.asObservable();

  private _activeJob: { username: string; jobId: string } | null = null;
  private _activeSubscription: Subscription | null = null;

  get currentStatus(): JobStatusResponse | null {
    return this._status$.value;
  }

  get isPolling(): boolean {
    return this._activeSubscription !== null && !this._activeSubscription.closed;
  }

  /**
   * Start centralized polling for a job. Idempotent — no-op if already polling
   * the same username + jobId. Stops automatically on terminal state.
   *
   * Each status tick updates status$ and candidateContext so the app-level
   * status badge keeps updating regardless of which view is active.
   */
  startJob(username: string, jobId: string, options: PollOptions = {}): void {
    if (
      this._activeJob?.username === username &&
      this._activeJob?.jobId === jobId &&
      this.isPolling
    ) {
      return;
    }

    this.stopJob();
    this._status$.next(null); // Discard stale status so subscribers don't match the previous job

    const { intervalMs = 3000, maxAttempts = 200 } = options;
    let attempts = 0;
    this._activeJob = { username, jobId };

    this._activeSubscription = timer(0, intervalMs)
      .pipe(
        switchMap(() => {
          if (attempts >= maxAttempts) {
            setTimeout(() => this.stopJob(), 0);
            return EMPTY;
          }
          attempts++;
          return this.repoBundleService.getJobStatus(username, jobId);
        })
      )
      .subscribe({
        next: (status: JobStatusResponse) => {
          this._status$.next(status);
          this.candidateContext.updateProgress(username, { jobStatusCode: status.status });
          if (status.status === 'completed' || status.status === 'failed') {
            const buildStatus = status.status === 'completed' ? 'ready' : 'failed';
            this.candidateContext.updateProgress(username, { buildStatus });
            this.stopJob();
          }
        },
        error: () => {
          this.candidateContext.updateProgress(username, { buildStatus: 'failed', jobStatusCode: 'failed' });
          this.stopJob();
        }
      });
  }

  /**
   * Stop the active poll and clear state. Safe to call multiple times.
   */
  stopJob(): void {
    if (this._activeSubscription) {
      this._activeSubscription.unsubscribe();
      this._activeSubscription = null;
    }
    this._activeJob = null;
  }

  /**
   * Observable that emits once when a specific repo's micro-summary is ready,
   * then completes. Filters the shared status$ — no new poll is created.
   * Emits immediately if the current status already satisfies the condition.
   */
  pollRepoReady(
    username: string,
    jobId: string,
    repoName: string,
    _options: PollOptions = {}
  ): Observable<JobStatusResponse> {
    return this.status$.pipe(
      filter(
        (s): s is JobStatusResponse =>
          !!s &&
          s.username === username &&
          (s.repo_details?.summary_ready?.includes(repoName) === true ||
            s.summary_ready === true)
      ),
      first()
    );
  }

  /**
   * Emits once (void) as soon as metadata is ready for fetching, then completes.
   * Emits immediately if the job has already passed metadata_ready or there is
   * no active poll (data is stable / no job in flight).
   * Use to gate metadata-fetching operations.
   */
  whenMetadataReady(username: string): Observable<void> {
    // If no poll is active, or the active poll is for a different candidate,
    // the data for this candidate is stable — emit immediately.
    if (!this.isPolling || this._activeJob?.username !== username) {
      return of(undefined as void);
    }
    // A poll is running for this candidate; wait for it to signal metadata_ready.
    if (this._status$.value?.metadata_ready) {
      return of(undefined as void);
    }
    return this.status$.pipe(
      filter(s => !!s && s.username === username && !!s.metadata_ready),
      first(),
      map(() => undefined as void)
    );
  }

  /**
   * Emits once (void) as soon as all summaries are ready (job completed), then completes.
   * Emits immediately if the job is already complete or there is no active poll.
   * Use to gate summary-fetching operations.
   */
  whenSummaryReady(username: string): Observable<void> {
    // If no poll is active, or the active poll is for a different candidate,
    // the data for this candidate is stable — emit immediately.
    if (!this.isPolling || this._activeJob?.username !== username) {
      return of(undefined as void);
    }
    // A poll is running for this candidate; wait for it to signal summary_ready.
    if (this._status$.value?.summary_ready) {
      return of(undefined as void);
    }
    return this.status$.pipe(
      filter(s => !!s && s.username === username && !!s.summary_ready),
      first(),
      map(() => undefined as void)
    );
  }

  ngOnDestroy(): void {
    this.stopJob();
  }
}
