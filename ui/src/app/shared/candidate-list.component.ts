import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { CandidateContextService, CandidateContext } from '../services/candidate-context.service';

@Component({
  selector: 'app-candidate-list',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-sm">
      <ng-container *ngIf="candidates$ | async as candidates">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-lg font-semibold tracking-tight">{{ heading }}</h2>
          <span class="text-xs text-[var(--muted)]">
            {{ candidates.length }} candidate{{ candidates.length === 1 ? '' : 's' }}
          </span>
        </div>

        <ng-container *ngIf="candidates.length > 0; else emptyState">
          <div class="space-y-2">
            <button
              *ngFor="let candidate of candidates"
              type="button"
              class="w-full rounded-xl border border-[var(--border)] bg-[var(--card)] px-4 py-3 text-left transition hover:border-[var(--primary)]"
              [ngClass]="{
                'border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]':
                  candidate.username === activeUsername,
                'text-[var(--fg)]': candidate.username !== activeUsername
              }"
              (click)="selectCandidate(candidate)"
              [attr.aria-pressed]="candidate.username === activeUsername"
            >
              <div class="flex items-center justify-between gap-3">
                <span class="font-semibold">{{ candidate.username }}</span>
                <span class="text-xs text-[var(--muted)]">
                  <!-- {{ candidate.jobId ? 'Job ' + candidate.jobId : 'New session' }} -->
                </span>
              </div>
            </button>
          </div>
        </ng-container>
      </ng-container>

      <ng-template #emptyState>
        <p class="text-sm text-[var(--muted)]">
          No candidates yet. Enter someone above or on the home page to populate this list.
        </p>
      </ng-template>
    </div>
  `
})
export class CandidateListComponent {
  private router = inject(Router);
  private candidateContext = inject(CandidateContextService);

  readonly candidates$ = this.candidateContext.candidates$;

  @Input() heading = 'Tracked candidates';
  @Input() autoNavigate?: string;
  @Output() candidateSelected = new EventEmitter<CandidateContext>();

  get activeUsername(): string | null {
    return this.candidateContext.activeUsername;
  }

  selectCandidate(candidate: CandidateContext): void {
    this.candidateContext.setActive(candidate.username);
    this.candidateSelected.emit(candidate);
    if (!this.autoNavigate) {
      return;
    }

    const queryParams: Record<string, string | null> = {
      username: candidate.username
    };

    this.router.navigate([this.autoNavigate], { queryParams });
  }
}
