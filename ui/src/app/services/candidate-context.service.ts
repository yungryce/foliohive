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
  private readonly storageKey = 'cloudfolio.trackedCandidates';
  private readonly activeKey = 'cloudfolio.activeCandidate';
  private readonly maxStored = 5;

  get activeUsername(): string | null {
    return this.activeUsernameSubject.value;
  }

  get activeCandidate(): CandidateContext | null {
    const username = this.activeUsername;
    if (!username) return null;
    return this.candidatesSubject.value.find((c: CandidateContext) => c.username === username) ?? null;
  }

  get storedCandidates(): CandidateContext[] {
    return [...this.candidatesSubject.value];
  }

  constructor() {
    this.loadFromStorage();
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
    const limited = next.slice(0, this.maxStored);
    this.candidatesSubject.next(limited);
    this.activeUsernameSubject.next(username);
    this.persistCandidates(limited);
    this.persistActive(username);
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
    this.persistActive(normalized);
  }

  removeCandidate(username: string): void {
    const normalized = (username || '').trim();
    if (!normalized) return;
    const next = this.candidatesSubject.value.filter((c: CandidateContext) => c.username !== normalized);
    this.candidatesSubject.next(next);
    this.persistCandidates(next);
    if (this.activeUsername === normalized) {
      const nextActive = next[0]?.username ?? null;
      this.activeUsernameSubject.next(nextActive);
      this.persistActive(nextActive);
    }
  }

  private loadFromStorage(): void {
    const storage = this.getStorage();
    if (!storage) return;

    try {
      const rawCandidates = storage.getItem(this.storageKey);
      if (rawCandidates) {
        const parsed = JSON.parse(rawCandidates) as CandidateContext[];
        if (Array.isArray(parsed) && parsed.length) {
          const limited = parsed.slice(0, this.maxStored);
          this.candidatesSubject.next(limited);
        }
      }
    } catch (error) {
      console.warn('Failed to load persisted candidates', error);
    }

    try {
      const active = storage.getItem(this.activeKey);
      if (active) {
        this.activeUsernameSubject.next(active);
      } else if (this.candidatesSubject.value.length) {
        this.activeUsernameSubject.next(this.candidatesSubject.value[0].username);
      }
    } catch (error) {
      console.warn('Failed to load active candidate', error);
    }
  }

  private getStorage(): Storage | null {
    if (typeof window === 'undefined' || !('localStorage' in window)) {
      return null;
    }
    return window.localStorage;
  }

  private persistCandidates(list: CandidateContext[]): void {
    const storage = this.getStorage();
    if (!storage) return;
    try {
      storage.setItem(this.storageKey, JSON.stringify(list.slice(0, this.maxStored)));
    } catch (error) {
      console.warn('Failed to persist candidates', error);
    }
  }

  private persistActive(username: string | null): void {
    const storage = this.getStorage();
    if (!storage) return;
    try {
      if (username) {
        storage.setItem(this.activeKey, username);
      } else {
        storage.removeItem(this.activeKey);
      }
    } catch (error) {
      console.warn('Failed to persist active candidate', error);
    }
  }
}
