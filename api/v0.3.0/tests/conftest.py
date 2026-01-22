"""Global pytest fixtures and environment setup for all Cloudfolio apps."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Azurite connection string for local testing
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)


@pytest.fixture(scope="session", autouse=True)
def configure_test_environment() -> None:
    os.environ.setdefault("AZURE_STORAGE_CONNECTION_STRING", AZURITE_CONNECTION_STRING)
    os.environ.setdefault("AZURE_STORAGE_ACCOUNT_NAME", "devstoreaccount1")
    os.environ.setdefault(
        "AZURE_STORAGE_ACCOUNT_KEY",
        "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==",
    )
    os.environ.setdefault("GITHUB_TOKEN", "test_github_token_123")
    os.environ.setdefault("GROQ_API_KEY", "test_groq_api_key")
    yield


@pytest.fixture(autouse=True)
def mock_default_credential():
    with patch('azure.identity.DefaultAzureCredential'):
        yield


@pytest.fixture
def mock_azure_blob_client():
    with patch('azure.storage.blob.BlobServiceClient') as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        mock_container = MagicMock()
        mock_instance.get_container_client.return_value = mock_container
        mock_blob = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        yield mock_instance


@pytest.fixture
def mock_azure_credential():
    with patch('azure.identity.DefaultAzureCredential') as mock_cred:
        mock_instance = MagicMock()
        mock_cred.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def sample_repo_metadata() -> Dict[str, Any]:
    return {
        'id': 123456789,
        'name': 'test-repo',
        'full_name': 'testuser/test-repo',
        'owner': {'login': 'testuser'},
        'description': 'A test repository',
        'updated_at': '2025-01-01T12:00:00Z',
        'pushed_at': '2025-01-01T11:00:00Z',
        'size': 1024,
        'default_branch': 'main',
        'language': 'Python',
        'stargazers_count': 10,
        'forks_count': 5,
        'topics': ['python', 'testing'],
    }


@pytest.fixture
def sample_repos_bundle() -> List[Dict[str, Any]]:
    return [
        {
            'name': 'repo-1',
            'has_documentation': True,
            'last_updated': '2025-01-01T12:00:00Z',
            'readme': '# Repo 1\n\nThis is a test repository.',
            'skills_index': 'Python, FastAPI, PostgreSQL',
            'architecture': 'Microservices architecture using Docker',
        },
        {
            'name': 'repo-2',
            'has_documentation': True,
            'last_updated': '2025-01-02T12:00:00Z',
            'readme': '# Repo 2\n\nAnother test repository.',
            'skills_index': 'JavaScript, React, Node.js',
            'architecture': 'Frontend SPA with REST API backend',
        },
        {
            'name': 'repo-3',
            'has_documentation': False,
            'last_updated': '2025-01-03T12:00:00Z',
            'readme': '',
            'skills_index': '',
            'architecture': '',
        },
    ]


@pytest.fixture
def sample_file_extensions() -> Dict[str, int]:
    return {
        '.py': 25,
        '.js': 10,
        '.json': 5,
        '.md': 3,
        '.yml': 2,
        '.txt': 1,
    }


@pytest.fixture
def mock_github_response_success():
    return {
        'id': 123456789,
        'name': 'test-repo',
        'full_name': 'testuser/test-repo',
        'description': 'A test repository',
        'stargazers_count': 10,
        'language': 'Python',
    }


@pytest.fixture
def mock_github_response_file():
    import base64

    content = "# Test README\n\nThis is a test file."
    encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    return {
        'name': 'README.md',
        'path': 'README.md',
        'content': encoded,
        'encoding': 'base64',
    }


@pytest.fixture
def temp_linguist_file(tmp_path: Path) -> str:
    linguist_content = """
Python:
  type: programming
  color: "#3572A5"
  extensions:
  - ".py"
  - ".pyw"

JavaScript:
  type: programming
  color: "#f1e05a"
  extensions:
  - ".js"
  - ".mjs"

JSON:
  type: data
  color: "#292929"
  extensions:
  - ".json"

Markdown:
  type: prose
  color: "#083fa1"
  extensions:
  - ".md"
  - ".markdown"

YAML:
  type: data
  color: "#cb171e"
  extensions:
  - ".yml"
  - ".yaml"

Text:
  type: prose
  extensions:
  - ".txt"
"""
    linguist_dir = tmp_path / "linguist"
    linguist_dir.mkdir()
    linguist_file = linguist_dir / "languages.yml"
    linguist_file.write_text(linguist_content)
    return str(linguist_file)


@pytest.fixture
def mock_env_vars(monkeypatch):
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
def current_time():
    return datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
