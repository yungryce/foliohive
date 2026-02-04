"""Pytest fixtures for GitHub API tests (normalized schema)."""
from __future__ import annotations

import base64
import os
from typing import Any, Dict

import pytest


@pytest.fixture(scope="session", autouse=True)
def _ensure_github_env() -> None:
    os.environ.setdefault("GITHUB_TOKEN", "test_github_token_123")
    os.environ.setdefault("GITHUB_USERNAME", "testuser")
    yield


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for testing (overrides)."""
    env_vars = {
        'GITHUB_TOKEN': 'test_github_token_123',
        'GITHUB_USERNAME': 'env-user',
        'BLOB_SERVICE_URI': 'https://teststorage.blob.core.windows.net',
        'AzureWebJobsStorage': 'DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=test_key==',
        'GROQ_API_KEY': 'test_groq_api_key',
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


@pytest.fixture
def mock_github_response_file() -> Dict[str, Any]:
    content = "# Test README\n\nThis is a test file."
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    return {
        "name": "README.md",
        "path": "README.md",
        "content": encoded,
        "encoding": "base64",
    }
