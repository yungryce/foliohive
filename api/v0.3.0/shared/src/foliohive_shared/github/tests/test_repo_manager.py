"""Initial Phase 1 tests for GitHubRepoManager discovery behavior."""

from unittest.mock import Mock

import pytest

from foliohive_shared.github.github_repo_manager import GitHubRepoManager


class TestRepoManagerFileDiscovery:
    """Test file discovery and path indexing."""

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
            limit=5,
        )

        assert "src/package.json" in selected
        assert "src/random.txt" not in selected

    def test_persists_discovered_paths_to_table(self):
        api = Mock()
        manager = GitHubRepoManager(api=api, username="alice")
        table = Mock()

        manager.persist_discovered_paths(
            table_manager_obj=table,
            username="alice",
            repo="sample",
            fingerprint="fp-1",
            config_files={"package.json": "{}"},
            readme_files={"README.md": "# readme"},
            extraction_metadata={
                "package.json": {
                    "extractor_key": "_extract_package_json",
                    "extraction_status": "extracted",
                }
            },
        )

        table.upsert_repo_discovered_paths.assert_called_once()
        row = table.upsert_repo_discovered_paths.call_args[0][0]
        assert row.username == "alice"
        assert row.repo_name == "sample"
        assert "README.md" in row.discovered_paths
        assert "package.json" in row.discovered_paths
        assert row.extraction_metadata["package.json"]["extraction_status"] == "extracted"


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

    def test_persists_extracted_artifact_to_blob(self):
        api = Mock()
        manager = GitHubRepoManager(api=api, username="alice")
        cache = Mock()
        cache.generate_cache_key.side_effect = lambda **kwargs: f"k:{kwargs['filename']}"

        cached = manager.cache_extracted_config_files(
            cache_manager_obj=cache,
            username="alice",
            repo="sample",
            extracted_config_files={"package.json": {"dependencies": {"react": "18"}}},
        )

        assert cached == 1
        cache.save.assert_called_once_with("k:package.json", {"dependencies": {"react": "18"}})

    def test_updates_discovered_path_extraction_status(self):
        api = Mock()
        manager = GitHubRepoManager(api=api, username="alice")
        table = Mock()

        manager.persist_extraction_statuses(
            table_manager_obj=table,
            username="alice",
            repo="sample",
            extraction_metadata={
                "package.json": {
                    "extractor_key": "_extract_package_json",
                    "extraction_status": "extracted",
                },
                "pyproject.toml": {
                    "extractor_key": "_extract_pyproject_toml",
                    "extraction_status": "failed",
                    "error": "invalid_toml",
                },
            },
        )

        assert table.update_repo_discovered_path_extraction_status.call_count == 2

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
