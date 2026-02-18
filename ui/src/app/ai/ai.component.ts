import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { Subject, takeUntil, switchMap, catchError, of } from 'rxjs';
import { CandidateContextService } from '../services/candidate-context.service';
import { RepoBundleService } from '../services/repo-bundle.service';
import { JobPollingService } from '../services/job-polling.service';
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
  private jobPollingService = inject(JobPollingService);
  private candidateContext = inject(CandidateContextService);
  private ai = inject(AIAssistantService);
  private sanitizer = inject(DomSanitizer);

  private readonly destroy$ = new Subject<void>();

  candidates$ = this.candidateContext.candidates$;

  activeUsername: string | null = null;
  activeJobId: string | null = null;
  skillsText: string = '';

  suggested: SuggestedRepo[] = [];
  noRepositories = false;

  query = '';
  loadingAnswer = false;
  error = '';
  answerHtml: SafeHtml | null = null;
  repositoriesUsed: { name: string; relevance_score: number }[] = [];

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
          this.noRepositories = false;
          this.suggested = [];
          this.answerHtml = null;
          this.repositoriesUsed = [];
          this.error = '';
          return;
        }
        this.loadCandidateData(username);
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

  private loadCandidateData(username: string): void {
    const active = this.candidateContext.activeCandidate;
    this.skillsText = active?.skillsText ?? '';
    
    // Reset state
    this.noRepositories = false;
    this.suggested = [];
    this.answerHtml = null;
    this.repositoriesUsed = [];
    this.error = '';

    // Load metadata to check if repos exist and get job_id
    this.repoService.getCandidateMetadata(username, undefined, true).subscribe({
      next: (bundle) => {
        const hasRepos = Array.isArray(bundle?.data) && bundle.data.length > 0;
        this.noRepositories = !hasRepos;
        this.activeJobId = bundle?.job_id || null;
        
        if (hasRepos) {
          this.loadSuggestions();
        }
      },
      error: () => {
        this.noRepositories = true;
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

    this.repoService.getCandidateMetadata(username, undefined, false).subscribe(bundle => {
      const repos = Array.isArray(bundle?.data) ? bundle.data : [];
      const scored: SuggestedRepo[] = repos
        .map((repo: any) => {
          const name = repo?.name;
          if (!name) return null;

          // Extract searchable terms from backend schema
          const langs = Object.keys(repo?.languages || {}).map((x: string) => x.toLowerCase());
          const topics = Array.isArray(repo?.metadata?.topics) 
            ? repo.metadata.topics.map((x: string) => x.toLowerCase()) 
            : [];
          const haystack = new Set<string>([...langs, ...topics]);

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

    // Optimistically try to get answer
    this.ai.askPortfolio({ query: q, username }).pipe(
      catchError((error) => {
        // Check if error is NOT_READY (404) and we have a job_id
        const isNotReady = error?.status === 404 || error?.error?.error_code === 'NOT_READY';
        
        if (isNotReady && this.activeJobId) {
          // Show generating message and poll until files are ready
          const generatingMsg = 'Generating answer… Please wait while we prepare your portfolio data.';
          const rawHtml = marked.parse(generatingMsg, { async: false }) as string;
          const clean = DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } }) as string;
          this.answerHtml = this.sanitizer.bypassSecurityTrustHtml(clean);
          
          // Poll until files are ready, then retry
          return this.jobPollingService.waitForFilesReady(username, this.activeJobId).pipe(
            switchMap(() => this.ai.askPortfolio({ query: q, username })),
            catchError(() => {
              // Failed even after polling
              return of({
                response: 'Failed to generate answer after data was ready. Please try again.',
                repositories_used: [],
                total_repositories: 0,
                query: q
              } as AIAssistantResponse);
            }),
            takeUntil(this.destroy$)
          );
        }
        
        // Not a NOT_READY error or no job_id
        const errorMsg = error?.error?.message || error?.message || 'Failed to get response.';
        return of({
          response: errorMsg,
          repositories_used: [],
          total_repositories: 0,
          query: q
        } as AIAssistantResponse);
      }),
      takeUntil(this.destroy$)
    ).subscribe({
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
    
    if (!this.candidateContext.activeCandidate) {
      this.router.navigate(['/']);
    }
  }
}
