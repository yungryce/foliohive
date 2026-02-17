import { Injectable, inject } from '@angular/core';
import { Observable, timer, throwError, EMPTY } from 'rxjs';
import { switchMap, takeWhile, finalize, shareReplay } from 'rxjs/operators';
import { RepoBundleService, JobStatusResponse } from './repo-bundle.service';

export interface PollOptions {
  intervalMs?: number;      // Default: 3000ms (3 seconds)
  maxAttempts?: number;     // Default: 40 attempts
  timeoutMs?: number;       // Default: 120000ms (2 minutes)
}

/**
 * Centralized service for polling job status with support for metadata_ready and files_ready states.
 * 
 * Usage:
 * - pollJobStatus() - Poll until completed/failed, emitting all status updates
 * - waitForMetadataReady() - Complete when metadata is ready for display
 * - waitForFilesReady() - Complete when files/summaries are ready for display
 */
@Injectable({ providedIn: 'root' })
export class JobPollingService {
  private repoBundleService = inject(RepoBundleService);

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

    // Set timeout to stop polling
    const timeoutHandle = setTimeout(() => {
      timedOut = true;
    }, timeoutMs);

    return timer(0, intervalMs).pipe(
      switchMap(() => {
        if (timedOut) {
          return EMPTY; // Stop emitting
        }

        attempts++;
        if (attempts > maxAttempts) {
          return EMPTY; // Stop emitting
        }

        return this.repoBundleService.getJobStatus(username, jobId);
      }),
      takeWhile((status) => {
        if (!status) return false;
        
        // Continue polling until completed or failed
        const isActive = status.status !== 'completed' && status.status !== 'failed';
        return isActive;
      }, true), // inclusive=true to emit final completed/failed status
      finalize(() => clearTimeout(timeoutHandle)),
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
        if (!status) return false;
        
        // Complete when metadata is ready or job completed/failed
        if (status.metadata_ready || status.status === 'completed') {
          return false; // Stop and emit this final value
        }
        if (status.status === 'failed') {
          return false;
        }
        return true; // Continue polling
      }, true) // inclusive=true to emit the ready status
    );
  }

  /**
   * Poll until files_ready is true, then complete.
   * Use when you need to wait for file caching/summaries to be available.
   * 
   * @param username - GitHub username
   * @param jobId - Job ID to poll
   * @param options - Polling configuration
   * @returns Observable<JobStatusResponse> completing when files are ready
   */
  waitForFilesReady(
    username: string,
    jobId: string,
    options: PollOptions = {}
  ): Observable<JobStatusResponse> {
    return this.pollJobStatus(username, jobId, options).pipe(
      takeWhile((status) => {
        if (!status) return false;
        
        // Complete when files are ready or job completed/failed
        if (status.files_ready || status.status === 'completed') {
          return false; // Stop and emit this final value
        }
        if (status.status === 'failed') {
          return false;
        }
        return true; // Continue polling
      }, true) // inclusive=true to emit the ready status
    );
  }
}
