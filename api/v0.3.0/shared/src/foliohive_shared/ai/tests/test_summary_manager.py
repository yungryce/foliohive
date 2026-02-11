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


class TestCacheKeys:
    """Test cache key generation."""

    def test_cache_key_includes_components(self):
        """Test cache key includes all required components."""
        manager = SummaryManager("test_user")
        
        key = manager.build_cache_key("profile", "job123", "fp456")
        
        assert "summary" in key
        assert "profile" in key
        assert "test_user" in key
        assert "job123" in key
        assert "fp456" in key

    def test_cache_key_with_extras(self):
        """Test cache key includes extra identifiers."""
        manager = SummaryManager("test_user")
        
        key = manager.build_cache_key(
            "readme",
            "job123",
            "fp456",
            repo_name="myrepo"
        )
        
        assert "myrepo" in key


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

    def test_build_profile_context(self):
        """Test building profile context with multiple repos."""
        manager = SummaryManager("test_user")
        
        profile = {
            "name": "Test User",
            "bio": "Software developer",
            "location": "San Francisco",
            "public_repos": 10,
            "followers": 50
        }
        
        top_repos = [
            {
                "name": "repo1",
                "description": "First repo",
                "primary_language": "Python",
                "languages": ["Python"],
                "topics": ["api"],
                "stats": {"stars": 10, "forks": 2}
            }
        ]
        
        repo_files = {
            "repo1": {
                "readme_content": "# Repo 1",
                "config_files": [{"filename": "setup.py", "content": "setup()"}]
            }
        }
        
        statistics = {
            "repo_count": 10,
            "stars_total": 50,
            "forks_total": 10
        }
        
        token_budget = {"metadata": 2000, "readme": 5000, "config": 3000}
        
        context = manager.build_profile_context(
            profile=profile,
            top_repos=top_repos,
            repo_files=repo_files,
            statistics=statistics,
            token_budget=token_budget
        )
        
        assert context["username"] == "test_user"
        assert context["profile"]["name"] == "Test User"
        assert context["statistics"]["repo_count"] == 10
        assert len(context["repositories"]) == 1
        assert context["repositories"][0]["name"] == "repo1"
        assert "readme_chunk" in context["repositories"][0]
        assert "tokens_estimated" in context


class TestRepoSelectionStrategies:
    """Test repository selection strategies."""

    def test_recent_strategy_sorts_by_update_date(self):
        """Test recent strategy selects most recently updated repos."""
        manager = SummaryManager("test_user")
        
        repos = [
            {"repo_name": "old", "github_updated_at": "2023-01-01T00:00:00Z"},
            {"repo_name": "newest", "github_updated_at": "2024-01-01T00:00:00Z"},
            {"repo_name": "middle", "github_updated_at": "2023-06-01T00:00:00Z"}
        ]
        
        selected = manager._select_repos_by_strategy(repos, "recent", 2)
        
        assert len(selected) == 2
        assert selected[0]["repo_name"] == "newest"
        assert selected[1]["repo_name"] == "middle"

    def test_top_starred_strategy_sorts_by_stars(self):
        """Test top_starred strategy selects most starred repos."""
        manager = SummaryManager("test_user")
        
        repos = [
            {"repo_name": "unpopular", "stars_count": 5},
            {"repo_name": "popular", "stars_count": 100},
            {"repo_name": "moderate", "stars_count": 50}
        ]
        
        selected = manager._select_repos_by_strategy(repos, "top_starred", 2)
        
        assert len(selected) == 2
        assert selected[0]["repo_name"] == "popular"
        assert selected[1]["repo_name"] == "moderate"

    @patch('random.sample')
    def test_random_strategy_samples_repos(self, mock_sample):
        """Test random strategy samples repos."""
        manager = SummaryManager("test_user")
        
        repos = [
            {"repo_name": "repo1"},
            {"repo_name": "repo2"},
            {"repo_name": "repo3"}
        ]
        
        mock_sample.return_value = [repos[0], repos[2]]
        selected = manager._select_repos_by_strategy(repos, "random", 2)
        
        mock_sample.assert_called_once()
        assert len(selected) == 2

    def test_unknown_strategy_defaults_to_recent(self):
        """Test unknown strategy falls back to recent."""
        manager = SummaryManager("test_user")
        
        repos = [
            {"repo_name": "old", "github_updated_at": "2023-01-01T00:00:00Z"},
            {"repo_name": "new", "github_updated_at": "2024-01-01T00:00:00Z"}
        ]
        
        selected = manager._select_repos_by_strategy(repos, "invalid_strategy", 1)
        
        assert len(selected) == 1
        assert selected[0]["repo_name"] == "new"

    def test_max_repos_limits_selection(self):
        """Test max_repos parameter limits results."""
        manager = SummaryManager("test_user")
        
        repos = [{"repo_name": f"repo{i}", "stars_count": i} for i in range(10)]
        
        selected = manager._select_repos_by_strategy(repos, "top_starred", 3)
        
        assert len(selected) == 3


