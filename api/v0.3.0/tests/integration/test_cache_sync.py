"""
Integration tests for cache synchronization across Cloudfolio workers.

Tests cache consistency, fingerprint management, and data integrity between workers.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Cache Store Fixture
# ---------------------------------------------------------------------------

class InMemoryCacheStore:
    """In-memory cache store for testing cache operations."""

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def save(
        self,
        key: str,
        data: Any,
        ttl: Optional[int] = None,
        fingerprint: Optional[str] = None,
    ) -> bool:
        expires_at = None
        if ttl:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()

        self._store[key] = {
            "data": data,
            "fingerprint": fingerprint,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
        }
        return True

    def get(self, key: str) -> Dict[str, Any]:
        if key not in self._store:
            return {"status": "missing", "data": None}

        entry = self._store[key]

        # Check expiration
        if entry.get("expires_at"):
            expires_at = datetime.fromisoformat(entry["expires_at"])
            if datetime.now(timezone.utc) > expires_at:
                del self._store[key]
                return {"status": "expired", "data": None}

        return {
            "status": "valid",
            "data": entry["data"],
            "fingerprint": entry.get("fingerprint"),
            "cached_at": entry.get("cached_at"),
        }

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
        return True

    def exists(self, key: str) -> bool:
        return key in self._store

    def keys(self) -> List[str]:
        return list(self._store.keys())


@pytest.fixture
def cache_store():
    """Provide a fresh in-memory cache store."""
    return InMemoryCacheStore()


# ---------------------------------------------------------------------------
# Cache Key Generation Tests
# ---------------------------------------------------------------------------

class TestCacheKeyGeneration:
    """Test cache key generation consistency."""

    def test_repo_cache_key_format(self):
        """Verify repo cache key format is consistent."""
        username = "testuser"
        repo_name = "test-repo"

        key = f"repo_level_bundle_{username}_{repo_name}"

        assert username in key
        assert repo_name in key
        assert key.startswith("repo_level_bundle_")

    def test_bundle_cache_key_format(self):
        """Verify bundle cache key format is consistent."""
        username = "testuser"

        key = f"repos_bundle_context_{username}"

        assert username in key
        assert key.startswith("repos_bundle_context_")

    def test_job_cache_key_format(self):
        """Verify job cache key format is consistent."""
        job_id = str(uuid.uuid4())

        key = f"job:{job_id}"

        assert job_id in key
        assert key.startswith("job:")

    def test_model_cache_key_format(self):
        """Verify model cache key formats."""
        fingerprint = "abc123def456"

        # With fingerprint
        key_with_fp = f"model_{fingerprint}"
        assert fingerprint in key_with_fp

        # Without fingerprint (metadata)
        key_metadata = "fine_tuned_model_metadata"
        assert "metadata" in key_metadata

    def test_cache_keys_are_deterministic(self):
        """Verify same inputs always produce same cache keys."""
        username = "testuser"
        repo_name = "my-repo"

        key1 = f"repo_level_bundle_{username}_{repo_name}"
        key2 = f"repo_level_bundle_{username}_{repo_name}"

        assert key1 == key2

    def test_different_users_have_different_keys(self):
        """Verify different users have isolated cache keys."""
        repo_name = "shared-repo"

        key_user1 = f"repo_level_bundle_user1_{repo_name}"
        key_user2 = f"repo_level_bundle_user2_{repo_name}"

        assert key_user1 != key_user2


# ---------------------------------------------------------------------------
# Fingerprint Tracking Tests
# ---------------------------------------------------------------------------

class TestFingerprintTracking:
    """Test fingerprint generation and tracking."""

    def test_fingerprint_from_metadata(self):
        """Test fingerprint generation from repo metadata."""
        metadata = {
            "name": "test-repo",
            "updated_at": "2025-01-15T12:00:00Z",
            "pushed_at": "2025-01-15T11:00:00Z",
            "size": 1024,
        }

        # Generate fingerprint
        payload = json.dumps(metadata, sort_keys=True)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()[:16]

        assert len(fingerprint) == 16
        assert fingerprint.isalnum()

    def test_fingerprint_changes_with_content(self):
        """Verify fingerprint changes when content changes."""
        metadata_v1 = {"name": "repo", "updated_at": "2025-01-15T12:00:00Z"}
        metadata_v2 = {"name": "repo", "updated_at": "2025-01-16T12:00:00Z"}

        fp1 = hashlib.sha256(json.dumps(metadata_v1, sort_keys=True).encode()).hexdigest()
        fp2 = hashlib.sha256(json.dumps(metadata_v2, sort_keys=True).encode()).hexdigest()

        assert fp1 != fp2

    def test_fingerprint_stable_for_same_content(self):
        """Verify fingerprint is stable for unchanged content."""
        metadata = {"name": "repo", "updated_at": "2025-01-15T12:00:00Z"}

        fp1 = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
        fp2 = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()

        assert fp1 == fp2

    def test_bundle_fingerprint_from_repo_fingerprints(self):
        """Verify bundle fingerprint derived from constituent repos."""
        repo_fingerprints = ["fp_alpha", "fp_beta", "fp_gamma"]

        # Sort for determinism
        combined = "_".join(sorted(repo_fingerprints))
        bundle_fp = hashlib.sha256(combined.encode()).hexdigest()[:16]

        assert len(bundle_fp) == 16


# ---------------------------------------------------------------------------
# Cross-Worker Cache Consistency Tests
# ---------------------------------------------------------------------------

class TestCrossWorkerCacheConsistency:
    """Test cache consistency between workers."""

    def test_sync_worker_cache_readable_by_merge_worker(self, cache_store):
        """Verify data cached by sync worker is readable by merge worker."""
        username = "testuser"
        repo_name = "shared-repo"

        # Sync worker saves repo data
        repo_key = f"repo_level_bundle_{username}_{repo_name}"
        repo_data = {
            "name": repo_name,
            "readme": "# Test Repo",
            "fingerprint": "fp_sync_123",
        }
        cache_store.save(repo_key, repo_data, fingerprint="fp_sync_123")

        # Merge worker reads the same data
        result = cache_store.get(repo_key)

        assert result["status"] == "valid"
        assert result["data"]["name"] == repo_name
        assert result["fingerprint"] == "fp_sync_123"

    def test_merge_worker_bundle_readable_by_api_gateway(self, cache_store):
        """Verify bundle cached by merge worker is readable by API gateway."""
        username = "testuser"

        # Merge worker saves bundle
        bundle_key = f"repos_bundle_context_{username}"
        bundle = [
            {"name": "repo-1", "fingerprint": "fp_1"},
            {"name": "repo-2", "fingerprint": "fp_2"},
        ]
        cache_store.save(bundle_key, bundle, fingerprint="bundle_fp")

        # API gateway reads bundle
        result = cache_store.get(bundle_key)

        assert result["status"] == "valid"
        assert len(result["data"]) == 2
        assert result["fingerprint"] == "bundle_fp"

    def test_job_status_accessible_across_workers(self, cache_store):
        """Verify job status is accessible across all workers."""
        job_id = str(uuid.uuid4())
        job_key = f"job:{job_id}"

        # API gateway creates job
        cache_store.save(job_key, {
            "job_id": job_id,
            "status": "queued",
            "total_repos": 3,
            "completed_repos": 0,
        })

        # Sync worker updates progress
        job_data = cache_store.get(job_key)["data"]
        job_data["completed_repos"] = 1
        job_data["status"] = "syncing"
        cache_store.save(job_key, job_data)

        # Merge worker reads progress
        result = cache_store.get(job_key)
        assert result["data"]["completed_repos"] == 1

        # Merge worker updates to completed
        job_data = result["data"]
        job_data["status"] = "completed"
        cache_store.save(job_key, job_data)

        # API gateway reads final status
        final = cache_store.get(job_key)
        assert final["data"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Cache Update Semantics Tests
# ---------------------------------------------------------------------------

class TestCacheUpdateSemantics:
    """Test cache update behavior."""

    def test_save_overwrites_existing_data(self, cache_store):
        """Verify save completely overwrites existing entry."""
        key = "test_key"

        # Initial save
        cache_store.save(key, {"version": 1, "field_a": "value_a"})

        # Overwrite with new data
        cache_store.save(key, {"version": 2, "field_b": "value_b"})

        result = cache_store.get(key)
        assert result["data"]["version"] == 2
        assert "field_b" in result["data"]
        assert "field_a" not in result["data"]

    def test_fingerprint_updated_on_save(self, cache_store):
        """Verify fingerprint is updated on save."""
        key = "test_key"

        cache_store.save(key, {"data": "v1"}, fingerprint="fp_v1")
        result1 = cache_store.get(key)
        assert result1["fingerprint"] == "fp_v1"

        cache_store.save(key, {"data": "v2"}, fingerprint="fp_v2")
        result2 = cache_store.get(key)
        assert result2["fingerprint"] == "fp_v2"

    def test_cached_at_updated_on_save(self, cache_store):
        """Verify cached_at timestamp is updated on save."""
        key = "test_key"

        cache_store.save(key, {"data": "initial"})
        result1 = cache_store.get(key)
        cached_at_1 = result1["cached_at"]

        # Small delay to ensure different timestamp
        import time
        time.sleep(0.01)

        cache_store.save(key, {"data": "updated"})
        result2 = cache_store.get(key)
        cached_at_2 = result2["cached_at"]

        assert cached_at_2 >= cached_at_1


# ---------------------------------------------------------------------------
# Cache Expiration Tests
# ---------------------------------------------------------------------------

class TestCacheExpiration:
    """Test cache TTL and expiration."""

    def test_no_ttl_means_no_expiration(self, cache_store):
        """Verify entries without TTL don't expire."""
        key = "persistent_key"
        cache_store.save(key, {"data": "persistent"}, ttl=None)

        result = cache_store.get(key)
        assert result["status"] == "valid"

    def test_expired_entry_returns_expired_status(self, cache_store):
        """Verify expired entries are detected."""
        key = "expiring_key"

        # Save with very short TTL (already expired)
        cache_store._store[key] = {
            "data": {"data": "expired"},
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        }

        result = cache_store.get(key)
        assert result["status"] == "expired"
        assert result["data"] is None

    def test_job_entries_have_ttl(self, cache_store):
        """Verify job entries are saved with TTL."""
        job_id = str(uuid.uuid4())
        job_key = f"job:{job_id}"

        # Jobs should have 1 hour TTL
        cache_store.save(job_key, {"status": "queued"}, ttl=3600)

        entry = cache_store._store[job_key]
        assert entry.get("expires_at") is not None


