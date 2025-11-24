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
from apps.shared.cache.fingerprint_manager import FingerprintManager


class TestFingerprintManager:
    """Test suite for FingerprintManager functionality."""

    def test_generate_metadata_fingerprint(self, sample_repo_metadata):
        """Test generating fingerprint from repository metadata."""
        fingerprint = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        
        # Verify fingerprint is a valid MD5 hash (32 hex characters)
        assert len(fingerprint) == 32
        assert all(c in '0123456789abcdef' for c in fingerprint)
        
    def test_generate_metadata_fingerprint_consistency(self, sample_repo_metadata):
        """Test that same metadata produces same fingerprint."""
        fingerprint1 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        fingerprint2 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        
        assert fingerprint1 == fingerprint2
        
    def test_generate_metadata_fingerprint_changes_with_update(self, sample_repo_metadata):
        """Test that fingerprint changes when metadata changes."""
        fingerprint1 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        
        # Modify metadata
        modified_metadata = sample_repo_metadata.copy()
        modified_metadata['updated_at'] = '2025-01-02T12:00:00Z'
        fingerprint2 = FingerprintManager.generate_metadata_fingerprint(modified_metadata)
        
        assert fingerprint1 != fingerprint2
        
    def test_generate_metadata_fingerprint_ignores_irrelevant_fields(self, sample_repo_metadata):
        """Test that fingerprint ignores fields not used in fingerprinting."""
        fingerprint1 = FingerprintManager.generate_metadata_fingerprint(sample_repo_metadata)
        
        # Add extra field that shouldn't affect fingerprint
        modified_metadata = sample_repo_metadata.copy()
        modified_metadata['stargazers_count'] = 999
        modified_metadata['description'] = 'Different description'
        fingerprint2 = FingerprintManager.generate_metadata_fingerprint(modified_metadata)
        
        # Fingerprint should be same since these fields aren't used
        assert fingerprint1 == fingerprint2
        
    def test_generate_metadata_fingerprint_with_missing_fields(self):
        """Test fingerprinting with missing optional fields."""
        minimal_metadata = {
            'id': 123,
            'updated_at': '2025-01-01T12:00:00Z'
        }
        
        fingerprint = FingerprintManager.generate_metadata_fingerprint(minimal_metadata)
        assert len(fingerprint) == 32
        
    def test_generate_content_fingerprint(self, sample_repos_bundle):
        """Test generating fingerprint from repository bundle content."""
        fingerprint = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        
        assert len(fingerprint) == 32
        assert all(c in '0123456789abcdef' for c in fingerprint)
        
    def test_generate_content_fingerprint_consistency(self, sample_repos_bundle):
        """Test that same bundle produces same fingerprint."""
        fingerprint1 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        fingerprint2 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        
        assert fingerprint1 == fingerprint2
        
    def test_generate_content_fingerprint_ignores_repos_without_docs(self, sample_repos_bundle):
        """Test that repos without documentation are skipped."""
        # Original bundle has 3 repos, but only 2 have documentation
        fingerprint1 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        
        # Remove the repo without docs
        bundle_with_docs_only = [r for r in sample_repos_bundle if r.get('has_documentation')]
        fingerprint2 = FingerprintManager.generate_content_fingerprint(bundle_with_docs_only)
        
        # Should be the same since repo-3 had no docs
        assert fingerprint1 == fingerprint2
        
    def test_generate_content_fingerprint_changes_with_content(self, sample_repos_bundle):
        """Test that fingerprint changes when content changes."""
        fingerprint1 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        
        # Modify content
        modified_bundle = sample_repos_bundle.copy()
        modified_bundle[0] = modified_bundle[0].copy()
        modified_bundle[0]['readme'] = '# Modified README\n\nThis changed.'
        fingerprint2 = FingerprintManager.generate_content_fingerprint(modified_bundle)
        
        assert fingerprint1 != fingerprint2
        
    def test_generate_content_fingerprint_order_independent(self, sample_repos_bundle):
        """Test that repo order doesn't affect fingerprint (sorted internally)."""
        fingerprint1 = FingerprintManager.generate_content_fingerprint(sample_repos_bundle)
        
        # Reverse order
        reversed_bundle = list(reversed(sample_repos_bundle))
        fingerprint2 = FingerprintManager.generate_content_fingerprint(reversed_bundle)
        
        # Should be same due to internal sorting by name
        assert fingerprint1 == fingerprint2
        
    def test_generate_content_fingerprint_empty_bundle(self):
        """Test fingerprinting empty bundle."""
        fingerprint = FingerprintManager.generate_content_fingerprint([])
        
        assert len(fingerprint) == 32
        
    def test_generate_bundle_fingerprint(self):
        """Test generating fingerprint from list of repo fingerprints."""
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


@pytest.mark.parametrize("metadata,expected_fields", [
    (
        {'id': 1, 'updated_at': '2025-01-01', 'pushed_at': '2025-01-01', 'size': 100},
        ['id', 'updated_at', 'pushed_at', 'size']
    ),
    (
        {'id': 2, 'default_branch': 'main', 'language': 'Python'},
        ['id', 'default_branch', 'language']
    ),
])
def test_generate_metadata_fingerprint_parametrized(metadata, expected_fields):
    """Parametrized test for various metadata combinations."""
    fingerprint = FingerprintManager.generate_metadata_fingerprint(metadata)
    
    # Should always produce valid fingerprint
    assert len(fingerprint) == 32
    assert all(c in '0123456789abcdef' for c in fingerprint)
