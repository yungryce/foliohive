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

interface SuggestedRepo {
  name: string;
  score: number;
}

@Component({
  selector: 'app-ai',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
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
  activeJobId: string | null = null;
  skillsText: string = '';

  status: JobStatusResponse | null = null;
  polling = false;

  suggested: SuggestedRepo[] = [];

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
      this.candidateContext.upsertCandidate({ username: usernameFromUrl, jobId: jobIdFromUrl || undefined });
    }
    this.syncActiveFromContext();
    this.startPollingIfPossible();
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }

  private syncActiveFromContext(): void {
    const active = this.candidateContext.activeCandidate;
    this.activeUsername = active?.username ?? null;
    this.activeJobId = active?.jobId ?? null;
    this.skillsText = active?.skillsText ?? '';
  }

  selectCandidate(username: string): void {
    this.candidateContext.setActive(username);
    this.syncActiveFromContext();
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { username: this.activeUsername, job_id: this.activeJobId || null },
      queryParamsHandling: 'merge',
    });
    this.startPollingIfPossible();
    this.suggested = [];
    this.answerHtml = null;
    this.repositoriesUsed = [];
  }

  private startPollingIfPossible(): void {
    this.pollSub?.unsubscribe();
    this.status = null;
    this.polling = false;

    const username = this.activeUsername;
    const jobId = this.activeJobId;
    if (!username || !jobId) return;

    this.polling = true;
    this.pollSub = timer(0, 2000)
      .pipe(switchMap(() => this.repoService.getJobStatus(username, jobId)))
      .subscribe((status) => {
        if (!status) return;
        this.status = status;
        if (status.status === 'completed') {
          this.polling = false;
          this.pollSub?.unsubscribe();
          this.loadSuggestions();
        }
      });
  }

  private loadSuggestions(): void {
    const username = this.activeUsername;
    if (!username) return;
    const jobId = this.activeJobId || undefined;
    const keywords = (this.skillsText || '')
      .split(/[,\n]/g)
      .map(s => s.trim().toLowerCase())
      .filter(Boolean);

    if (!keywords.length) {
      this.suggested = [];
      return;
    }

    this.repoService.getUserBundle(username, jobId, false).subscribe(bundle => {
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
}
