import { Component, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { CandidateContextService } from '../services/candidate-context.service';
import { RepoBundleService } from '../services/repo-bundle.service';
import { CandidateListComponent } from '../shared/candidate-list.component';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [CommonModule, FormsModule, CandidateListComponent],
  templateUrl: './landing.component.html',
  styleUrls: ['./landing.component.css'],
})
export class LandingComponent implements OnInit {
  private router = inject(Router);
  private repoService = inject(RepoBundleService);
  private candidateContext = inject(CandidateContextService);

  username = '';

  loading = false;
  error = '';

  start(): void {
    this.error = '';
    const username = (this.username || '').trim();
    if (!username) {
      this.error = 'Enter a GitHub username.';
      return;
    }

    this.loading = true;

    // Check if latest job exists and is valid
    this.repoService.getUserBundle(username, undefined, false).subscribe({
      next: (bundle) => {
        if (bundle?.job_id) {
          // Job exists, verify status to ensure it's still valid
          this.repoService.getJobStatus(username, bundle.job_id).subscribe({
            next: (status) => {
              // Job exists and is valid, navigate to AI view
              this.candidateContext.upsertCandidate({ username });
              this.loading = false;
              this.router.navigate(['/ai'], { queryParams: { username, job_id: bundle.job_id } });
            },
            error: (err) => {
              // Job status check failed (404 or error), trigger new build
              this.triggerNewBuild(username);
            }
          });
        } else {
          // No job_id in bundle, trigger new build
          this.triggerNewBuild(username);
        }
      },
      error: (err) => {
        // No bundle found, trigger new build
        this.triggerNewBuild(username);
      },
    });
  }

  private triggerNewBuild(username: string): void {
    this.repoService.startBuild(username, true).subscribe({
      next: (response) => {
        const jobId = response?.job_id;
        this.candidateContext.upsertCandidate({ username });
        this.loading = false;
        this.router.navigate(['/ai'], { queryParams: { username, job_id: jobId } });
      },
      error: (err: any) => {
        this.loading = false;
        this.error = 'Failed to start refresh. Is api-gateway running?';
      },
    });
  }

  ngOnInit(): void {
    this.restoreStoredCandidates();
  }

  private restoreStoredCandidates(): void {
    const candidates = this.candidateContext.storedCandidates.slice(0, 5);
    console.log('Restoring candidates from storage:', candidates);
    if (!candidates.length) return;

    if (!this.candidateContext.activeUsername) {
      this.candidateContext.setActive(candidates[0].username);
      console.log('No active candidate. Setting active to:', candidates[0].username);
    }

    candidates.forEach(candidate => {
      this.repoService.getUserBundle(candidate.username, undefined, false).subscribe((bundle) => {
        // Remove candidate if no valid job or data found
        if (!bundle?.job_id || !bundle?.data || bundle.data.length === 0) {
          this.candidateContext.removeCandidate(candidate.username);
        }
      });
    });
  }
}
