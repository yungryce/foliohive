"""Tests for RepoCacheRetrieval class."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from ..repo_cache_retrieval import RepoCacheRetrieval, repo_cache_retrieval


@pytest.fixture
def retrieval():
    """Create a RepoCacheRetrieval instance for testing."""
    return RepoCacheRetrieval()


@pytest.fixture
def mock_cache_manager():
    """Mock cache_manager for testing."""
    with patch("foliohive_shared.cache.repo_cache_retrieval.cache_manager") as mock:
        yield mock


class TestKeyGeneration:
    """Test cache key generation methods."""
    
    def test_generate_primary_readme_key(self, retrieval):
        """Test primary readme key generation."""
        key = retrieval.generate_primary_readme_key("testuser", "testrepo")
        assert "testuser" in key
        assert "testrepo" in key
        assert "readme" in key.lower()
        assert "PRIMARY" in key
    
    def test_generate_readme_key(self, retrieval):
        """Test readme file key generation with path."""
        key = retrieval.generate_readme_key("testuser", "testrepo", "docs/README.md")
        assert "testuser" in key
        assert "testrepo" in key
        assert "readme" in key.lower()
        assert "docs/README.md" in key or "docs" in key
    
    def test_generate_config_key(self, retrieval):
        """Test config file key generation."""
        key = retrieval.generate_config_key("testuser", "testrepo", "package.json")
        assert "testuser" in key
        assert "testrepo" in key
        assert "config" in key.lower()
        assert "package.json" in key
    
    def test_generate_config_key_with_path(self, retrieval):
        """Test config file key generation with nested path."""
        key = retrieval.generate_config_key("testuser", "testrepo", "src/Dockerfile")
        assert "testuser" in key
        assert "testrepo" in key
        assert "config" in key.lower()
        assert "src/Dockerfile" in key or "Dockerfile" in key
    
    def test_keys_are_unique_per_file_type(self, retrieval):
        """Test that different file types generate different keys."""
        primary_key = retrieval.generate_primary_readme_key("user", "repo")
        readme_key = retrieval.generate_readme_key("user", "repo", "docs/README.md")
        config_key = retrieval.generate_config_key("user", "repo", "package.json")
        
        assert primary_key != readme_key
        assert primary_key != config_key
        assert readme_key != config_key


class TestGetPrimaryReadme:
    """Test primary readme retrieval."""
    
    def test_get_primary_readme_success(self, retrieval, mock_cache_manager):
        """Test successful primary readme retrieval."""
        mock_cache_manager.get.return_value = {
            "status": "valid",
            "data": "# Test README\n\nContent here",
        }
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        
        content = retrieval.get_primary_readme("testuser", "testrepo")
        
        assert content == "# Test README\n\nContent here"
        assert mock_cache_manager.get.called
    
    def test_get_primary_readme_not_cached(self, retrieval, mock_cache_manager):
        """Test primary readme retrieval when not cached."""
        mock_cache_manager.get.return_value = {"status": "not_found"}
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        
        content = retrieval.get_primary_readme("testuser", "testrepo")
        
        assert content is None
    
    def test_get_primary_readme_invalid_status(self, retrieval, mock_cache_manager):
        """Test primary readme retrieval with invalid status."""
        mock_cache_manager.get.return_value = {"status": "expired", "data": "old content"}
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        
        content = retrieval.get_primary_readme("testuser", "testrepo")
        
        assert content is None


class TestGetReadmeFiles:
    """Test readme files retrieval."""
    
    def test_get_readme_files_success(self, retrieval, mock_cache_manager):
        """Test successful retrieval of multiple readme files."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.side_effect = [
            {"status": "valid", "data": "Content 1"},
            {"status": "valid", "data": "Content 2"},
        ]
        
        paths = ["docs/README.md", "examples/README.md"]
        results = retrieval.get_readme_files("testuser", "testrepo", paths)
        
        assert len(results) == 2
        assert results["docs/README.md"] == "Content 1"
        assert results["examples/README.md"] == "Content 2"
    
    def test_get_readme_files_partial_success(self, retrieval, mock_cache_manager):
        """Test retrieval with some files missing."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.side_effect = [
            {"status": "valid", "data": "Content 1"},
            {"status": "not_found"},
        ]
        
        paths = ["docs/README.md", "missing/README.md"]
        results = retrieval.get_readme_files("testuser", "testrepo", paths)
        
        assert len(results) == 1
        assert "docs/README.md" in results
        assert "missing/README.md" not in results
    
    def test_get_readme_files_empty_paths(self, retrieval, mock_cache_manager):
        """Test retrieval with empty path list."""
        results = retrieval.get_readme_files("testuser", "testrepo", [])
        
        assert results == {}
        assert not mock_cache_manager.get.called


class TestGetConfigFiles:
    """Test config files retrieval."""
    
    def test_get_config_files_success(self, retrieval, mock_cache_manager):
        """Test successful retrieval of config files."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.side_effect = [
            {"status": "valid", "data": '{"name": "test"}'},
            {"status": "valid", "data": "FROM python:3.9"},
        ]
        
        paths = ["package.json", "Dockerfile"]
        results = retrieval.get_config_files("testuser", "testrepo", paths)
        
        assert len(results) == 2
        assert results[0]["filename"] == "package.json"
        assert results[0]["path"] == "package.json"
        assert results[0]["content"] == '{"name": "test"}'
        assert results[1]["filename"] == "Dockerfile"
        assert results[1]["content"] == "FROM python:3.9"
    
    def test_get_config_files_with_nested_paths(self, retrieval, mock_cache_manager):
        """Test config file retrieval with nested directory paths."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.return_value = {
            "status": "valid",
            "data": '{"version": "1.0"}',
        }
        
        paths = ["src/config/settings.json"]
        results = retrieval.get_config_files("testuser", "testrepo", paths)
        
        assert len(results) == 1
        assert results[0]["filename"] == "settings.json"
        assert results[0]["path"] == "src/config/settings.json"
    
    def test_get_config_files_with_max_limit(self, retrieval, mock_cache_manager):
        """Test config file retrieval respects max_files limit."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.return_value = {"status": "valid", "data": "content"}
        
        paths = ["file1.json", "file2.json", "file3.json", "file4.json"]
        results = retrieval.get_config_files("testuser", "testrepo", paths, max_files=2)
        
        assert len(results) == 2
        assert mock_cache_manager.get.call_count == 2
    
    def test_get_config_files_skips_empty_content(self, retrieval, mock_cache_manager):
        """Test that empty content is skipped."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.side_effect = [
            {"status": "valid", "data": "content"},
            {"status": "valid", "data": ""},
            {"status": "valid", "data": None},
        ]
        
        paths = ["file1.json", "file2.json", "file3.json"]
        results = retrieval.get_config_files("testuser", "testrepo", paths)
        
        assert len(results) == 1
        assert results[0]["filename"] == "file1.json"


class TestGetRepoFiles:
    """Test combined repo files retrieval."""
    
    def test_get_repo_files_primary_readme_only(self, retrieval, mock_cache_manager):
        """Test retrieval with no discovered paths."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.return_value = {
            "status": "valid",
            "data": "# Primary README",
        }
        
        result = retrieval.get_repo_files("testuser", "testrepo", discovered_paths=None)
        
        assert result["repo_name"] == "testrepo"
        assert result["readme_content"] == "# Primary README"
        assert result["readme_files"] == {}
        assert result["config_files"] == []
    
    def test_get_repo_files_with_discovered_paths(self, retrieval, mock_cache_manager):
        """Test retrieval with discovered paths."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.side_effect = [
            {"status": "valid", "data": "# Primary README"},
            {"status": "valid", "data": "# Docs README"},
            {"status": "valid", "data": '{"name": "test"}'},
        ]
        
        discovered_paths = ["docs/README.md", "package.json"]
        readme_candidates = ["README.md", "readme.md"]
        
        result = retrieval.get_repo_files(
            "testuser",
            "testrepo",
            discovered_paths=discovered_paths,
            readme_candidates=readme_candidates,
            max_config_files=5,
        )
        
        assert result["repo_name"] == "testrepo"
        assert result["readme_content"] == "# Primary README"
        assert len(result["readme_files"]) == 1
        assert len(result["config_files"]) == 1
    
    def test_get_repo_files_separates_readme_from_config(self, retrieval, mock_cache_manager):
        """Test that readme and config files are properly separated."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.side_effect = [
            {"status": "valid", "data": "# Primary"},
            {"status": "valid", "data": "# Sub README"},
            {"status": "valid", "data": '{"config": true}'},
            {"status": "valid", "data": "FROM alpine"},
        ]
        
        discovered_paths = [
            "subdir/README.md",
            "package.json",
            "Dockerfile",
        ]
        readme_candidates = ["README.md"]
        
        result = retrieval.get_repo_files(
            "testuser",
            "testrepo",
            discovered_paths=discovered_paths,
            readme_candidates=readme_candidates,
        )
        
        assert len(result["readme_files"]) == 1
        assert "subdir/README.md" in result["readme_files"]
        assert len(result["config_files"]) == 2