# ---------------------------------------------------------------------------
# Cache Isolation Tests
# ---------------------------------------------------------------------------

class TestCacheIsolation:
    """Test cache isolation between users and jobs."""

    def test_user_bundles_are_isolated(self, cache_store):
        """Verify different users have isolated bundles."""
        # User 1 bundle
        cache_store.save(
            "repos_bundle_context_user1",
            [{"name": "user1-repo"}],
        )

        # User 2 bundle
        cache_store.save(
            "repos_bundle_context_user2",
            [{"name": "user2-repo"}],
        )

        result1 = cache_store.get("repos_bundle_context_user1")
        result2 = cache_store.get("repos_bundle_context_user2")

        assert result1["data"][0]["name"] == "user1-repo"
        assert result2["data"][0]["name"] == "user2-repo"

    def test_jobs_are_isolated(self, cache_store):
        """Verify different jobs have isolated status."""
        job1_id = str(uuid.uuid4())
        job2_id = str(uuid.uuid4())

        cache_store.save(f"job:{job1_id}", {"status": "completed"})
        cache_store.save(f"job:{job2_id}", {"status": "syncing"})

        result1 = cache_store.get(f"job:{job1_id}")
        result2 = cache_store.get(f"job:{job2_id}")

        assert result1["data"]["status"] == "completed"
        assert result2["data"]["status"] == "syncing"

    def test_repo_caches_are_isolated_per_user(self, cache_store):
        """Verify same repo name for different users is isolated."""
        repo_name = "common-repo"

        cache_store.save(
            f"repo_level_bundle_user1_{repo_name}",
            {"name": repo_name, "owner": "user1"},
        )
        cache_store.save(
            f"repo_level_bundle_user2_{repo_name}",
            {"name": repo_name, "owner": "user2"},
        )

        result1 = cache_store.get(f"repo_level_bundle_user1_{repo_name}")
        result2 = cache_store.get(f"repo_level_bundle_user2_{repo_name}")

        assert result1["data"]["owner"] == "user1"
        assert result2["data"]["owner"] == "user2"


