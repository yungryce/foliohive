"""Initial Phase 1 tests for GitHubRepoManager discovery behavior."""

from unittest.mock import Mock

import pytest

from foliohive_shared.github.github_repo_manager import GitHubRepoManager


class TestRepoManagerFileDiscovery:
    """Test file discovery and path indexing."""

    def test_discover_standard_config_files(self):
        api = Mock()
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
class TestRepoManagerExtractionIntegration:
    """Test extraction triggering during cache phase."""

    def test_triggers_extractor_for_discovered_config(self):
        api = Mock()
        manager = GitHubRepoManager(api=api, username="alice")

        extracted, metadata = manager.extract_config_payloads(
            {
                "requirements.txt": "fastapi==0.110.0",
                "package.json": '{"dependencies": {"react": "18"}}',
            }
        )

        assert "requirements.txt" in extracted
        assert "package.json" in extracted
        assert metadata["requirements.txt"]["extraction_status"] == "extracted"
        assert metadata["package.json"]["extraction_status"] == "extracted"

    def test_skips_files_without_registered_extractor(self):
        api = Mock()
        manager = GitHubRepoManager(api=api, username="alice")

        extracted, metadata = manager.extract_config_payloads(
            {
                "README.md": "# docs",
                "requirements.txt": "fastapi==0.110.0",
            }
        )

        assert "README.md" not in extracted
        assert metadata["README.md"]["extraction_status"] == "skipped"
        assert metadata["README.md"]["error"] == "no_extractor"
        assert "requirements.txt" in extracted