class TestGetMultipleReposFiles:
    """Test multiple repos file retrieval."""
    
    def test_get_multiple_repos_files(self, retrieval, mock_cache_manager):
        """Test retrieval for multiple repositories."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.side_effect = [
            {"status": "valid", "data": "# Repo1 README"},
            {"status": "valid", "data": '{"name": "repo1"}'},
            {"status": "valid", "data": "# Repo2 README"},
            {"status": "valid", "data": "FROM python"},
        ]
        
        repo_discovery_map = {
            "repo1": ["package.json"],
            "repo2": ["Dockerfile"],
        }
        
        results = retrieval.get_multiple_repos_files(
            "testuser",
            repo_discovery_map,
            readme_candidates=["README.md"],
        )
        
        assert len(results) == 2
        assert "repo1" in results
        assert "repo2" in results
        assert results["repo1"]["readme_content"] == "# Repo1 README"
        assert results["repo2"]["readme_content"] == "# Repo2 README"


class TestSaveMethods:
    """Test file save methods."""
    
    def test_save_primary_readme(self, retrieval, mock_cache_manager):
        """Test saving primary readme."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.save.return_value = True
        
        result = retrieval.save_primary_readme(
            "testuser",
            "testrepo",
            "# README content",
            ttl=3600,
        )
        
        assert result is True
        mock_cache_manager.save.assert_called_once_with(
            "test:key",
            "# README content",
            ttl=3600,
        )
    
    def test_save_readme_file(self, retrieval, mock_cache_manager):
        """Test saving readme file with path."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.save.return_value = True
        
        result = retrieval.save_readme_file(
            "testuser",
            "testrepo",
            "docs/README.md",
            "# Docs README",
            ttl=None,
        )
        
        assert result is True
        mock_cache_manager.save.assert_called_once_with(
            "test:key",
            "# Docs README",
            ttl=None,
        )
    
    def test_save_config_file(self, retrieval, mock_cache_manager):
        """Test saving config file."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.save.return_value = True
        
        result = retrieval.save_config_file(
            "testuser",
            "testrepo",
            "package.json",
            '{"name": "test"}',
            ttl=7200,
        )
        
        assert result is True
        mock_cache_manager.save.assert_called_once_with(
            "test:key",
            '{"name": "test"}',
            ttl=7200,
        )


