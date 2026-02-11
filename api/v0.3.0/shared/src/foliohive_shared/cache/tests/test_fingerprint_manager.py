"""
Unit tests for FingerprintManager.

Tests fingerprint generation for:
- Individual repository metadata
- Repository content bundles
- Bundle collections
"""
import pytest
import json
import hashlib
from foliohive_shared.cache.fingerprint_manager import FingerprintManager


class TestFingerprintManager:
    """Test suite for FingerprintManager functionality."""

    def test_generate_metadata_fingerprint(self, sample_repo_metadata):
        """Test generating fingerprint from normalized GitHub metadata."""
        fingerprint = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        assert len(fingerprint) == 32
        assert all(c in '0123456789abcdef' for c in fingerprint)
        
    def test_generate_metadata_fingerprint_consistency(self, sample_repo_metadata):
        fingerprint1 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        fingerprint2 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        assert fingerprint1 == fingerprint2
        
    def test_generate_metadata_fingerprint_changes_with_update(self, sample_repo_metadata):
        fingerprint1 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        modified_metadata = sample_repo_metadata.copy()
        modified_metadata['github_updated_at'] = '2026-01-02T12:00:00+00:00'
        fingerprint2 = FingerprintManager.generate_metadata_fingerprint(modified_metadata)
        assert fingerprint1 != fingerprint2
        
    def test_generate_metadata_fingerprint_ignores_irrelevant_fields(self, sample_repo_metadata):
        fingerprint1 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        modified_metadata = sample_repo_metadata.copy()
        modified_metadata['topics'] = ['extra', 'topics']
        modified_metadata['description'] = 'Different description'
        fingerprint2 = FingerprintManager.generate_metadata_fingerprint(modified_metadata)
        assert fingerprint1 == fingerprint2
        
    def test_generate_metadata_fingerprint_with_missing_fields(self):
        minimal_metadata = {
            'repo_name': 'empty',
            'github_updated_at': '2026-01-01T00:00:00+00:00',
        }
        fingerprint = FingerprintManager.generate_metadata_fingerprint(minimal_metadata)
        assert len(fingerprint) == 32
        
    def test_generate_content_fingerprint(self, sample_repos_bundle):
        fingerprint = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        assert len(fingerprint) == 32
        assert all(c in '0123456789abcdef' for c in fingerprint)
        
    def test_generate_content_fingerprint_consistency(self, sample_repos_bundle):
        fingerprint1 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        fingerprint2 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        assert fingerprint1 == fingerprint2
        
    def test_generate_content_fingerprint_changes_with_content(self, sample_repos_bundle):
        fingerprint1 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        modified_bundle = sample_repos_bundle.copy()
        modified_bundle[0] = {**modified_bundle[0], "fingerprint": "fp_new"}
        fingerprint2 = FingerprintManager.generate_content_fingerprint(modified_bundle)
        assert fingerprint1 != fingerprint2
        
    def test_generate_content_fingerprint_order_independent(self, sample_repos_bundle):
        fingerprint1 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        reversed_bundle = list(reversed(sample_repos_bundle))
        fingerprint2 = FingerprintManager.generate_content_fingerprint(reversed_bundle)
        assert fingerprint1 == fingerprint2

    def test_generate_content_fingerprint_empty_bundle(self):
        fingerprint = FingerprintManager.generate_content_fingerprint([])
        assert len(fingerprint) == 32
        
    def test_generate_bundle_fingerprint(self):
        repo_fingerprints = [
            'abc123def456',
            '789ghi012jkl',
            'mno345pqr678'
        ]
        fingerprint = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints)
        assert len(fingerprint) == 32
        assert all(c in '0123456789abcdef' for c in fingerprint)
        
    def test_generate_bundle_fingerprint_consistency(self):
        """Test that same fingerprints produce same bundle fingerprint."""
        repo_fingerprints = ['abc123', 'def456', 'ghi789']
        
        fingerprint1 = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints)
        fingerprint2 = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints)
        
        assert fingerprint1 == fingerprint2
        
    def test_generate_bundle_fingerprint_order_independent(self):
        """Test that fingerprint order doesn't affect result (sorted internally)."""
        repo_fingerprints = ['abc123', 'def456', 'ghi789']
        
        fingerprint1 = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints)
        fingerprint2 = FingerprintManager.generate_bundle_fingerprint(['ghi789', 'abc123', 'def456'])
        
        # Should be same due to internal sorting
        assert fingerprint1 == fingerprint2
        
    def test_generate_bundle_fingerprint_empty_list(self):
        """Test bundle fingerprint with empty list."""
        fingerprint = FingerprintManager.generate_bundle_fingerprint([])
        
        assert len(fingerprint) == 32
        
    def test_generate_bundle_fingerprint_changes_with_addition(self):
        """Test that adding a repo changes bundle fingerprint."""
        repo_fingerprints1 = ['abc123', 'def456']
        repo_fingerprints2 = ['abc123', 'def456', 'ghi789']
        
        fingerprint1 = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints1)
        fingerprint2 = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints2)
        
        assert fingerprint1 != fingerprint2


@pytest.mark.parametrize("metadata", [
    {'repo_name': 'a', 'github_updated_at': '2025-01-01T00:00:00+00:00'},
    {'repo_name': 'b', 'github_pushed_at': '2025-01-02T00:00:00+00:00', 'primary_language': 'Go'},
])
def test_generate_metadata_fingerprint_parametrized(metadata):
    fingerprint = FingerprintManager.generate_metadata_fingerprint(metadata)
    assert len(fingerprint) == 32
    assert all(c in '0123456789abcdef' for c in fingerprint)


def test_generate_user_profile_fingerprint_consistency():
    profile = {
        "id": 1,
        "updated_at": "2026-02-01T00:00:00Z",
        "public_repos": 8,
        "followers": 100,
        "following": 0,
        "name": "The Octocat",
        "company": "GitHub",
        "location": "San Francisco",
    }
    fp1 = FingerprintManager.generate_user_profile_fingerprint(profile)
    fp2 = FingerprintManager.generate_user_profile_fingerprint(profile)
    assert fp1 == fp2


def test_generate_user_profile_fingerprint_changes_on_update():
    profile = {
        "github_id": 1,
        "github_updated_at": "2026-02-01T00:00:00+00:00",
        "public_repos": 8,
        "followers": 100,
        "following": 0,
        "name": "The Octocat",
        "company": "GitHub",
        "location": "San Francisco",
    }
    fp1 = FingerprintManager.generate_user_profile_fingerprint(profile)
    changed = {**profile, "followers": 101}
    fp2 = FingerprintManager.generate_user_profile_fingerprint(changed)
    assert fp1 != fp2


def test_generate_user_profile_fingerprint_ignores_untracked_fields():
    profile = {
        "id": 1,
        "updated_at": "2026-02-01T00:00:00Z",
        "public_repos": 8,
        "followers": 100,
        "following": 0,
        "name": "The Octocat",
        "company": "GitHub",
        "location": "San Francisco",
    }
    fp1 = FingerprintManager.generate_user_profile_fingerprint(profile)
    changed = {**profile, "bio": "Hello", "avatar_url": "https://example.com"}
    fp2 = FingerprintManager.generate_user_profile_fingerprint(changed)
    assert fp1 == fp2
