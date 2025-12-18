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
  private cache = new Map<string, CacheItem<any>>();
  private cleanupInterval: any;

  constructor() {
    // Run cleanup every 5 minutes
    this.cleanupInterval = setInterval(() => {
      this.cleanupExpired();
    }, 5 * 60 * 1000);
  }

  /**
   * Set cache item with optional TTL
   * @param key Cache key
   * @param value Value to cache
   * @param ttlMs Time to live in milliseconds (default: 15 minutes)
   */
  set<T>(key: string, value: T, ttlMs: number = 15 * 60 * 1000): void {
    const now = Date.now();
    const item: CacheItem<T> = {
      value,
      expiresAt: now + ttlMs,
      createdAt: now
    };
    
    this.cache.set(key, item);
  }

  /**
   * Get cached value if not expired
   * @param key Cache key
   * @returns Cached value or null if expired/not found
   */
  get<T>(key: string): T | null {
    const item = this.cache.get(key) as CacheItem<T> | undefined;
    
    if (!item) {
      return null;
    }

    // Check if expired
    if (Date.now() > item.expiresAt) {
      this.cache.delete(key);
      return null;
    }

    return item.value;
  }

  /**
   * Check if cache has valid (non-expired) entry
   * @param key Cache key
   * @returns True if cache has valid entry
   */
  has(key: string): boolean {
    const item = this.cache.get(key);
    
    if (!item) {
      return false;
    }

    // Check if expired
    if (Date.now() > item.expiresAt) {
      this.cache.delete(key);
      return false;
    }

    return true;
  }

  /**
   * Clear specific key or entire cache
   * @param key Optional specific key to clear
   */
  clear(key?: string): void {
    if (key) {
      this.cache.delete(key);
    } else {
      this.cache.clear();
    }
  }

  /**
   * Remove a specific cache entry
   * @param key Cache key to remove
   */
  remove(key: string): void {
    this.cache.delete(key);
  }

  /**
   * Get cache statistics
   * @returns Object with cache stats
   */
  getStats(): {
    totalEntries: number;
    expiredEntries: number;
    validEntries: number;
    oldestEntry: Date | null;
    newestEntry: Date | null;
  } {
    const now = Date.now();
    let expiredCount = 0;
    let validCount = 0;
    let oldestTime = Infinity;
    let newestTime = 0;

    for (const [key, item] of this.cache.entries()) {
      if (now > item.expiresAt) {
        expiredCount++;
      } else {
        validCount++;
        oldestTime = Math.min(oldestTime, item.createdAt);
        newestTime = Math.max(newestTime, item.createdAt);
      }
    }

    return {
      totalEntries: this.cache.size,
      expiredEntries: expiredCount,
      validEntries: validCount,
      oldestEntry: oldestTime === Infinity ? null : new Date(oldestTime),
      newestEntry: newestTime === 0 ? null : new Date(newestTime)
    };
  }

  /**
   * Manually trigger cleanup of expired entries
   */
  cleanupExpired(): void {
    const now = Date.now();
    const keysToDelete: string[] = [];

    for (const [key, item] of this.cache.entries()) {
      if (now > item.expiresAt) {
        keysToDelete.push(key);
      }
    }

    keysToDelete.forEach(key => this.cache.delete(key));
    
    if (keysToDelete.length > 0) {
      console.log(`Cache cleanup: Removed ${keysToDelete.length} expired entries`);
    }
  }

  /**
   * Get remaining TTL for a cache entry
   * @param key Cache key
   * @returns Remaining TTL in milliseconds, or -1 if not found/expired
   */
  getRemainingTTL(key: string): number {
    const item = this.cache.get(key);
    
    if (!item) {
      return -1;
    }

    const remaining = item.expiresAt - Date.now();
    return remaining > 0 ? remaining : -1;
  }

  /**
   * Extend TTL for existing cache entry
   * @param key Cache key
   * @param additionalTtlMs Additional time in milliseconds
   * @returns True if successfully extended, false if not found
   */
  extendTTL(key: string, additionalTtlMs: number): boolean {
    const item = this.cache.get(key);
    
    if (!item) {
      return false;
    }

    item.expiresAt += additionalTtlMs;
    return true;
  }

  /**
   * Set cache with custom expiration time
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
    
    this.cache.set(key, item);
  }

  /**
   * Cleanup on service destroy
   */
  ngOnDestroy(): void {
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
    }
    this.cache.clear();
  }
}