class TestSingletonInstance:
    """Test the global singleton instance."""
    
    def test_singleton_exists(self):
        """Test that the singleton instance is available."""
        assert repo_cache_retrieval is not None
        assert isinstance(repo_cache_retrieval, RepoCacheRetrieval)
    
    def test_singleton_has_cache_manager(self):
        """Test that singleton has cache_manager attribute."""
        assert hasattr(repo_cache_retrieval, "cache_manager")
        assert repo_cache_retrieval.cache_manager is not None


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_username(self, retrieval, mock_cache_manager):
        """Test handling of empty username."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.return_value = {"status": "valid", "data": "content"}
        
        # Should not raise exception
        content = retrieval.get_primary_readme("", "testrepo")
        assert mock_cache_manager.get.called
    
    def test_empty_repo(self, retrieval, mock_cache_manager):
        """Test handling of empty repo name."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.return_value = {"status": "valid", "data": "content"}
        
        # Should not raise exception
        content = retrieval.get_primary_readme("testuser", "")
        assert mock_cache_manager.get.called
    
    def test_special_characters_in_path(self, retrieval, mock_cache_manager):
        """Test handling of special characters in file paths."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.return_value = {"status": "valid", "data": "content"}
        
        # Should not raise exception
        key = retrieval.generate_config_key("user", "repo", "path/with-special_chars.json")
        assert key is not None
    
    def test_very_long_path(self, retrieval):
        """Test handling of very long file paths."""
        long_path = "/".join(["subdir"] * 20) + "/config.json"
        
        # Should not raise exception
        key = retrieval.generate_config_key("user", "repo", long_path)
        assert key is not None
    
    def test_get_config_files_with_no_valid_results(self, retrieval, mock_cache_manager):
        """Test config file retrieval when no files are valid."""
        mock_cache_manager.generate_cache_key.return_value = "test:key"
        mock_cache_manager.get.return_value = {"status": "not_found"}
        
        paths = ["file1.json", "file2.json"]
        results = retrieval.get_config_files("testuser", "testrepo", paths)
        
        assert results == []
