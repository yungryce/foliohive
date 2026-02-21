"""Unit tests for SummaryManager."""

import pytest
import hashlib
import json
from unittest.mock import Mock, patch, MagicMock
from foliohive_shared.ai.summary_manager import SummaryManager, TOKEN_BUDGETS, REPO_SELECTION_STRATEGIES


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


class TestFingerprinting:
    """Test fingerprint calculation."""

    def test_fingerprint_changes_with_content(self):
        """Test fingerprint changes when content changes."""
        manager = SummaryManager("test_user")
        
        fp1 = manager.calculate_fingerprint("profile", [{"data": "v1"}])
        fp2 = manager.calculate_fingerprint("profile", [{"data": "v2"}])
        
        assert fp1 != fp2
        assert len(fp1) == 12
        assert len(fp2) == 12

    def test_fingerprint_stable_for_same_content(self):
        """Test fingerprint is stable for same content."""
        manager = SummaryManager("test_user")
        
        fp1 = manager.calculate_fingerprint("profile", [{"data": "test"}])
        fp2 = manager.calculate_fingerprint("profile", [{"data": "test"}])
        
        assert fp1 == fp2

    def test_fingerprint_uses_existing_fingerprints(self):
        """Test fingerprint incorporates existing fingerprints."""
        manager = SummaryManager("test_user")
        
        fp = manager.calculate_fingerprint(
            "profile",
            [{"fingerprint": "abc123"}, {"fingerprint": "def456"}]
        )
        
        assert len(fp) == 12


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
            readme_content=readme,
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
        mock_cache.get.return_value = {"status": "missing", "data": None}
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                repo_name="repo1",
                repo_metadata={"name": "repo1", "description": "desc"},
                readme_content="# Repo\n\nREADME",
                config_files={"requirements.txt": {"dependencies": ["fastapi"]}},
                fingerprint="fp1",
            )

        assert "summary" in result
        call_kwargs = manager.ai_assistant.summarize_repo_micro_summary_json.call_args.kwargs
        repo_context = call_kwargs["repo_context"]
        assert repo_context["readme_chunk"]
        assert repo_context["config_chunks"]

    def test_enforces_token_budget(self):
        manager = SummaryManager("test_user")
        context = manager.build_repo_context(
            repo_metadata={"name": "repo1", "description": "desc"},
            readme_content="# Title\n" + ("x" * 120000),
            config_files={"package.json": "{" + ("\"k\":\"v\"," * 5000) + "\"z\":\"q\"}"},
            token_budget={"metadata": 2000, "readme": 8000, "config": 2000, "reserve": 1000},
        )
        assert context["tokens_estimated"] <= 13000

    def test_returns_structured_json_output(self):
        manager = SummaryManager("test_user")
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
        mock_cache.get.return_value = {"status": "missing", "data": None}
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                readme_content="readme",
                config_files={},
                fingerprint="fp1",
            )
        assert set(result["summary"].keys()) == {
            "overview",
            "key_features",
            "tech_stack",
            "architecture_patterns",
            "skill_signals",
        }

    def test_validates_json_schema_before_caching(self):
        manager = SummaryManager("test_user")
        manager.ai_assistant.summarize_repo_micro_summary_json = Mock(return_value={"overview": "missing fields"})
        mock_cache = Mock()
        mock_cache.get.return_value = {"status": "missing", "data": None}
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                readme_content="readme",
                config_files={},
                fingerprint="fp1",
            )
        assert result["error"] == "micro_summary_generation_failed"
        mock_cache.save.assert_not_called()

    def test_caches_successful_micro_summary(self):
        manager = SummaryManager("test_user")
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
        mock_cache.get.return_value = {"status": "missing", "data": None}
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            manager.generate_repo_micro_summary(
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                readme_content="readme",
                config_files={},
                fingerprint="fp1",
            )
        mock_cache.save.assert_called_once()

    def test_handles_api_errors_gracefully(self):
        manager = SummaryManager("test_user")
        manager.ai_assistant.summarize_repo_micro_summary_json = Mock(return_value={"error": "api_failure"})
        mock_cache = Mock()
        mock_cache.get.return_value = {"status": "missing", "data": None}
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.generate_repo_micro_summary(
                repo_name="repo1",
                repo_metadata={"name": "repo1"},
                readme_content="readme",
                config_files={},
                fingerprint="fp1",
            )
        assert result["error"] == "micro_summary_generation_failed"


