import { Injectable } from '@angular/core';

interface CacheItem<T> {
  value: T;
  expiresAt: number;
  createdAt: number;
}

@Injectable({
  providedIn: 'root'
})
export class CacheService {
  private readonly storagePrefix = 'foliohive_cache_';
  private cleanupInterval: any;

  constructor() {
    // Run cleanup every 5 minutes
    this.cleanupInterval = setInterval(() => {
      this.cleanupExpired();
    }, 5 * 60 * 1000);
    
    // Initial cleanup on service initialization
    this.cleanupExpired();
  }

  /**
   * Set cache item with optional TTL in localStorage
   * @param key Cache key
   * @param value Value to cache
   * @param ttlMs Time to live in milliseconds (default: 24 hours for expensive operations)
   */
  set<T>(key: string, value: T, ttlMs: number = 24 * 60 * 60 * 1000): void {
    const now = Date.now();
    const item: CacheItem<T> = {
      value,
      expiresAt: now + ttlMs,
      createdAt: now
    };
    
    try {
      localStorage.setItem(this.storagePrefix + key, JSON.stringify(item));
    } catch (error) {
      // Handle quota exceeded or other localStorage errors
      console.warn('Failed to cache item:', key, error);
      this.cleanupExpired(); // Try to free up space
    }
  }

  /**
   * Get cached value from localStorage if not expired
   * @param key Cache key
   * @returns Cached value or null if expired/not found
   */
  get<T>(key: string): T | null {
    try {
      const itemStr = localStorage.getItem(this.storagePrefix + key);
      
      if (!itemStr) {
        return null;
      }

      const item = JSON.parse(itemStr) as CacheItem<T>;

      // Check if expired
      if (Date.now() > item.expiresAt) {
        localStorage.removeItem(this.storagePrefix + key);
        return null;
      }

      return item.value;
    } catch (error) {
      console.warn('Failed to retrieve cache item:', key, error);
      return null;
    }
  }

  /**
   * Check if cache has valid (non-expired) entry in localStorage
   * @param key Cache key
   * @returns True if cache has valid entry
   */
  has(key: string): boolean {
    try {
      const itemStr = localStorage.getItem(this.storagePrefix + key);
      
      if (!itemStr) {
        return false;
      }

      const item = JSON.parse(itemStr) as CacheItem<any>;

      // Check if expired
      if (Date.now() > item.expiresAt) {
        localStorage.removeItem(this.storagePrefix + key);
        return false;
      }

      return true;
    } catch (error) {
      return false;
    }
  }

  /**
   * Clear specific key or entire cache from localStorage
   * @param key Optional specific key to clear
   */
  clear(key?: string): void {
    if (key) {
      localStorage.removeItem(this.storagePrefix + key);
    } else {
      // Clear all foliohive cache entries
      const keysToRemove: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const storageKey = localStorage.key(i);
        if (storageKey && storageKey.startsWith(this.storagePrefix)) {
          keysToRemove.push(storageKey);
        }
      }
      keysToRemove.forEach(k => localStorage.removeItem(k));
    }
  }

  /**
   * Remove a specific cache entry from localStorage
   * @param key Cache key to remove
   */
  remove(key: string): void {
    localStorage.removeItem(this.storagePrefix + key);
  }

  /**
   * Get cache statistics from localStorage
   * @returns Object with cache stats
   */
  getStats(): {
    totalEntries: number;
    expiredEntries: number;
    validEntries: number;
    oldestEntry: Date | null;
    newestEntry: Date | null;
    totalSizeBytes: number;
  } {
    const now = Date.now();
    let expiredCount = 0;
    let validCount = 0;
    let oldestTime = Infinity;
    let newestTime = 0;
    let totalSize = 0;

    for (let i = 0; i < localStorage.length; i++) {
      const storageKey = localStorage.key(i);
      if (!storageKey || !storageKey.startsWith(this.storagePrefix)) {
        continue;
      }

      try {
        const itemStr = localStorage.getItem(storageKey);
        if (!itemStr) continue;

        totalSize += itemStr.length;
        const item = JSON.parse(itemStr) as CacheItem<any>;

        if (now > item.expiresAt) {
          expiredCount++;
        } else {
          validCount++;
          oldestTime = Math.min(oldestTime, item.createdAt);
          newestTime = Math.max(newestTime, item.createdAt);
        }
      } catch (error) {
        // Invalid entry, count as expired
        expiredCount++;
      }
    }

    return {
      totalEntries: expiredCount + validCount,
      expiredEntries: expiredCount,
      validEntries: validCount,
      oldestEntry: oldestTime === Infinity ? null : new Date(oldestTime),
      newestEntry: newestTime === 0 ? null : new Date(newestTime),
      totalSizeBytes: totalSize
    };
  }

  /**
   * Manually trigger cleanup of expired entries from localStorage
   */
  cleanupExpired(): void {
    const now = Date.now();
    const keysToDelete: string[] = [];

    for (let i = 0; i < localStorage.length; i++) {
      const storageKey = localStorage.key(i);
      if (!storageKey || !storageKey.startsWith(this.storagePrefix)) {
        continue;
      }

      try {
        const itemStr = localStorage.getItem(storageKey);
        if (!itemStr) continue;

        const item = JSON.parse(itemStr) as CacheItem<any>;
        if (now > item.expiresAt) {
          keysToDelete.push(storageKey);
        }
      } catch (error) {
        // Invalid entry, remove it
        keysToDelete.push(storageKey);
      }
    }

    keysToDelete.forEach(key => localStorage.removeItem(key));
  }

  /**
   * Get remaining TTL for a cache entry from localStorage
   * @param key Cache key
   * @returns Remaining TTL in milliseconds, or -1 if not found/expired
   */
  getRemainingTTL(key: string): number {
    try {
      const itemStr = localStorage.getItem(this.storagePrefix + key);
      
      if (!itemStr) {
        return -1;
      }

      const item = JSON.parse(itemStr) as CacheItem<any>;
      const remaining = item.expiresAt - Date.now();
      return remaining > 0 ? remaining : -1;
    } catch (error) {
      return -1;
    }
  }

  /**
   * Extend TTL for existing cache entry in localStorage
   * @param key Cache key
   * @param additionalTtlMs Additional time in milliseconds
   * @returns True if successfully extended, false if not found
   */
  extendTTL(key: string, additionalTtlMs: number): boolean {
    try {
      const itemStr = localStorage.getItem(this.storagePrefix + key);
      
      if (!itemStr) {
        return false;
      }

      const item = JSON.parse(itemStr) as CacheItem<any>;
      item.expiresAt += additionalTtlMs;
      localStorage.setItem(this.storagePrefix + key, JSON.stringify(item));
      return true;
    } catch (error) {
      return false;
    }
  }

  /**
   * Set cache with custom expiration time in localStorage
   * @param key Cache key
   * @param value Value to cache
   * @param expiresAt Absolute expiration timestamp
   */
  setWithExpiration<T>(key: string, value: T, expiresAt: number): void {
    const item: CacheItem<T> = {
      value,
      expiresAt,
      createdAt: Date.now()
    };
    
    try {
      localStorage.setItem(this.storagePrefix + key, JSON.stringify(item));
    } catch (error) {
      console.warn('Failed to cache item with expiration:', key, error);
      this.cleanupExpired();
    }
  }

  /**
   * Cleanup on service destroy
   */
  ngOnDestroy(): void {
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
    }
  }
}