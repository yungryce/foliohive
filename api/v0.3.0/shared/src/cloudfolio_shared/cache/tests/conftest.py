"""Fixtures for cache module tests (normalized schema).

These fixtures align with RepoGitHubMetadataRow fields in table_manager.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest


@pytest.fixture
def sample_repo_metadata() -> Dict[str, Any]:
    """Normalized GitHub metadata snapshot used for fingerprinting."""
    return {
        "username": "testuser",
        "repo_name": "test-repo",
        "fingerprint": "fp_abc123",
        "description": "A test repository",
        "github_updated_at": "2026-01-01T12:00:00+00:00",
        "github_pushed_at": "2026-01-01T11:00:00+00:00",
        "github_created_at": "2025-01-01T00:00:00+00:00",
        "primary_language": "Python",
        "stars_count": 10,
        "forks_count": 5,
        "topics": ["python", "testing"],
        "is_fork": False,
        "is_archived": False,
        "license_name": "MIT",
    }


@pytest.fixture
def sample_repos_bundle() -> List[Dict[str, Any]]:
    """Sample cached bundle entries with repo-level fingerprints."""
    return [
        {
            "repo_name": "api",
            "fingerprint": "fp_api",
            "github_updated_at": "2026-01-01T12:00:00+00:00",
        },
        {
            "repo_name": "web",
            "fingerprint": "fp_web",
            "github_updated_at": "2026-01-01T12:05:00+00:00",
        },
        {
            "repo_name": "worker",
            "fingerprint": "fp_worker",
            "github_updated_at": "2026-01-02T09:00:00+00:00",
        },
    ]
