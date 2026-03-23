import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { CandidateContextService, CandidateContext } from '../services/candidate-context.service';
import { RepoBundleService } from '../services/repo-bundle.service';

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
            <div
              *ngFor="let candidate of candidates"
              class="w-full rounded-xl border border-[var(--border)] bg-[var(--card)] px-3 py-2.5 transition"
              [ngClass]="{
                'border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]':
                  candidate.username === activeUsername,
                'text-[var(--fg)] hover:border-[var(--primary)]': candidate.username !== activeUsername
              }"
            >
              <div class="flex items-center gap-2 min-w-0">
                <!-- username — truncates if too long -->
                <button
                  type="button"
                  class="min-w-0 flex-1 truncate text-left text-sm font-semibold bg-transparent border-none p-0 cursor-pointer"
                  (click)="selectCandidate(candidate)"
                  [attr.aria-pressed]="candidate.username === activeUsername"
                  [title]="candidate.username"
                >{{ candidate.username }}</button>

                <!-- action icons — pinned to right, never wrap -->
                <div class="flex items-center gap-0.5 shrink-0">
                  <!-- pulse dot while a job is in progress -->
                  <span
                    *ngIf="candidate.buildStatus === 'building'"
                    title="Build in progress"
                    class="inline-block w-2 h-2 rounded-full animate-pulse mr-1"
                    [ngClass]="candidate.username === activeUsername ? 'bg-[var(--primary-foreground)]' : 'bg-[var(--primary)]'"
                  ></span>

                  <button
                    type="button"
                    title="Refresh"
                    class="p-1 rounded bg-transparent border-none cursor-pointer text-base leading-none transition-colors"
                    [ngClass]="candidate.username === activeUsername
                      ? 'text-[var(--primary-foreground)]/60 hover:text-[var(--primary-foreground)]'
                      : 'text-[var(--muted)] hover:text-[var(--fg)]'"
                    (click)="refreshCandidate(candidate)"
                  >↺</button>

                  <button
                    type="button"
                    title="Remove"
                    class="p-1 rounded bg-transparent border-none cursor-pointer text-base leading-none transition-colors"
                    [ngClass]="candidate.username === activeUsername
                      ? 'text-[var(--primary-foreground)]/60 hover:text-[var(--primary-foreground)]'
                      : 'text-[var(--muted)] hover:text-red-500'"
                    (click)="deleteCandidate(candidate)"
                  >✕</button>
                </div>
              </div>
            </div>
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
  private repoBundleService = inject(RepoBundleService);

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

  refreshCandidate(candidate: CandidateContext): void {
    this.router.navigate(['/'], { queryParams: { username: candidate.username } });
  }

  deleteCandidate(candidate: CandidateContext): void {
    this.candidateContext.removeCandidate(candidate.username);
    this.repoBundleService.deleteCandidate(candidate.username).subscribe();
  }
}
