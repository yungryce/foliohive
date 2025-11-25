"""
Shared fixtures for portfolio shared module tests.
"""
import pytest
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any, List


@pytest.fixture
def mock_azure_blob_client():
    """Mock Azure Blob Storage client."""
    with patch('azure.storage.blob.BlobServiceClient') as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        
        # Mock container client
        mock_container = MagicMock()
        mock_instance.get_container_client.return_value = mock_container
        
        # Mock blob client
        mock_blob = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        
        yield mock_instance


@pytest.fixture
def mock_azure_credential():
    """Mock Azure DefaultAzureCredential."""
    with patch('azure.identity.DefaultAzureCredential') as mock_cred:
        mock_instance = MagicMock()
        mock_cred.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def sample_repo_metadata() -> Dict[str, Any]:
    """Sample repository metadata for testing."""
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
        'topics': ['python', 'testing']
    }


@pytest.fixture
def sample_repos_bundle() -> List[Dict[str, Any]]:
    """Sample repository bundle for testing."""
    return [
        {
            'name': 'repo-1',
            'has_documentation': True,
            'last_updated': '2025-01-01T12:00:00Z',
            'readme': '# Repo 1\n\nThis is a test repository.',
            'skills_index': 'Python, FastAPI, PostgreSQL',
            'architecture': 'Microservices architecture using Docker'
        },
        {
            'name': 'repo-2',
            'has_documentation': True,
            'last_updated': '2025-01-02T12:00:00Z',
            'readme': '# Repo 2\n\nAnother test repository.',
            'skills_index': 'JavaScript, React, Node.js',
            'architecture': 'Frontend SPA with REST API backend'
        },
        {
            'name': 'repo-3',
            'has_documentation': False,
            'last_updated': '2025-01-03T12:00:00Z',
            'readme': '',
            'skills_index': '',
            'architecture': ''
        }
    ]


@pytest.fixture
def sample_file_extensions() -> Dict[str, int]:
    """Sample file extensions for type analysis."""
    return {
        '.py': 25,
        '.js': 10,
        '.json': 5,
        '.md': 3,
        '.yml': 2,
        '.txt': 1
    }


@pytest.fixture
def mock_github_response_success():
    """Mock successful GitHub API response."""
    return {
        'id': 123456789,
        'name': 'test-repo',
        'full_name': 'testuser/test-repo',
        'description': 'A test repository',
        'stargazers_count': 10,
        'language': 'Python'
    }


@pytest.fixture
def mock_github_response_file():
    """Mock GitHub API file content response."""
    import base64
    content = "# Test README\n\nThis is a test file."
    encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    return {
        'name': 'README.md',
        'path': 'README.md',
        'content': encoded,
        'encoding': 'base64'
    }


@pytest.fixture
def temp_linguist_file(tmp_path):
    """Create a temporary linguist languages.yml file for testing."""
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
    """Mock environment variables for testing."""
    env_vars = {
        'GITHUB_TOKEN': 'test_github_token_123',
        'GITHUB_USERNAME': 'env-user',
        'BLOB_SERVICE_URI': 'https://teststorage.blob.core.windows.net',
        'AzureWebJobsStorage': 'DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=test_key==',
        'GROQ_API_KEY': 'test_groq_api_key'
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


@pytest.fixture
def current_time():
    """Fixed timestamp for testing."""
    return datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
