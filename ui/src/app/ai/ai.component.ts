import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { Subscription, timer } from 'rxjs';
import { switchMap } from 'rxjs/operators';
import { CandidateContextService } from '../services/candidate-context.service';
import { RepoBundleService, JobStatusResponse } from '../services/repo-bundle.service';
import { AIAssistantService, AIAssistantResponse } from '../services/assistant.service';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { CandidateListComponent } from '../shared/candidate-list.component';

interface SuggestedRepo {
  name: string;
  score: number;
}

@Component({
  selector: 'app-ai',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, CandidateListComponent],
  templateUrl: './ai.component.html',
  styleUrls: ['./ai.component.css'],
})
export class AiComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private repoService = inject(RepoBundleService);
  private candidateContext = inject(CandidateContextService);
  private ai = inject(AIAssistantService);
  private sanitizer = inject(DomSanitizer);

  candidates$ = this.candidateContext.candidates$;

  activeUsername: string | null = null;
  skillsText: string = '';

  // Job status tracking
  activeJobId: string | null = null;
  status: JobStatusResponse | null = null;
  polling = false;

  suggested: SuggestedRepo[] = [];
  noRepositories = false;

  query = '';
  loadingAnswer = false;
  error = '';
  answerHtml: SafeHtml | null = null;
  repositoriesUsed: { name: string; relevance_score: number }[] = [];

  private pollSub: Subscription | null = null;

  ngOnInit(): void {
    const usernameFromUrl = (this.route.snapshot.queryParamMap.get('username') || '').trim();
    const jobIdFromUrl = (this.route.snapshot.queryParamMap.get('job_id') || '').trim();
    
    if (usernameFromUrl) {
      this.candidateContext.upsertCandidate({ username: usernameFromUrl });
    }
    
    this.syncActiveFromContext();
    
    // If we have a fresh job_id from URL, start polling for its completion
    if (jobIdFromUrl) {
      console.log(`[AiComponent] Fresh job_id detected: ${jobIdFromUrl}, starting status polling`);
      this.activeJobId = jobIdFromUrl;
      this.startPollingJobStatus(jobIdFromUrl);
    } else {
      // No fresh job, check if bundle already exists
      this.checkBundleStatusIfReady();
    }
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }

  private syncActiveFromContext(): void {
    const active = this.candidateContext.activeCandidate;
    this.activeUsername = active?.username ?? null;
    this.skillsText = active?.skillsText ?? '';
  }

  selectCandidate(username: string): void {
    this.candidateContext.setActive(username);
    this.syncActiveFromContext();
    this.noRepositories = false;
    this.status = null;
    this.activeJobId = null;
    this.polling = false;
    this.pollSub?.unsubscribe();
    
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { username: this.activeUsername, job_id: null },
      queryParamsHandling: 'merge',
    });
    this.checkBundleStatusIfReady();
    this.suggested = [];
    this.answerHtml = null;
    this.repositoriesUsed = [];
  }

  private startPollingJobStatus(jobId: string): void {
    const username = this.activeUsername;
    if (!username || !jobId) {
      console.warn('[AiComponent] Cannot start polling: missing username or job_id');
      return;
    }

    this.polling = true;
    this.noRepositories = false;
    console.log(`[AiComponent] Starting job status polling for ${username}, job_id: ${jobId}`);

    // Poll every 3 seconds for up to 2 minutes (40 attempts)
    this.pollSub = timer(0, 3000).pipe(
      switchMap(() => this.repoService.getJobStatus(username, jobId))
    ).subscribe({
      next: (statusResponse) => {
        if (!statusResponse) {
          console.warn('[AiComponent] Job status returned null, stopping poll');
          this.stopPollingAndCheck();
          return;
        }

        this.status = statusResponse;
        console.log(`[AiComponent] Job status: ${statusResponse.status}, metadata_ready: ${statusResponse.metadata_ready}, progress: ${statusResponse.progress?.percentage}%`);

        // Load metadata as soon as first repo is cached
        if (statusResponse.metadata_ready && !this.noRepositories) {
          console.log(`[AiComponent] Metadata ready, loading candidate data`);
          this.loadMetadataOnly();
        }

        // Check if all files are complete
        if (statusResponse.files_ready || statusResponse.status === 'completed') {
          console.log(`[AiComponent] All files ready, job completed`);
          this.stopPollingAndCheck();
        } else if (statusResponse.status === 'failed') {
          console.error(`[AiComponent] Job failed`);
          this.stopPollingAndCheck();
        }
        // Continue polling for 'queued', 'syncing', or 'metadata_ready' status
      },
      error: (err) => {
        console.error('[AiComponent] Job status polling error:', err);
        this.stopPollingAndCheck();
      }
    });

    // Safety timeout: stop polling after 2 minutes
    setTimeout(() => {
      if (this.polling) {
        console.warn('[AiComponent] Polling timeout reached (2 minutes), stopping');
        this.stopPollingAndCheck();
      }
    }, 120000);
  }

  private stopPollingAndCheck(): void {
    this.polling = false;
    this.pollSub?.unsubscribe();
    this.pollSub = null;
    
    // Clear job_id from URL to prevent re-polling on component reuse
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { job_id: null },
      queryParamsHandling: 'merge',
    });
    
    this.activeJobId = null;
    this.checkBundleStatusIfReady();
  }

  private loadMetadataOnly(): void {
    const username = this.activeUsername;
    if (!username) return;

    console.log(`[AiComponent] Loading metadata for ${username}`);
    this.repoService.getUserBundle(username, undefined, true).subscribe(bundle => {
      const hasRepos = Array.isArray(bundle?.data) && bundle.data.length > 0;
      this.noRepositories = !hasRepos;
      
      if (hasRepos) {
        console.log(`[AiComponent] Metadata loaded with ${bundle.data.length} repositories`);
        this.loadSuggestions();
      } else {
        console.log(`[AiComponent] No repositories found for ${username}`);
        this.suggested = [];
      }
    });
  }

  private checkBundleStatusIfReady(): void {
    const username = this.activeUsername;
    if (!username) return;

    // Don't check if we're still polling for an active job
    if (this.polling && this.activeJobId) {
      console.log(`[AiComponent] Skipping bundle check - still polling job ${this.activeJobId}`);
      return;
    }

    this.repoService.getUserBundle(username, undefined, true).subscribe(bundle => {
      const hasRepos = Array.isArray(bundle?.data) && bundle.data.length > 0;
      this.noRepositories = !hasRepos;
      
      if (hasRepos) {
        console.log(`[AiComponent] Bundle loaded with ${bundle.data.length} repositories`);
        this.loadSuggestions();
      } else {
        console.log(`[AiComponent] No repositories found for ${username}`);
        this.suggested = [];
        this.answerHtml = null;
        this.repositoriesUsed = [];
      }
    });
  }

  private loadSuggestions(): void {
    const username = this.activeUsername;
    if (!username) return;
    const keywords = (this.skillsText || '')
      .split(/[,\n]/g)
      .map(s => s.trim().toLowerCase())
      .filter(Boolean);

    if (!keywords.length) {
      this.suggested = [];
      return;
    }

    this.repoService.getUserBundle(username, undefined, false).subscribe(bundle => {
      const repos = Array.isArray(bundle?.data) ? bundle.data : [];
      const scored: SuggestedRepo[] = repos
        .map((repo: any) => {
          const name = repo?.name ?? repo?.metadata?.name;
          if (!name) return null;

          const langs = Object.keys(repo?.languages || {}).map((x: string) => x.toLowerCase());
          const categorized = Object.keys(repo?.categorized_types || {}).map((x: string) => x.toLowerCase());
          const tech = (repo?.repoContext?.tech_stack?.primary || []).map((x: string) => (x || '').toLowerCase());
          const haystack = new Set<string>([...langs, ...categorized, ...tech]);

          const score = keywords.reduce((acc, kw) => acc + (haystack.has(kw) ? 1 : 0), 0);
          return { name: String(name), score };
        })
        .filter((x): x is SuggestedRepo => !!x && x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 8);

      this.suggested = scored;
    });
  }

  ask(): void {
    this.error = '';
    const username = this.activeUsername;
    const q = (this.query || '').trim();
    if (!username) {
      this.error = 'Select a candidate first.';
      return;
    }
    if (this.noRepositories) {
      this.error = 'No repositories found for this candidate.';
      return;
    }
    if (!q) {
      this.error = 'Enter a question.';
      return;
    }

    this.loadingAnswer = true;
    this.answerHtml = null;
    this.repositoriesUsed = [];

    this.ai.askPortfolio({ query: q, username }).subscribe({
      next: (res: AIAssistantResponse) => {
        this.loadingAnswer = false;
        this.repositoriesUsed = res.repositories_used || [];
        const rawHtml = marked.parse(res.response || '', { async: false }) as string;
        const clean = DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } }) as string;
        this.answerHtml = this.sanitizer.bypassSecurityTrustHtml(clean);
      },
      error: () => {
        this.loadingAnswer = false;
        this.error = 'Failed to get response.';
      }
    });
  }

  removeActiveCandidate(): void {
    if (!this.activeUsername) return;
    this.candidateContext.removeCandidate(this.activeUsername);
    this.syncActiveFromContext();
    this.noRepositories = false;

    if (!this.activeUsername) {
      this.router.navigate(['/']);
      return;
    }

    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { username: this.activeUsername },
      queryParamsHandling: 'merge',
    });
    this.checkBundleStatusIfReady();
  }
}