class TestBundleContextBuilding:
    """Test build_query_bundle_context method."""

    def test_builds_bundle_with_repos(self):
        """Test bundle context includes selected repos."""
        manager = SummaryManager("test_user")
        
        repo_rows = [
            {
                "repo_name": "test-repo",
                "description": "Test repository",
                "primary_language": "Python",
                "stars_count": 42,
                "forks_count": 10,
                "github_updated_at": "2024-01-01T00:00:00Z"
            }
        ]
        
        repo_files = {
            "test-repo": {
                "readme_content": "# Test README\n\nContent here",
                "config_files": [
                    {"filename": "requirements.txt", "content": "flask==2.0.0"}
                ]
            }
        }
        
        token_budget = TOKEN_BUDGETS["query"]
        context = manager.build_query_bundle_context(
            query="What frameworks?",
            repo_rows=repo_rows,
            repo_files=repo_files,
            token_budget=token_budget,
            selection_strategy="recent",
            max_repos=5
        )
        
        assert context["query"] == "What frameworks?"
        assert context["repos_included"] == 1
        assert context["selection_strategy"] == "recent"
        assert len(context["repositories"]) == 1
        
        repo = context["repositories"][0]
        assert repo["name"] == "test-repo"
        assert repo["description"] == "Test repository"
        assert repo["primary_language"] == "Python"
        assert repo["stars"] == 42
        assert "readme_summary" in repo
        assert "config_summaries" in repo

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
            max_repos=1
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
            max_repos=5
        )
        
        assert context["repos_included"] == 5
        assert len(context["repositories"]) == 5
        # Each repo should get roughly equal budget (updated to new 36k query budget)
        assert context["tokens_estimated"] <= 36000


class TestCacheManagement:
    """Test cache operations."""

    def test_get_cached_summary_hit(self):
        """Test cache hit returns cached summary."""
        manager = SummaryManager("test_user")
        
        with patch('foliohive_shared.ai.summary_manager.cache_manager.get') as mock_get:
            cached_data = {
                "summary_html": "<h2>Cached</h2>",
                "metadata": {
                    "fingerprint": "abc123",
                    "tokens_estimated": 1000
                }
            }
            mock_get.return_value = {"status": "valid", "data": cached_data}
            
            result = manager.get_cached_summary("cache_key", "abc123")
            
            assert result is not None
            assert result["summary_html"] == "<h2>Cached</h2>"

    def test_get_cached_summary_miss(self):
        """Test cache miss returns None."""
        manager = SummaryManager("test_user")
        
        with patch('foliohive_shared.ai.summary_manager.cache_manager.get') as mock_get:
            mock_get.return_value = {"status": "missing"}
            
            result = manager.get_cached_summary("cache_key", "abc123")
            
            assert result is None

    def test_get_cached_summary_fingerprint_mismatch(self):
        """Test fingerprint mismatch invalidates cache."""
        manager = SummaryManager("test_user")
        
        with patch('foliohive_shared.ai.summary_manager.cache_manager.get') as mock_get:
            cached_data = {
                "summary_html": "<h2>Cached</h2>",
                "metadata": {"fingerprint": "old_fingerprint"}
            }
            mock_get.return_value = {"status": "valid", "data": cached_data}
            
            result = manager.get_cached_summary("cache_key", "new_fingerprint")
            
            assert result is None

    def test_cache_summary(self):
        """Test caching summary."""
        manager = SummaryManager("test_user", cache_ttl=3600)
        
        with patch('foliohive_shared.ai.summary_manager.cache_manager.save') as mock_save:
            metadata = {"fingerprint": "abc123", "tokens": 1000}
            manager.cache_summary("cache_key", "<h2>Summary</h2>", metadata)
            
            mock_save.assert_called_once()
            call_args = mock_save.call_args
            assert call_args[1]["cache_key"] == "cache_key"
            assert call_args[1]["ttl"] == manager.cache_ttl
            assert call_args[1]["fingerprint"] == metadata["fingerprint"]
            assert call_args[1]["data"]["summary_html"] == "<h2>Summary</h2>"


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


