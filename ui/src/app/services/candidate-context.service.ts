import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export interface CandidateContext {
  username: string;
  jobId?: string;
  skillsText?: string;
}

@Injectable({ providedIn: 'root' })
export class CandidateContextService {
  private readonly candidatesSubject = new BehaviorSubject<CandidateContext[]>([]);
  readonly candidates$ = this.candidatesSubject.asObservable();

  private readonly activeUsernameSubject = new BehaviorSubject<string | null>(null);
  readonly activeUsername$ = this.activeUsernameSubject.asObservable();

  get activeUsername(): string | null {
    return this.activeUsernameSubject.value;
  }

  get activeCandidate(): CandidateContext | null {
    const username = this.activeUsername;
    if (!username) return null;
    return this.candidatesSubject.value.find((c: CandidateContext) => c.username === username) ?? null;
  }

  upsertCandidate(candidate: CandidateContext): void {
    const username = (candidate.username || '').trim();
    if (!username) return;

    const existing = this.candidatesSubject.value;
    const idx = existing.findIndex((c: CandidateContext) => c.username === username);
    const next = [...existing];
    if (idx >= 0) {
      next[idx] = { ...next[idx], ...candidate, username };
    } else {
      next.unshift({ ...candidate, username });
    }
    this.candidatesSubject.next(next);
    this.activeUsernameSubject.next(username);
  }

  setActive(username: string): void {
    const normalized = (username || '').trim();
    if (!normalized) return;
    const exists = this.candidatesSubject.value.some((c: CandidateContext) => c.username === normalized);
    if (!exists) {
      this.upsertCandidate({ username: normalized });
      return;
    }
    this.activeUsernameSubject.next(normalized);
  }
}
