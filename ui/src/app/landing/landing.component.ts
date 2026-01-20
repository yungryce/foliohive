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
      console.log('[LandingComponent] No username provided.');
      this.error = 'Enter a GitHub username.';
      return;
    }

    console.log(`[LandingComponent] Starting process for username: ${username}`);
    this.loading = true;

    // Check if bundle already exists
    this.repoService.checkBundle(username).subscribe({
      next: (exists) => {
        console.log(`[LandingComponent] Bundle check for ${username}: ${exists ? 'exists' : 'does not exist'}`);
        if (exists) {
          // Bundle exists, navigate directly without forcing refresh
          this.candidateContext.upsertCandidate({ username });
          this.loading = false;
          console.log(`[LandingComponent] Navigating to /ai for username: ${username}`);
          this.router.navigate(['/ai'], { queryParams: { username } });
        } else {
          // Bundle doesn't exist, trigger refresh
          console.log(`[LandingComponent] Triggering refresh for username: ${username}`);
          this.repoService.startBuild(username, true).subscribe({
            next: (response) => {
              const jobId = response?.job_id;
              console.log(`[LandingComponent] Refresh started successfully for username: ${username}, job_id: ${jobId}`);
              this.candidateContext.upsertCandidate({ username });
              this.loading = false;
              this.router.navigate(['/ai'], { queryParams: { username, job_id: jobId } });
            },
            error: (err) => {
              console.error(`[LandingComponent] Failed to start refresh for username: ${username}`, err);
              this.loading = false;
              this.error = 'Failed to start refresh. Is api-gateway running?';
            },
          });
        }
      },
      error: (err) => {
        console.error(`[LandingComponent] Failed to check bundle for username: ${username}`, err);
        this.loading = false;
        this.error = 'Failed to check bundle. Is api-gateway running?';
      },
    });
  }

  ngOnInit(): void {
    this.restoreStoredCandidates();
  }

  private restoreStoredCandidates(): void {
    const candidates = this.candidateContext.storedCandidates.slice(0, 5);
    if (!candidates.length) return;

    if (!this.candidateContext.activeUsername) {
      this.candidateContext.setActive(candidates[0].username);
    }

    candidates.forEach(candidate => {
      this.repoService.checkBundle(candidate.username).subscribe((exists) => {
        if (!exists) {
          this.candidateContext.removeCandidate(candidate.username);
        }
      });
    });
  }
}