class TestRepoMicroSummaryCaching:
    """Test micro-summary cache operations."""

    def test_cache_key_includes_repo_fingerprint(self):
        manager = SummaryManager("test_user")
        key = manager.build_repo_micro_summary_cache_key("repo-1", "fp123")
        assert "repo_micro_summary" in key
        assert "test_user" in key
        assert "repo-1" in key
        assert "fp123" in key

    def test_retrieves_cached_micro_summary(self):
        manager = SummaryManager("test_user")
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
            result = manager.get_repo_micro_summary("repo1", "fp1")
        assert result is not None
        assert result["overview"] == "cached"

    def test_cache_miss_returns_none(self):
        manager = SummaryManager("test_user")
        mock_cache = Mock()
        mock_cache.get.return_value = {"status": "missing", "data": None}
        with patch("foliohive_shared.ai.summary_manager.cache_manager", mock_cache):
            result = manager.get_repo_micro_summary("repo1", "fp1")
        assert result is None

    def test_bundle_respects_token_budget(self):
        """Test bundle context stays within token budget."""
        manager = SummaryManager("test_user")
        
        # Create large content
        large_readme = "# README\n" + ("x" * 100000)
        
        repo_rows = [{"repo_name": "test-repo", "github_updated_at": "2024-01-01T00:00:00Z"}]
        repo_files = {"test-repo": {"readme_content": large_readme, "config_files": []}}
        
        token_budget = TOKEN_BUDGETS["query"]
        context = manager.build_query_bundle_context(
            query="test",
            repo_rows=repo_rows,
            repo_files=repo_files,
            token_budget=token_budget,
        )
        
        # Check total tokens (updated to new 36k query budget)
        assert context["tokens_estimated"] <= 36000

    def test_bundle_with_multiple_repos_distributes_budget(self):
        """Test bundle with multiple repos distributes token budget."""
        manager = SummaryManager("test_user")
        
        repo_rows = [
            {"repo_name": f"repo{i}", "github_updated_at": f"2024-0{i}-01T00:00:00Z"}
            for i in range(1, 6)
        ]
        
        repo_files = {
            f"repo{i}": {
                "readme_content": f"# Repo {i}\n" + ("Content " * 1000),
                "config_files": []
            }
            for i in range(1, 6)
        }
        
        token_budget = TOKEN_BUDGETS["query"]
        context = manager.build_query_bundle_context(
            query="test",
            repo_rows=repo_rows,
            repo_files=repo_files,
            token_budget=token_budget,
        )
        
        assert context["repos_included"] == 5
        assert len(context["repositories"]) == 5
        # Each repo should get roughly equal budget (updated to new 36k query budget)
        assert context["tokens_estimated"] <= 36000


class TestFingerprintGeneration:
    """Test fingerprint calculation."""

    def test_fingerprint_deterministic(self):
        """Test fingerprint is deterministic for same input."""
        manager = SummaryManager("test_user")
        
        data = [{"key": "value"}, ["list", "items"], "string"]
        
        fp1 = manager.calculate_fingerprint("profile", data)
        fp2 = manager.calculate_fingerprint("profile", data)
        
        assert fp1 == fp2
        assert len(fp1) == 12  # MD5 hash truncated to 12 chars

    def test_fingerprint_changes_with_data(self):
        """Test fingerprint changes when data changes."""
        manager = SummaryManager("test_user")
        
        data1 = [{"key": "value1"}]
        data2 = [{"key": "value2"}]
        
        fp1 = manager.calculate_fingerprint("profile", data1)
        fp2 = manager.calculate_fingerprint("profile", data2)
        
        assert fp1 != fp2

    def test_fingerprint_changes_with_type(self):
        """Test fingerprint changes with summary type."""
        manager = SummaryManager("test_user")
        
        data = [{"key": "value"}]
        
        fp1 = manager.calculate_fingerprint("profile", data)
        fp2 = manager.calculate_fingerprint("readme", data)
        
        assert fp1 != fp2


