import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { SessionIdService } from './session-id.service';

export interface ChatHistoryRepository {
  name: string;
  relevance_score: number;
}

export interface ChatHistoryEntry {
  id: string;
  username: string;
  query: string;
  responseMarkdown: string;
  repositoriesUsed: ChatHistoryRepository[];
  createdAt: string;
  requestMetadata?: Record<string, unknown>;
}

const STORAGE_KEY_PREFIX = 'foliohive.aiHistory';
const MAX_ENTRIES_PER_CANDIDATE = 20;

function createEntryId(): string {
  try {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
      return crypto.randomUUID();
    }
  } catch {
  }
  return `chat_${Date.now().toString(16)}_${Math.random().toString(16).slice(2)}`;
}

@Injectable({ providedIn: 'root' })
export class ChatHistoryService {
  private readonly sessionId: string;
  private readonly historySubject = new BehaviorSubject<ChatHistoryEntry[]>([]);
  private activeUsername: string | null = null;

  readonly history$ = this.historySubject.asObservable();

  constructor(sessionIdService: SessionIdService) {
    this.sessionId = sessionIdService.getOrCreate();
  }

  setActiveCandidate(username: string | null): void {
    const normalized = (username || '').trim() || null;
    this.activeUsername = normalized;
    this.historySubject.next(normalized ? this.readHistory(normalized) : []);
  }

  appendEntry(
    username: string,
    entry: Omit<ChatHistoryEntry, 'id' | 'username' | 'createdAt'> & Partial<Pick<ChatHistoryEntry, 'id' | 'createdAt'>>,
  ): ChatHistoryEntry {
    const normalized = (username || '').trim();
    if (!normalized) {
      throw new Error('username is required for chat history');
    }

    const nextEntry: ChatHistoryEntry = {
      id: entry.id || createEntryId(),
      username: normalized,
      query: entry.query,
      responseMarkdown: entry.responseMarkdown,
      repositoriesUsed: Array.isArray(entry.repositoriesUsed) ? entry.repositoriesUsed : [],
      createdAt: entry.createdAt || new Date().toISOString(),
      requestMetadata: entry.requestMetadata,
    };

    const nextHistory = [...this.readHistory(normalized), nextEntry]
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
      .slice(-MAX_ENTRIES_PER_CANDIDATE);

    this.writeHistory(normalized, nextHistory);
    if (this.activeUsername === normalized) {
      this.historySubject.next(nextHistory);
    }
    return nextEntry;
  }

  clearCandidateHistory(username: string): void {
    const normalized = (username || '').trim();
    if (!normalized) {
      return;
    }

    const storage = this.getStorage();
    if (storage) {
      try {
        storage.removeItem(this.storageKey(normalized));
      } catch {
      }
    }

    if (this.activeUsername === normalized) {
      this.historySubject.next([]);
    }
  }

  clearActiveHistory(): void {
    if (this.activeUsername) {
      this.clearCandidateHistory(this.activeUsername);
    }
  }

  private readHistory(username: string): ChatHistoryEntry[] {
    const storage = this.getStorage();
    if (!storage) {
      return [];
    }

    try {
      const raw = storage.getItem(this.storageKey(username));
      if (!raw) {
        return [];
      }

      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }

      return parsed.filter((item): item is ChatHistoryEntry => {
        return Boolean(
          item &&
          typeof item.id === 'string' &&
          typeof item.username === 'string' &&
          typeof item.query === 'string' &&
          typeof item.responseMarkdown === 'string' &&
          typeof item.createdAt === 'string' &&
          Array.isArray(item.repositoriesUsed),
        );
      });
    } catch {
      return [];
    }
  }

  private writeHistory(username: string, entries: ChatHistoryEntry[]): void {
    const storage = this.getStorage();
    if (!storage) {
      return;
    }

    try {
      storage.setItem(this.storageKey(username), JSON.stringify(entries));
    } catch {
    }
  }

  private storageKey(username: string): string {
    return `${STORAGE_KEY_PREFIX}.${this.sessionId}.${username}`;
  }

  private getStorage(): Storage | null {
    if (typeof window === 'undefined' || !('localStorage' in window)) {
      return null;
    }
    return window.localStorage;
  }
}