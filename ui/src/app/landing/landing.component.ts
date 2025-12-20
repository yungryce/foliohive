import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { CandidateContextService } from '../services/candidate-context.service';
import { RepoBundleService } from '../services/repo-bundle.service';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './landing.component.html',
  styleUrls: ['./landing.component.css'],
})
export class LandingComponent {
  private router = inject(Router);
  private repoService = inject(RepoBundleService);
  private candidateContext = inject(CandidateContextService);

  username = '';
  skillsText = '';
  notesText = '';

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
    this.repoService.startBuild(username, true).subscribe({
      next: (res) => {
        const jobId = res?.job_id;
        this.candidateContext.upsertCandidate({ username, jobId: jobId || undefined, skillsText: this.skillsText || undefined });
        this.router.navigate(['/ai'], { queryParams: { username, job_id: jobId || null } });
      },
      error: () => {
        this.loading = false;
        this.error = 'Failed to start refresh. Is api-gateway running?';
      },
      complete: () => {
        this.loading = false;
      },
    });
  }
}