class TestTokenBudgetConfiguration:
    """Test token budget configurations."""

    def test_token_budgets_defined(self):
        """Test TOKEN_BUDGETS constant is properly defined."""
        assert "profile" in TOKEN_BUDGETS
        assert "readme" in TOKEN_BUDGETS
        assert "query" in TOKEN_BUDGETS

    def test_profile_budget_totals_safe(self):
        """Test profile budget totals to safe limit."""
        budget = TOKEN_BUDGETS["profile"]
        total = sum(budget.values())
        # Updated to new 45k profile budget (well within gpt-5-nano's 128k context window)
        assert total <= 45000

    def test_query_budget_totals_safe(self):
        """Test query budget totals to safe limit."""
        budget = TOKEN_BUDGETS["query"]
        total = sum(budget.values())
        # Updated to new 36k query budget (well within gpt-5-nano's 128k context window)
        assert total <= 36000

    def test_repo_selection_strategies_defined(self):
        """Test REPO_SELECTION_STRATEGIES constant is defined."""
        assert "recent" in REPO_SELECTION_STRATEGIES
        assert "random" in REPO_SELECTION_STRATEGIES
        assert "top_starred" in REPO_SELECTION_STRATEGIES


class TestProfileAggregation:
    """Phase 3: aggregate profile JSON from micro-summaries."""

    @patch('foliohive_shared.ai.summary_manager.cache_manager.save')
    def test_aggregate_profile_from_summaries_deduplicates_and_scores(self, mock_save):
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

        aggregate = manager.aggregate_profile_from_summaries(micro_summaries=micro_summaries)

        assert aggregate["username"] == "test_user"
        assert len(aggregate["repos_included"]) == 2
        assert aggregate["skills"][0]["skill"] == "python"
        assert aggregate["skills"][0]["frequency"] == 2
        assert any(item["pattern"] == "microservices" for item in aggregate["experience_signals"]["architecture_patterns"])
        mock_save.assert_called_once()


class TestProfileHTMLFormatting:
    """Phase 3: format profile HTML from aggregate JSON."""

    @patch('foliohive_shared.ai.summary_manager.cache_manager.save')
    def test_format_profile_html_outputs_required_sections(self, mock_save):
        manager = SummaryManager("test_user")
        aggregate = {
            "profile": {"name": "Test User"},
            "repos_included": ["repo1"],
            "skills": [{"skill": "python", "frequency": 2, "score": 1.7}],
            "domains": [{"domain": "python", "count": 2}],
            "experience_signals": {"architecture_patterns": [{"pattern": "microservices", "count": 1}]},
        }

        html = manager.format_profile_html(aggregate)

        assert "<h2>Overview</h2>" in html
        assert "<h3>Skills</h3>" in html
        assert "<h3>Domains</h3>" in html
        assert "<h3>Experience Signals</h3>" in html
        mock_save.assert_called_once()


class TestQueryFromSummaries:
    """Phase 4: query responses from aggregate + repo micro-summaries."""

    @patch('foliohive_shared.ai.summary_manager.cache_manager.save')
    def test_query_from_summaries_filters_relevant_repos(self, mock_save):
        manager = SummaryManager("test_user")
        aggregate = {
            "skills": [{"skill": "python"}],
        }
        repo_micro_summaries = [
            {
                "repo_name": "api-service",
                "micro_summary": {
                    "overview": "Python API service",
                    "key_features": ["REST API"],
                    "tech_stack": {"languages": ["Python"], "frameworks": ["FastAPI"], "tools": []},
                    "architecture_patterns": ["microservices"],
                },
            },
            {
                "repo_name": "frontend-app",
                "micro_summary": {
                    "overview": "React frontend app",
                    "key_features": ["UI"],
                    "tech_stack": {"languages": ["TypeScript"], "frameworks": ["React"], "tools": []},
                    "architecture_patterns": ["spa"],
                },
            },
        ]

        result = manager.query_from_summaries(
            query="python api",
            profile_aggregate=aggregate,
            repo_micro_summaries=repo_micro_summaries,
            max_repos=1,
        )

        assert "Query: python api" in result["response"]
        assert len(result["repositories_used"]) == 1
        assert result["repositories_used"][0]["name"] == "api-service"
        mock_save.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

