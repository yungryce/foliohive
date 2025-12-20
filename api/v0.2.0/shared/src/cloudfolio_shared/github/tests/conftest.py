"""Pytest fixtures for GitHub API tests."""
import pytest


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for testing."""
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