class TestCacheKeyBuilding:
    """Test cache key generation."""

    def test_cache_key_format(self):
        """Test cache key follows expected format."""
        manager = SummaryManager("test_user")
        
        key = manager.build_cache_key("profile", "job123", "abc123")
        
        assert key.startswith("summary_profile_test_user_job123_abc123")

    def test_cache_key_with_extra_params(self):
        """Test cache key includes extra parameters."""
        manager = SummaryManager("test_user")
        
        key = manager.build_cache_key(
            "query",
            "job123",
            "abc123",
            query_hash="qh123",
            strategy="recent"
        )
        
        assert "qh123" in key
        assert "recent" in key

    def test_cache_key_unique_per_strategy(self):
        """Test different strategies produce different cache keys."""
        manager = SummaryManager("test_user")
        
        key1 = manager.build_cache_key("query", "job123", "abc123", strategy="recent")
        key2 = manager.build_cache_key("query", "job123", "abc123", strategy="random")
        
        assert key1 != key2


class TestHighLevelAPIMethods:
    """Test high-level summary generation methods."""

    @patch('foliohive_shared.ai.summary_manager.AIAssistant')
    def test_get_or_generate_profile_summary(self, mock_assistant_class):
        """Test profile summary generation."""
        mock_assistant = MagicMock()
        mock_assistant.summarize_profile_html.return_value = "<h2>Profile</h2>"
        mock_assistant_class.return_value = mock_assistant

        profile = {"name": "Test User", "bio": "Developer"}
        repo_rows = [{"name": "repo1", "stars_count": 10}]
        languages = {"repo1": [{"language": "Python", "percentage": 100}]}
        statistics = {"repo_count": 1}

        with patch.object(SummaryManager, "get_cached_summary", return_value=None), \
             patch('foliohive_shared.ai.summary_manager.cache_manager.save') as mock_save:
            manager = SummaryManager("test_user")

            result = manager.get_or_generate_profile_summary(
                job_id="job123",
                profile=profile,
                repo_rows=repo_rows,
                languages_by_repo=languages,
                statistics=statistics
            )

            assert result["summary_html"] == "<h2>Profile</h2>"
            assert "metadata" in result
            assert result["metadata"]["job_id"] == "job123"
            mock_save.assert_called_once()

    def test_get_or_generate_readme_summary_cache_hit(self):
        """Test README summary with cache hit."""
        cached_data = {
            "summary_html": "<h2>Cached README</h2>",
            "metadata": {
                "cache_hit": True
            }
        }

        with patch.object(SummaryManager, "get_cached_summary", return_value=cached_data):
            manager = SummaryManager("test_user")
            result = manager.get_or_generate_readme_summary(
                job_id="job123",
                repo_name="test-repo",
                readme_content="# README",
                repo_metadata={"name": "test-repo"}
            )

            assert result["summary_html"] == "<h2>Cached README</h2>"
            assert result["metadata"]["cache_hit"] is True

    @patch('foliohive_shared.ai.summary_manager.AIAssistant')
    def test_get_or_generate_query_response(self, mock_assistant_class):
        """Test query response generation."""
        mock_assistant = MagicMock()
        mock_assistant.summarize_query_html.return_value = {
            "response": "Answer here",
            "repositories_used": [{"name": "repo1"}],
            "total_repositories": 1,
            "query": "test query"
        }
        mock_assistant_class.return_value = mock_assistant

        repo_rows = [{"repo_name": "repo1", "github_updated_at": "2024-01-01T00:00:00Z"}]
        repo_files = {"repo1": {"readme_content": "# README", "config_files": []}}

        with patch.object(SummaryManager, "get_cached_summary", return_value=None), \
             patch('foliohive_shared.ai.summary_manager.cache_manager.save') as mock_save:
            manager = SummaryManager("test_user")

            result = manager.get_or_generate_query_response(
                job_id="job123",
                query="test query",
                repo_rows=repo_rows,
                repo_files=repo_files,
                selection_strategy="recent",
                max_repos=5
            )

            assert result["response"] == "Answer here"
            assert result["repositories_used"][0]["name"] == "repo1"
            mock_save.assert_called_once()


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

