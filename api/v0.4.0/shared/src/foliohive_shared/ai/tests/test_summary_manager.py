"""Unit tests for SummaryManager."""

import pytest
from unittest.mock import Mock, patch
from foliohive_shared.ai.summary_manager import SummaryManager, TOKEN_BUDGETS


class TestTokenEstimation:
    """Test token estimation and chunking."""

    def test_estimate_tokens(self):
        """Test basic token estimation."""
        manager = SummaryManager("test_user")
        
        text = "a" * 400  # 400 chars = ~100 tokens
        assert manager.estimate_tokens(text) == 100
        
        assert manager.estimate_tokens("") == 0
        assert manager.estimate_tokens(None) == 0

    def test_chunk_readme_respects_budget(self):
        """Test README chunking respects token budget."""
        manager = SummaryManager("test_user")
        
        long_readme = "# Title\n" + ("x" * 50000)
        chunked = manager.chunk_readme(long_readme, max_tokens=1000)
        
        # Allow small tolerance for chunking logic (within 1%)
        assert manager.estimate_tokens(chunked) <= 1010
        assert "... [README truncated for length]" in chunked

    def test_chunk_readme_preserves_short_content(self):
        """Test short README is not truncated."""
        manager = SummaryManager("test_user")
        
        short_readme = "# Short README\n\nJust a few words."
        chunked = manager.chunk_readme(short_readme, max_tokens=1000)
        
        assert chunked == short_readme
        assert "... [README truncated for length]" not in chunked

    def test_chunk_readme_preserves_sections(self):
        """Test chunking tries to preserve complete sections."""
        manager = SummaryManager("test_user")
        
        readme = "# Title\n\n" + ("x" * 2000) + "\n\n## Section\n\n" + ("y" * 2000)
        chunked = manager.chunk_readme(readme, max_tokens=600)
        
        # Should break at section boundary
        assert "## Section" not in chunked or chunked.endswith("... [README truncated for length]")


class TestConfigChunking:
    """Test config file chunking."""

    def test_chunk_config_preserves_small_files(self):
        """Test small config files are not truncated."""
        manager = SummaryManager("test_user")
        
        small_config = "key: value\nanother: setting"
        chunked = manager.chunk_config_file("config.yml", small_config, max_tokens=200)
        
        assert chunked == small_config

    def test_chunk_package_json(self):
        """Test package.json gets smart truncation."""
        manager = SummaryManager("test_user")
        
        package_json = """{
  "name": "test-package",
  "version": "1.0.0",
  "description": "Test",
  "scripts": {"test": "jest"},
  "dependencies": {"react": "^18.0.0"},
  "devDependencies": {"jest": "^29.0.0"}
}"""
        
        chunked = manager.chunk_config_file("package.json", package_json, max_tokens=100)
        
        # Should include essentials
        assert "name" in chunked
        assert "dependencies" in chunked

    def test_chunk_dockerfile(self):
        """Test Dockerfile gets smart truncation."""
        manager = SummaryManager("test_user")
        
        dockerfile = """# Comment
FROM node:18
RUN npm install
EXPOSE 3000
CMD ["npm", "start"]
# More comments"""
        
        chunked = manager.chunk_config_file("Dockerfile", dockerfile, max_tokens=50)
        
        # Should keep important instructions
        assert "FROM" in chunked
        assert "EXPOSE" in chunked


class TestContextBuilding:
    """Test context building methods."""

    def test_build_repo_context(self):
        """Test building single repo context."""
        manager = SummaryManager("test_user")
        
        repo_metadata = {
            "name": "test-repo",
            "description": "A test repository",
            "primary_language": "Python",
            "languages": ["Python", "JavaScript"],
            "topics": ["api", "backend"],
            "stats": {"stars": 10, "forks": 5}
        }
        
        readme = "# Test Repo\n\nThis is a test."
        config_files = {"requirements.txt": "flask\nrequests"}
        
        token_budget = {"metadata": 1000, "readme": 2000, "config": 1000}
        
        context = manager.build_repo_context(
            repo_metadata=repo_metadata,
            primary_readme_content=readme,
            config_files=config_files,
            token_budget=token_budget
        )
        
        assert context["repo_name"] == "test-repo"
        assert context["description"] == "A test repository"
        assert context["readme_chunk"] == readme
        assert len(context["config_chunks"]) == 1
        assert context["config_chunks"][0]["filename"] == "requirements.txt"
        assert "tokens_estimated" in context


