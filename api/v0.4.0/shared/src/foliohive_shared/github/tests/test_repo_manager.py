"""Initial Phase 1 tests for GitHubRepoManager discovery behavior."""

from unittest.mock import Mock

import pytest

from foliohive_shared.github.github_repo_manager import GitHubRepoManager


class TestRepoManagerFileDiscovery:
    """Test file discovery and path indexing."""

    def test_discover_standard_config_files(self):
        api = Mock()
        api.make_request.return_value = {"type": "file", "content": "Zmxhc2s=", "encoding": "base64"}
        api.decode_file_content.return_value = "flask\nrequests"
        manager = GitHubRepoManager(api=api, username="alice")
        manager.get_repo_path_index = Mock(return_value=["requirements.txt", "package.json", "README.md"])
        result = manager.get_standard_config_files(username="alice", repo="sample", limit=30)

        assert "requirements.txt" in result
        assert "package.json" in result
        assert "README.md" not in result

    def test_filters_irrelevant_paths(self):
        api = Mock()
        manager = GitHubRepoManager(api=api, username="alice")

        selected = manager._discover_file_target_paths_by_level(
            path_index={
                "README.md",
                "src/README.md",
                "src/package.json",
                "src/random.txt",
                "docs/notes.md",
            },
            file_candidates=["package.json", "requirements.txt"],
            readme_candidates=["README.md"],
            config_cap=5,
            readme_cap=4,
        )

        assert "src/package.json" in selected
        assert "src/random.txt" not in selected