# ---------------------------------------------------------------------------
# Cache Integrity Tests
# ---------------------------------------------------------------------------

class TestCacheIntegrity:
    """Test cache data integrity."""

    def test_complex_data_structures_preserved(self, cache_store):
        """Verify complex nested data is preserved."""
        complex_data = {
            "name": "test-repo",
            "metadata": {
                "languages": {"Python": 5000, "JavaScript": 1000},
                "topics": ["api", "backend", "microservices"],
            },
            "files": [
                {"path": "src/main.py", "size": 1024},
                {"path": "tests/test_main.py", "size": 512},
            ],
            "nested": {
                "level1": {
                    "level2": {
                        "level3": "deep_value"
                    }
                }
            },
        }

        cache_store.save("complex_key", complex_data)
        result = cache_store.get("complex_key")

        assert result["data"]["metadata"]["languages"]["Python"] == 5000
        assert "microservices" in result["data"]["metadata"]["topics"]
        assert result["data"]["nested"]["level1"]["level2"]["level3"] == "deep_value"

    def test_unicode_content_preserved(self, cache_store):
        """Verify unicode content is preserved."""
        unicode_data = {
            "name": "i18n-repo",
            "readme": "# 国际化文档\n\nПривет мир! 🌍 مرحبا",
            "description": "Émojis: 🚀 💻 🔥",
        }

        cache_store.save("unicode_key", unicode_data)
        result = cache_store.get("unicode_key")

        assert "国际化" in result["data"]["readme"]
        assert "Привет" in result["data"]["readme"]
        assert "🌍" in result["data"]["readme"]

    def test_large_bundle_preserved(self, cache_store):
        """Verify large bundles are preserved correctly."""
        large_bundle = [
            {
                "name": f"repo-{i}",
                "readme": f"# Repo {i}\n\n" + "Content. " * 100,
                "fingerprint": f"fp_{i}",
            }
            for i in range(50)
        ]

        cache_store.save("large_bundle_key", large_bundle)
        result = cache_store.get("large_bundle_key")

        assert len(result["data"]) == 50
        assert result["data"][0]["name"] == "repo-0"
        assert result["data"][49]["name"] == "repo-49"


# ---------------------------------------------------------------------------
# Concurrent Access Tests
# ---------------------------------------------------------------------------

class TestConcurrentAccess:
    """Test cache behavior under concurrent access."""

    def test_multiple_reads_dont_corrupt_data(self, cache_store):
        """Verify multiple reads don't corrupt data."""
        cache_store.save("shared_key", {"counter": 0, "data": "original"})

        # Multiple reads
        results = [cache_store.get("shared_key") for _ in range(10)]

        # All should return same data
        assert all(r["status"] == "valid" for r in results)
        assert all(r["data"]["data"] == "original" for r in results)

    def test_last_write_wins(self, cache_store):
        """Verify last write wins in concurrent scenario."""
        key = "contested_key"

        # Simulate concurrent writes
        for i in range(5):
            cache_store.save(key, {"version": i})

        result = cache_store.get(key)
        assert result["data"]["version"] == 4  # Last write

    def test_delete_then_read_returns_missing(self, cache_store):
        """Verify delete followed by read returns missing."""
        key = "delete_test"

        cache_store.save(key, {"data": "exists"})
        cache_store.delete(key)
        result = cache_store.get(key)

        assert result["status"] == "missing"
