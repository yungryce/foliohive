import { Injectable } from '@angular/core';

const STORAGE_KEY = 'foliohive.session_id';

function generateId(): string {
  try {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
      return crypto.randomUUID();
    }
  } catch {
    // ignore
  }
  // Fallback: not cryptographically strong, but stable enough for local dev.
  return `sess_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

@Injectable({ providedIn: 'root' })
export class SessionIdService {
  getOrCreate(): string {
    const existing = localStorage.getItem(STORAGE_KEY);
    if (existing && existing.trim()) return existing;
    const created = generateId();
    localStorage.setItem(STORAGE_KEY, created);
    return created;
  }
}
