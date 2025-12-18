import { TestBed } from '@angular/core/testing';
import { CacheService } from './cache.service';

describe('CacheService', () => {
  let service: CacheService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(CacheService);
  });

  afterEach(() => {
    // Clear cache after each test to avoid interference
    service.clear();
  });

  describe('Basic Cache Operations', () => {
    it('should be created', () => {
      expect(service).toBeTruthy();
    });

    it('should set and get a value', () => {
      const key = 'test-key';
      const value = { data: 'test-data' };

      service.set(key, value);
      const result = service.get(key);

      expect(result).toEqual(value);
    });

    it('should return null for non-existent key', () => {
      const result = service.get('non-existent-key');
      expect(result).toBeNull();
    });

    it('should check if key exists', () => {
      const key = 'test-key';
      const value = 'test-value';

      expect(service.has(key)).toBeFalse();
      
      service.set(key, value);
      expect(service.has(key)).toBeTrue();
    });

    it('should remove a specific key', () => {
      const key = 'test-key';
      const value = 'test-value';

      service.set(key, value);
      expect(service.has(key)).toBeTrue();

      service.remove(key);
      expect(service.has(key)).toBeFalse();
      expect(service.get(key)).toBeNull();
    });

    it('should clear all cache entries', () => {
      service.set('key1', 'value1');
      service.set('key2', 'value2');

      expect(service.has('key1')).toBeTrue();
      expect(service.has('key2')).toBeTrue();

      service.clear();

      expect(service.has('key1')).toBeFalse();
      expect(service.has('key2')).toBeFalse();
    });

    it('should clear specific key', () => {
      service.set('key1', 'value1');
      service.set('key2', 'value2');

      service.clear('key1');

      expect(service.has('key1')).toBeFalse();
      expect(service.has('key2')).toBeTrue();
    });
  });

  describe('TTL (Time To Live) Functionality', () => {
    it('should use default TTL of 12 hours', () => {
      const key = 'test-key';
      const value = 'test-value';

      service.set(key, value);
      const remainingTTL = service.getRemainingTTL(key);

      // Should be close to 12 hours (43200000 ms), allowing for small execution time
      expect(remainingTTL).toBeGreaterThan(43199000); // 12 hours - 1 second
      expect(remainingTTL).toBeLessThanOrEqual(43200000); // 12 hours
    });

    it('should respect custom TTL', () => {
      const key = 'test-key';
      const value = 'test-value';
      const customTTL = 5000; // 5 seconds

      service.set(key, value, customTTL);
      const remainingTTL = service.getRemainingTTL(key);

      expect(remainingTTL).toBeGreaterThan(4000); // At least 4 seconds remaining
      expect(remainingTTL).toBeLessThanOrEqual(5000); // At most 5 seconds
    });

    it('should expire entries after TTL', (done) => {
      const key = 'test-key';
      const value = 'test-value';
      const shortTTL = 100; // 100ms

      service.set(key, value, shortTTL);
      
      // Should exist initially
      expect(service.has(key)).toBeTrue();
      expect(service.get(key)).toEqual(value);

      // Should expire after TTL
      setTimeout(() => {
        expect(service.has(key)).toBeFalse();
        expect(service.get(key)).toBeNull();
        done();
      }, 150); // Wait longer than TTL
    });

    it('should return -1 for TTL of non-existent key', () => {
      const remainingTTL = service.getRemainingTTL('non-existent-key');
      expect(remainingTTL).toBe(-1);
    });

    it('should return -1 for TTL of expired key', (done) => {
      const key = 'test-key';
      const value = 'test-value';
      const shortTTL = 50; // 50ms

      service.set(key, value, shortTTL);

      setTimeout(() => {
        const remainingTTL = service.getRemainingTTL(key);
        expect(remainingTTL).toBe(-1);
        done();
      }, 100); // Wait for expiration
    });

    it('should extend TTL for existing key', () => {
      const key = 'test-key';
      const value = 'test-value';
      const initialTTL = 1000; // 1 second
      const extension = 2000; // 2 seconds

      service.set(key, value, initialTTL);
      const initialRemaining = service.getRemainingTTL(key);
      
      const extended = service.extendTTL(key, extension);
      const newRemaining = service.getRemainingTTL(key);

      expect(extended).toBeTrue();
      expect(newRemaining).toBeGreaterThan(initialRemaining);
      expect(newRemaining).toBeGreaterThan(2000); // Should be more than extension
    });

    it('should not extend TTL for non-existent key', () => {
      const extended = service.extendTTL('non-existent-key', 1000);
      expect(extended).toBeFalse();
    });

    it('should set cache with custom expiration time', () => {
      const key = 'test-key';
      const value = 'test-value';
      const futureTime = Date.now() + 5000; // 5 seconds from now

      service.setWithExpiration(key, value, futureTime);
      
      expect(service.has(key)).toBeTrue();
      expect(service.get(key)).toEqual(value);

      const remainingTTL = service.getRemainingTTL(key);
      expect(remainingTTL).toBeGreaterThan(4000);
      expect(remainingTTL).toBeLessThanOrEqual(5000);
    });
  });

  describe('Cache Statistics', () => {
    it('should return correct statistics for empty cache', () => {
      const stats = service.getStats();

      expect(stats.totalEntries).toBe(0);
      expect(stats.validEntries).toBe(0);
      expect(stats.expiredEntries).toBe(0);
      expect(stats.oldestEntry).toBeNull();
      expect(stats.newestEntry).toBeNull();
    });

    it('should return correct statistics for cache with valid entries', () => {
      service.set('key1', 'value1');
      service.set('key2', 'value2');

      const stats = service.getStats();

      expect(stats.totalEntries).toBe(2);
      expect(stats.validEntries).toBe(2);
      expect(stats.expiredEntries).toBe(0);
      expect(stats.oldestEntry).toBeInstanceOf(Date);
      expect(stats.newestEntry).toBeInstanceOf(Date);
    });

    it('should return correct statistics with expired entries', (done) => {
      const shortTTL = 50; // 50ms
      
      service.set('valid-key', 'valid-value');
      service.set('expired-key', 'expired-value', shortTTL);

      setTimeout(() => {
        const stats = service.getStats();

        expect(stats.totalEntries).toBe(2);
        expect(stats.validEntries).toBe(1);
        expect(stats.expiredEntries).toBe(1);
        done();
      }, 100); // Wait for expiration
    });
  });

  describe('Cache Cleanup', () => {
    it('should manually cleanup expired entries', (done) => {
      const shortTTL = 50; // 50ms
      
      service.set('key1', 'value1');
      service.set('key2', 'value2', shortTTL);

      setTimeout(() => {
        // Before cleanup
        let stats = service.getStats();
        expect(stats.totalEntries).toBe(2);
        expect(stats.expiredEntries).toBe(1);

        // Manual cleanup
        service.cleanupExpired();

        // After cleanup
        stats = service.getStats();
        expect(stats.totalEntries).toBe(1);
        expect(stats.expiredEntries).toBe(0);
        expect(stats.validEntries).toBe(1);
        done();
      }, 100); // Wait for expiration
    });

    it('should automatically remove expired entries on get', (done) => {
      const key = 'test-key';
      const value = 'test-value';
      const shortTTL = 50; // 50ms

      service.set(key, value, shortTTL);

      setTimeout(() => {
        // Accessing expired entry should remove it
        const result = service.get(key);
        expect(result).toBeNull();

        const stats = service.getStats();
        expect(stats.totalEntries).toBe(0);
        done();
      }, 100); // Wait for expiration
    });

    it('should automatically remove expired entries on has check', (done) => {
      const key = 'test-key';
      const value = 'test-value';
      const shortTTL = 50; // 50ms

      service.set(key, value, shortTTL);

      setTimeout(() => {
        // Checking expired entry should remove it
        const exists = service.has(key);
        expect(exists).toBeFalse();

        const stats = service.getStats();
        expect(stats.totalEntries).toBe(0);
        done();
      }, 100); // Wait for expiration
    });
  });

  describe('Data Types', () => {
    it('should handle string values', () => {
      const key = 'string-key';
      const value = 'string-value';

      service.set(key, value);
      expect(service.get(key)).toBe(value);
    });

    it('should handle number values', () => {
      const key = 'number-key';
      const value = 42;

      service.set(key, value);
      expect(service.get(key)).toBe(value);
    });

    it('should handle boolean values', () => {
      const key = 'boolean-key';
      const value = true;

      service.set(key, value);
      expect(service.get(key)).toBe(value);
    });

    it('should handle object values', () => {
      const key = 'object-key';
      const value = { name: 'test', count: 123, active: true };

      service.set(key, value);
      expect(service.get(key)).toEqual(value);
    });

    it('should handle array values', () => {
      const key = 'array-key';
      const value = [1, 2, 3, 'test', { nested: true }];

      service.set(key, value);
      expect(service.get(key)).toEqual(value);
    });

    it('should handle null values', () => {
      const key = 'null-key';
      const value = null;

      service.set(key, value);
      expect(service.get(key)).toBeNull();
    });
  });

  describe('Edge Cases', () => {
    it('should handle empty string as key', () => {
      const key = '';
      const value = 'empty-key-value';

      service.set(key, value);
      expect(service.get(key)).toBe(value);
    });

    it('should handle overwriting existing key', () => {
      const key = 'overwrite-key';
      const oldValue = 'old-value';
      const newValue = 'new-value';

      service.set(key, oldValue);
      expect(service.get(key)).toBe(oldValue);

      service.set(key, newValue);
      expect(service.get(key)).toBe(newValue);
    });

    it('should handle zero TTL', () => {
      const key = 'zero-ttl-key';
      const value = 'zero-ttl-value';

      service.set(key, value, 0);
      
      // Should be immediately expired
      expect(service.get(key)).toBeNull();
      expect(service.has(key)).toBeFalse();
    });

    it('should handle negative TTL', () => {
      const key = 'negative-ttl-key';
      const value = 'negative-ttl-value';

      service.set(key, value, -1000);
      
      // Should be immediately expired
      expect(service.get(key)).toBeNull();
      expect(service.has(key)).toBeFalse();
    });
  });
});