class TestRepoMicroSummaryGeneration:
    """Test generate_repo_micro_summary() pipeline."""

    def test_builds_context_from_readme_and_configs(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.ai_assistant.summarize_repo_micro_summary_json = Mock(
            return_value={
                "overview": "A repo",
                "key_features": ["api"],
                "tech_stack": {"languages": ["Python"], "frameworks": [], "tools": []},
                "architecture_patterns": ["layered"],
                "skill_signals": [{"skill": "python", "confidence": 0.9, "evidence": "requirements"}],
            }
        )

        mock_cache = Mock()
        with patch("foliohive_shared.ai.summary_manager.get_table_manager"), \
             patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                username="test_user",
                job_id="j1",
                repo_name="repo1",
                repo_metadata={"name": "repo1", "description": "desc"},
                primary_readme_content="# Repo\n\nREADME",
                config_content={"requirements.txt": "fastapi==0.110.0"},
                fingerprint="fp1",
                skip_cache_lookup=True,
            )

        assert result["summary"] is True
        call_kwargs = manager.ai_assistant.summarize_repo_micro_summary_json.call_args.kwargs
        repo_context = call_kwargs["repo_context"]
        assert repo_context["readme_chunk"]
        assert repo_context["config_chunks"]

    def test_enforces_token_budget(self):
        manager = SummaryManager("test_user")
        context = manager.build_repo_context(
            repo_metadata={"name": "repo1", "description": "desc"},
            primary_readme_content="# Title\n" + ("x" * 120000),
            config_files={"package.json": "{" + ("\"k\":\"v\"," * 5000) + "\"z\":\"q\"}"},
            token_budget={"metadata": 2000, "readme": 8000, "config": 2000, "reserve": 1000},
        )
        assert context["tokens_estimated"] <= 13000

    def test_returns_structured_json_output(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.ai_assistant.summarize_repo_micro_summary_json = Mock(
            return_value={
                "overview": "A repo",
                "key_features": ["api"],
                "tech_stack": {"languages": ["Python"], "frameworks": [], "tools": []},
                "architecture_patterns": ["layered"],
                "skill_signals": [{"skill": "python", "confidence": 0.9, "evidence": "requirements"}],
            }
        )
        mock_cache = Mock()
        with patch("foliohive_shared.ai.summary_manager.get_table_manager"), \
             patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                username="test_user",
                job_id="j1",
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                primary_readme_content="readme",
                config_content={},
                fingerprint="fp1",
                skip_cache_lookup=True,
            )
        assert result["summary"] is True
        assert result["cache_hit"] is False

    def test_validates_json_schema_before_caching(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.ai_assistant.summarize_repo_micro_summary_json = Mock(return_value={"overview": "missing fields"})
        mock_cache = Mock()
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                username="test_user",
                job_id="j1",
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                primary_readme_content="readme",
                config_content={},
                fingerprint="fp1",
                skip_cache_lookup=True,
            )
        assert result["summary"] is False
        assert result["error"].startswith("missing_keys:")
        mock_cache.save.assert_not_called()

    def test_caches_successful_micro_summary(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.ai_assistant.summarize_repo_micro_summary_json = Mock(
            return_value={
                "overview": "A repo",
                "key_features": ["api"],
                "tech_stack": {"languages": ["Python"], "frameworks": [], "tools": []},
                "architecture_patterns": ["layered"],
                "skill_signals": [{"skill": "python", "confidence": 0.9, "evidence": "requirements"}],
            }
        )
        mock_cache = Mock()
        with patch("foliohive_shared.ai.summary_manager.get_table_manager"), \
             patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            manager.generate_repo_micro_summary(
                username="test_user",
                job_id="j1",
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                primary_readme_content="readme",
                config_content={},
                fingerprint="fp1",
                skip_cache_lookup=True,
            )
        mock_cache.save.assert_called_once()

    def test_handles_api_errors_gracefully(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.ai_assistant.summarize_repo_micro_summary_json = Mock(return_value={"error": "api_failure"})
        mock_cache = Mock()
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                username="test_user",
                job_id="j1",
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                primary_readme_content="readme",
                config_content={},
                fingerprint="fp1",
                skip_cache_lookup=True,
            )
        assert result["summary"] is False
        assert result["error"] == "api_failure"


class TestRepoMicroSummaryCaching:
    """Test micro-summary cache operations."""

    def test_cache_key_includes_repo_fingerprint(self):
        manager = SummaryManager("test_user")
        key = manager.build_repo_micro_summary_cache_key("repo-1", "fp123")
        assert "repo_micro_summary" in key
        assert "repo-1" in key
        assert "fp123" in key

    def test_retrieves_cached_micro_summary(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.table_manager.get_cache_summary.return_value = {"cache_status": "valid", "cache_key": "some_key"}
        mock_cache = Mock()
        mock_cache.get.return_value = {
            "status": "valid",
            "data": {
                "overview": "cached",
                "key_features": [],
                "tech_stack": {},
                "architecture_patterns": [],
                "skill_signals": [],
            },
        }
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.get_cache_repo_micro_summary("repo1", "fp1")
        assert result is not None
        assert result["overview"] == "cached"

    def test_cache_miss_returns_none(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.table_manager.get_cache_summary.return_value = None
        result = manager.get_cache_repo_micro_summary("repo1", "fp1")
        assert result is None



class TestTokenBudgetConfiguration:
    """Test token budget configurations."""

    def test_token_budgets_defined(self):
        assert "metadata" in TOKEN_BUDGETS
        assert "readme" in TOKEN_BUDGETS
        assert "config" in TOKEN_BUDGETS
        assert "reserve" in TOKEN_BUDGETS

    def test_budget_totals_safe(self):
        """Test all budget values sum to a safe total."""
        total = sum(TOKEN_BUDGETS.values())
        assert total <= 4096


class TestProfileAggregation:
    """Phase 3: aggregate profile JSON from micro-summaries."""

    @patch('foliohive_shared.ai.summary_manager.cache_manager.save')
    def test_aggregate_micro_summaries_deduplicates_and_scores(self, mock_save):
        manager = SummaryManager("test_user")
        micro_summaries = [
            {
                "repo_name": "repo1",
                "micro_summary": {
                    "architecture_patterns": ["microservices"],
                    "tech_stack": {"languages": ["Python"], "frameworks": ["FastAPI"], "tools": ["Docker"]},
                    "skill_signals": [
                        {"skill": "python", "confidence": 0.9, "evidence": "API implementation"},
                        {"skill": "testing", "confidence": 0.7, "evidence": "unit tests"},
                    ],
                },
            },
            {
                "repo_name": "repo2",
                "micro_summary": {
                    "architecture_patterns": ["event-driven"],
                    "tech_stack": {"languages": ["Python"], "frameworks": ["Flask"], "tools": ["Docker"]},
                    "skill_signals": [
                        {"skill": "python", "confidence": 0.8, "evidence": "service code"},
                    ],
                },
            },
        ]

        aggregate = manager.aggregate_micro_summaries(micro_summaries=micro_summaries)

        assert aggregate["username"] == "test_user"
        assert len(aggregate["repos_included"]) == 2
        assert aggregate["skills"][0]["skill"] == "python"
        assert aggregate["skills"][0]["frequency"] == 2
        assert any(item["pattern"] == "microservices" for item in aggregate["experience_signals"]["architecture_patterns"])
        mock_save.assert_called_once()



class TestQueryResponseGeneration:
    """Test get_or_generate_query_response."""

    def test_query_response_shape_and_context(self):
        manager = SummaryManager("test_user")
        micro_summaries = [
            {
                "repo_name": "api-service",
                "fingerprint": "fp1",
                "micro_summary": {
                    "overview": "Python API",
                    "key_features": ["REST"],
                    "tech_stack": {"languages": ["Python"], "frameworks": ["FastAPI"], "tools": []},
                    "architecture_patterns": ["microservices"],
                    "skill_signals": [{"skill": "python", "confidence": 0.9, "evidence": "code"}],
                },
            },
        ]
        manager._load_cached_micro_summaries = Mock(return_value=micro_summaries)
        manager.aggregate_micro_summaries = Mock(return_value={"skills": [{"skill": "python"}], "repos_included": ["api-service"]})
        manager.ai_assistant.summarize_query = Mock(return_value="## Response\n\nPython expertise found.")

        result = manager.get_or_generate_query_response(
            job_id="j1",
            query="python api experience",
            profile={"login": "test_user"},
        )

        manager._load_cached_micro_summaries.assert_called_once_with("j1")
        assert result is not None
        assert "response" in result
        assert "repositories_used" in result
        assert "total_repositories" in result
        assert result["query"] == "python api experience"
        assert "metadata" in result


class TestCachedMicroSummaryLoading:
    """Test per-job micro-summary loading behavior."""

    def test_load_cached_micro_summaries_filters_and_preserves_order(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.table_manager.list_repo_statuses.return_value = [
            {"repo_name": "repo-a", "username": "test_user", "status": "summary_ready"},
            {"repo_name": "repo-b", "username": "other_user", "status": "summary_ready"},
            {"repo_name": "repo-c", "username": "test_user", "status": "synced"},
            {"repo_name": "repo-d", "username": "test_user", "status": "summary_ready"},
        ]
        manager._load_single_cached_micro_summary = Mock(side_effect=[
            {"repo_name": "repo-a", "fingerprint": "fp-a", "micro_summary": {"overview": "A"}},
            {"repo_name": "repo-d", "fingerprint": "fp-d", "micro_summary": {"overview": "D"}},
        ])

        result = manager._load_cached_micro_summaries("job-1")

        assert [item["repo_name"] for item in result] == ["repo-a", "repo-d"]
        manager._load_single_cached_micro_summary.assert_any_call(
            {"repo_name": "repo-a", "username": "test_user", "status": "summary_ready"}
        )
        manager._load_single_cached_micro_summary.assert_any_call(
            {"repo_name": "repo-d", "username": "test_user", "status": "summary_ready"}
        )

    def test_load_single_cached_micro_summary_skips_failures(self):
        manager = SummaryManager("test_user")
        manager.table_manager = Mock()
        manager.table_manager.get_repo_github_metadata.side_effect = RuntimeError("table read failed")

        result = manager._load_single_cached_micro_summary(
            {"repo_name": "repo-a", "username": "test_user", "status": "summary_ready"}
        )

        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

