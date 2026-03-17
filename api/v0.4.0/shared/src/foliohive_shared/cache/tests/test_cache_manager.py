"""Unit tests for cache_manager.py aligned to current file-cache API."""

from unittest.mock import Mock

import pytest

from foliohive_shared.cache.cache_manager import CacheManager


class TestCacheKeyGeneration:
    def test_file_cache_key_generation(self):
        key = CacheManager.generate_cache_key(
            username="alice",
            repo="my/repo",
            file_type="config",
            filename="infra/main.tf",
        )
        assert key == "file_alice_my_repo_config_infra_main.tf"

    def test_generate_cache_key_missing_required_fields(self):
        with pytest.raises(ValueError):
            CacheManager.generate_cache_key(username="alice", repo="repo", file_type="readme")


class TestGetRepoFiles:
    """Test get_repo_files() with extracted config payload behavior."""

    def test_returns_readme_and_extracted_configs(self):
        cache = CacheManager(use_cache=False)
        cache.get_primary_readme = Mock(return_value="# Root")
        cache.get_readme_files = Mock(return_value={"docs/README.md": "# Docs"})
        cache.get_config_files = Mock(
            return_value={
                "package.json": {"dependencies": {"react": "18"}},
                "pyproject.toml": {"project_dependencies": ["fastapi"]},
            }
        )

        result = cache.get_repo_files(
            username="alice",
            repo="sample",
            discovered_paths=["docs/README.md", "package.json", "pyproject.toml"],
            readme_candidates=["README.md"],
        )

        assert result["repo_name"] == "sample"
        assert result["readme_content"] == "# Root"
        assert "docs/README.md" in result["readme_files"]
        assert "package.json" in result["config_files"]
        assert "pyproject.toml" in result["config_files"]

    def test_skips_missing_extractions_gracefully(self):
        cache = CacheManager(use_cache=False)
        cache.get_primary_readme = Mock(return_value="# Root")
        cache.get_readme_files = Mock(return_value={})
        cache.get_config_files = Mock(return_value={"package.json": {"dependencies": {"react": "18"}}})

        result = cache.get_repo_files(
            username="alice",
            repo="sample",
            discovered_paths=["package.json", "pyproject.toml"],
            readme_candidates=["README.md"],
        )

        assert list(result["config_files"].keys()) == ["package.json"]

    def test_missing_readme_returns_empty_readme_section(self):
        cache = CacheManager(use_cache=False)
        cache.get_primary_readme = Mock(return_value=None)
        cache.get_readme_files = Mock(return_value={})
        cache.get_config_files = Mock(return_value={"package.json": {"dependencies": {"react": "18"}}})

        result = cache.get_repo_files(
            username="alice",
            repo="sample",
            discovered_paths=["package.json"],
            readme_candidates=["README.md"],
        )

        assert result["readme_content"] is None
        assert result["readme_files"] == {}
        assert "package.json" in result["config_files"]

    def test_respects_max_config_files_limit(self):
        cache = CacheManager(use_cache=False)
        cache.get_primary_readme = Mock(return_value="# Root")
        cache.get_readme_files = Mock(return_value={})
        cache.get_config_files = Mock(return_value={})

        discovered_paths = [
            "a/package.json",
            "b/pyproject.toml",
            "c/main.tf",
            "d/docker-compose.yml",
            "e/pom.xml",
        ]

        cache.get_repo_files(
            username="alice",
            repo="sample",
            discovered_paths=discovered_paths,
            readme_candidates=["README.md"],
            max_config_files=3,
        )

        cache.get_config_files.assert_called_once_with(
            "alice",
            "sample",
            ["a/package.json", "b/pyproject.toml", "c/main.tf"],
        )

    def test_extraction_failure_marked_in_result(self):
        cache = CacheManager(use_cache=False)
        cache.get_primary_readme = Mock(return_value="# Root")
        cache.get_readme_files = Mock(return_value={})
        cache.get_config_files = Mock(return_value={"package.json": {"error": "invalid_json"}})

        result = cache.get_repo_files(
            username="alice",
            repo="sample",
            discovered_paths=["package.json"],
            readme_candidates=["README.md"],
        )

        assert result["config_files"]["package.json"]["error"] == "invalid_json"
