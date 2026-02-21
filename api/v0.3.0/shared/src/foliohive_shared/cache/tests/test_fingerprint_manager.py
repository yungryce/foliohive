"""Unit tests for FingerprintManager."""

import pytest
from foliohive_shared.cache.fingerprint_manager import FingerprintManager


class TestMetadataFingerprint:
    """Tests for generate_metadata_fingerprint."""

    def test_returns_md5_hex_string(self):
        fp = FingerprintManager.generate_metadata_fingerprint({"repo_name": "repo1", "github_updated_at": "2026-01-01T00:00:00Z"})
        assert len(fp) == 32
        assert all(c in "0123456789abcdef" for c in fp)

    def test_deterministic_for_same_input(self):
        meta = {"repo_name": "repo1", "github_updated_at": "2026-01-01T00:00:00Z", "stars_count": 10}
        assert FingerprintManager.generate_metadata_fingerprint(meta) == FingerprintManager.generate_metadata_fingerprint(meta)

    def test_changes_when_stars_change(self):
        base = {"repo_name": "repo1", "github_updated_at": "2026-01-01T00:00:00Z", "stars_count": 10}
        changed = {**base, "stars_count": 20}
        assert FingerprintManager.generate_metadata_fingerprint(base) != FingerprintManager.generate_metadata_fingerprint(changed)

    def test_stable_across_untracked_fields(self):
        base = {"repo_name": "r", "github_updated_at": "2026-01-01T00:00:00Z"}
        extra = {**base, "untracked_field": "value"}
        assert FingerprintManager.generate_metadata_fingerprint(base) == FingerprintManager.generate_metadata_fingerprint(extra)

    def test_handles_missing_optional_fields(self):
        fp = FingerprintManager.generate_metadata_fingerprint({"repo_name": "empty", "github_updated_at": "2026-01-01T00:00:00Z"})
        assert len(fp) == 32


@pytest.mark.parametrize("metadata", [
    {"repo_name": "a", "github_updated_at": "2025-01-01T00:00:00+00:00"},
    {"repo_name": "b", "github_pushed_at": "2025-01-02T00:00:00+00:00", "primary_language": "Go"},
])
def test_generate_metadata_fingerprint_parametrized(metadata):
    fp = FingerprintManager.generate_metadata_fingerprint(metadata)
    assert len(fp) == 32
    assert all(c in "0123456789abcdef" for c in fp)


class TestUserProfileFingerprint:
    """Tests for generate_user_profile_fingerprint."""

    _base = {
        "id": 1,
        "updated_at": "2026-02-01T00:00:00Z",
        "public_repos": 8,
        "followers": 100,
        "following": 0,
        "name": "The Octocat",
        "company": "GitHub",
        "location": "San Francisco",
    }

    def test_returns_md5_hex_string(self):
        fp = FingerprintManager.generate_user_profile_fingerprint(self._base)
        assert len(fp) == 32
        assert all(c in "0123456789abcdef" for c in fp)

    def test_deterministic_for_same_input(self):
        fp1 = FingerprintManager.generate_user_profile_fingerprint(self._base)
        fp2 = FingerprintManager.generate_user_profile_fingerprint(self._base)
        assert fp1 == fp2

    def test_changes_when_followers_change(self):
        changed = {**self._base, "followers": 101}
        assert (
            FingerprintManager.generate_user_profile_fingerprint(self._base)
            != FingerprintManager.generate_user_profile_fingerprint(changed)
        )

    def test_stable_across_untracked_fields(self):
        extra = {**self._base, "bio": "Hello", "avatar_url": "https://example.com"}
        assert (
            FingerprintManager.generate_user_profile_fingerprint(self._base)
            == FingerprintManager.generate_user_profile_fingerprint(extra)
        )
