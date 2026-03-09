import { Injectable, inject } from '@angular/core';
import { Observable, timer, throwError, EMPTY, Subject } from 'rxjs';
import { switchMap, takeWhile, finalize, shareReplay, takeUntil } from 'rxjs/operators';
import { RepoBundleService, JobStatusResponse } from './repo-bundle.service';

export interface PollOptions {
  intervalMs?: number;      // Default: 3000ms (3 seconds)
  maxAttempts?: number;     // Default: 40 attempts
  timeoutMs?: number;       // Default: 120000ms (2 minutes)
}

/**
 * Centralized service for polling job status with support for metadata_ready and summary_ready states.
 * 
 * Usage:
 * - pollJobStatus() - Poll until completed/failed, emitting all status updates
 * - waitForMetadataReady() - Complete when metadata is ready for display
 * - waitForFilesReady() - Complete when summaries are ready for display
 */
@Injectable({ providedIn: 'root' })
export class JobPollingService {
  private repoBundleService = inject(RepoBundleService);
  private stop$ = new Subject<void>();  // Signal to stop polling early

  /**
   * Poll job status until completed or failed.
   * Emits status updates including metadata_ready and files_ready flags.
   * 
   * @param username - GitHub username
   * @param jobId - Job ID to poll
   * @param options - Polling configuration
   * @returns Observable<JobStatusResponse> emitting status updates until job completes
   */
  pollJobStatus(
    username: string,
    jobId: string,
    options: PollOptions = {}
  ): Observable<JobStatusResponse> {
    const {
      intervalMs = 3000,
      maxAttempts = 40,
      timeoutMs = 120000
    } = options;

    let attempts = 0;
    let timedOut = false;

    console.log(`[pollJobStatus] starting: username=${username}, jobId=${jobId}, intervalMs=${intervalMs}, maxAttempts=${maxAttempts}`);

    // Set timeout to stop polling
    const timeoutHandle = setTimeout(() => {
      timedOut = true;
      console.warn(`[pollJobStatus] timeout: exceeded ${timeoutMs}ms`);
    }, timeoutMs);

    return timer(0, intervalMs).pipe(
      takeUntil(this.stop$),  // Stop polling when child methods signal completion
      switchMap(() => {
        if (timedOut) {
          console.log(`[pollJobStatus] stopping: timeout flag set`);
          return EMPTY; // Stop emitting
        }

        attempts++;
        if (attempts > maxAttempts) {
          console.warn(`[pollJobStatus] stopping: exceeded ${maxAttempts} attempts`);
          return EMPTY; // Stop emitting
        }

        console.debug(`[pollJobStatus] poll attempt ${attempts}/${maxAttempts}`);
        return this.repoBundleService.getJobStatus(username, jobId);
      }),
      takeWhile((status) => {
        if (!status) {
          console.warn(`[pollJobStatus] received null status`);
          return false;
        }
        
        // Continue polling until completed or failed
        const isActive = status.status !== 'completed' && status.status !== 'failed';
        console.debug(`[pollJobStatus] status=${status.status}, metadata_ready=${status.metadata_ready}, summary_ready=${status.summary_ready}, isActive=${isActive}`);
        return isActive;
      }, true), // inclusive=true to emit final completed/failed status
      finalize(() => {
        clearTimeout(timeoutHandle);
        console.log(`[pollJobStatus] complete after ${attempts} attempts`);
      }),
      shareReplay(1) // Share the same observable for multiple subscribers
    );
  }

  /**
   * Poll until metadata_ready is true, then complete.
   * Use when you need to wait for repository metadata to be available.
   * 
   * @param username - GitHub username
   * @param jobId - Job ID to poll
   * @param options - Polling configuration
   * @returns Observable<JobStatusResponse> completing when metadata is ready
   */
  waitForMetadataReady(
    username: string,
    jobId: string,
    options: PollOptions = {}
  ): Observable<JobStatusResponse> {
    return this.pollJobStatus(username, jobId, options).pipe(
      takeWhile((status) => {
        if (!status) {
          console.warn(`[waitForMetadataReady] null status`);
          return false;
        }
        
        // Complete when metadata_ready flag is true or job failed
        if (status.metadata_ready) {
          this.stop$.next();  // Signal root polling to stop
          return false; // Stop and emit this final value
        }
        if (status.status === 'failed') {
          this.stop$.next();  // Signal root polling to stop
          return false;
        }
        console.debug(`[waitForMetadataReady] waiting: status=${status.status}, metadata_ready=${status.metadata_ready}, progress=${status.progress?.completed}/${status.progress?.total}`);
        return true; // Continue polling
      }, true) // inclusive=true to emit the ready status
    );
  }

  /**
   * Poll until summary_ready is true, then complete.
   * Use when you need to wait for micro-summaries to be available.
   * 
   * @param username - GitHub username
   * @param jobId - Job ID to poll
   * @param options - Polling configuration
   * @returns Observable<JobStatusResponse> completing when summaries are ready
   */
  waitForFilesReady(
    username: string,
    jobId: string,
    options: PollOptions = {}
  ): Observable<JobStatusResponse> {
    return this.pollJobStatus(username, jobId, options).pipe(
      takeWhile((status) => {
        if (!status) {
          console.warn(`[waitForFilesReady] null status`);
          return false;
        }
        
        // Complete when summary_ready flag is true or job failed
        if (status.summary_ready) {
          this.stop$.next();  // Signal root polling to stop
          return false; // Stop and emit this final value
        }
        if (status.status === 'failed') {
          this.stop$.next();  // Signal root polling to stop
          return false;
        }
        console.debug(`[waitForFilesReady] waiting: status=${status.status}, summary_ready=${status.summary_ready}, progress=${status.progress?.summary_ready}/${status.progress?.total}`);
        return true; // Continue polling
      }, true) // inclusive=true to emit the ready status
    );
  }

  /**
   * Poll until a specific repository's summary is ready, then complete.
   * Checks repo_details.summary_ready array to see if the repo name is present.
   * Use when you need to wait for a single repo micro-summary before fetching.
   * 
   * @param username - GitHub username
   * @param jobId - Job ID to poll
   * @param repoName - Repository name to wait for
   * @param options - Polling configuration
   * @returns Observable<JobStatusResponse> completing when repo summary is ready
   */
  pollRepoReady(
    username: string,
    jobId: string,
    repoName: string,
    options: PollOptions = {}
  ): Observable<JobStatusResponse> {
    return this.pollJobStatus(username, jobId, options).pipe(
      takeWhile((status) => {
        if (!status) {
          console.warn(`[pollRepoReady] null status for repo=${repoName}`);
          return false;
        }
        
        // Complete when this repo is in summary_ready or job failed
        const isRepoReady = status.repo_details?.summary_ready?.includes(repoName) ?? false;
        if (isRepoReady) {
          this.stop$.next();  // Signal root polling to stop
          return false; // Stop and emit this final value
        }
        if (status.status === 'failed') {
          this.stop$.next();  // Signal root polling to stop
          return false;
        }
        console.debug(`[pollRepoReady] waiting: repo=${repoName}, status=${status.status}, ready=${status.repo_details?.summary_ready?.length}/${status.progress?.total}`);
        return true; // Continue polling
      }, true) // inclusive=true to emit the ready status
    );
  }
}
