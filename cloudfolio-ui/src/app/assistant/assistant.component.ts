import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { AIAssistantService, AIAssistantResponse } from '../services/assistant.service';
import { finalize } from 'rxjs/operators';

@Component({
  selector: 'app-assistant',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './assistant.component.html',
  styleUrls: ['./assistant.component.css']
})
export class AssistantComponent implements OnInit {
  private ai = inject(AIAssistantService);
  private sanitizer = inject(DomSanitizer);

  username = 'yungryce';
  query = '';
  loading = false;
  error = '';
  loadingMessage = '';

  answerHtml: SafeHtml | null = null;
  repositoriesUsed: { name: string; relevance_score: number }[] = [];

  building = false;

  ngOnInit(): void {
    // no local theme handling; global toggle in AppComponent
  }

  triggerBuild(): void {
    if (this.building) return;
    this.building = true;
    this.loadingMessage = 'Preparing repositories… This may take a few minutes.';
    this.ai.startBuild(this.username, true)
      .pipe(finalize(() => { /* keep building active; user can ask later */ }))
      .subscribe({
        next: () => {
          // Keep the info visible; user can retry asking after a short wait.
          this.loading = true;
        },
        error: () => {
          this.building = false;
          this.loading = false;
          this.error = 'Failed to start preparation.';
        }
      });
  }

  ask(): void {
    this.error = '';
    const q = (this.query || '').trim();
    if (!q) {
      this.error = 'Enter a question.';
      return;
    }
    this.loading = true;
    this.answerHtml = null;
    this.repositoriesUsed = [];
    this.loadingMessage = 'Processing your query...';

    this.ai.askPortfolio({ query: q, username: this.username }).subscribe({
      next: (res: AIAssistantResponse) => {
        if (res.total_repositories === 0) {
          // Keep loading true to show the skeleton; surface a helpful message.
          this.loadingMessage = 'We’re preparing your repositories for analysis. This may take a few minutes on first run.';
        } else {
          this.loading = false;
          this.repositoriesUsed = res.repositories_used || [];
          const rawHtml = marked.parse(res.response || '', { async: false }) as string;
          const clean = DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } }) as string;
          this.answerHtml = this.sanitizer.bypassSecurityTrustHtml(clean);
        }
      },
      error: () => {
        this.loading = false;
        this.error = 'Failed to get response.';
      }
    });
  }

  clear(): void {
    this.query = '';
    this.answerHtml = null;
    this.repositoriesUsed = [];
    this.error = '';
  }
}
