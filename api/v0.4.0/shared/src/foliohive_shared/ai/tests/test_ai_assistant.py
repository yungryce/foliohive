"""Unit tests for the current AIAssistant API."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from foliohive_shared.ai.ai_assistant import AIAssistant, AIServiceError


@pytest.fixture
def assistant_with_table_manager() -> AIAssistant:
    return AIAssistant(username="test_user", table_manager=MagicMock())


class TestAIAssistantInitialization:
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test_key_123"})
    @patch("foliohive_shared.ai.ai_assistant.OpenAI")
    def test_init_with_api_key(self, mock_openai_class):
        assistant = AIAssistant(username="test_user")

        assert assistant.username == "test_user"
        assert assistant.openai_api_key == "test_key_123"
        assert assistant.client is not None
        mock_openai_class.assert_called_once_with(
            api_key="test_key_123",
            base_url="https://api.openai.com/v1",
        )

    @patch.dict("os.environ", {}, clear=True)
    def test_init_without_api_key(self):
        assistant = AIAssistant(username="test_user")

        assert assistant.username == "test_user"
        assert assistant.openai_api_key is None
        assert assistant.client is None

    def test_init_without_username(self):
        assistant = AIAssistant(table_manager=MagicMock())

        assert assistant.username is None


class TestCallAIAPI:
    @patch.dict("os.environ", {}, clear=True)
    def test_raises_without_client(self):
        assistant = AIAssistant(username="test_user", table_manager=MagicMock())

        with pytest.raises(Exception, match="AI service not configured"):
            assistant.call_ai_api("system", "query")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test_key"})
    @patch("foliohive_shared.ai.ai_assistant.OpenAI")
    def test_successful_api_call_uses_default_model(self, mock_openai_class):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.finish_reason = "stop"
        mock_choice.message.content = "AI response"
        mock_response.choices = [mock_choice]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        assistant = AIAssistant(username="test_user", table_manager=MagicMock())
        result = assistant.call_ai_api("system message", "user query")

        assert result == "AI response"
        call_args = mock_client.chat.completions.create.call_args.kwargs
        assert call_args["model"] == "gpt-5-nano"
        assert call_args["messages"][0]["role"] == "system"
        assert call_args["messages"][1]["role"] == "user"

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test_key"})
    @patch("foliohive_shared.ai.ai_assistant.OpenAI")
    def test_balanced_tier_uses_gpt_4o_mini(self, mock_openai_class):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.finish_reason = "stop"
        mock_choice.message.content = "Response"
        mock_response.choices = [mock_choice]
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1
        mock_response.usage.total_tokens = 2
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        assistant = AIAssistant(username="test_user", table_manager=MagicMock())
        assistant.call_ai_api("system", "query", model_tier="balanced")

        call_args = mock_client.chat.completions.create.call_args.kwargs
        assert call_args["model"] == "gpt-4o-mini"

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test_key"})
    @patch("foliohive_shared.ai.ai_assistant.OpenAI")
    def test_api_error_raises_ai_service_error(self, mock_openai_class):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_openai_class.return_value = mock_client

        assistant = AIAssistant(username="test_user", table_manager=MagicMock())

        with pytest.raises(AIServiceError, match="API Error"):
            assistant.call_ai_api("system", "query")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test_key"})
    @patch("foliohive_shared.ai.ai_assistant.OpenAI")
    def test_empty_response_raises_ai_service_error(self, mock_openai_class):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.finish_reason = "stop"
        mock_choice.message.content = "   "
        mock_response.choices = [mock_choice]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 0
        mock_response.usage.total_tokens = 10
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        assistant = AIAssistant(username="test_user", table_manager=MagicMock())

        with pytest.raises(AIServiceError, match="empty or whitespace"):
            assistant.call_ai_api("system", "query")


class TestRepoMicroSummary:
    def test_returns_error_for_empty_context(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()

        result = assistant_with_table_manager.summarize_repo_micro_summary_json(
            repo_name="repo1",
            repo_context={},
        )

        assert result == {"error": "empty_context"}

    def test_returns_error_without_client(self, assistant_with_table_manager):
        assistant_with_table_manager.client = None

        result = assistant_with_table_manager.summarize_repo_micro_summary_json(
            repo_name="repo1",
            repo_context={"repo_name": "repo1"},
        )

        assert result == {"error": "ai_service_not_configured"}

    def test_parses_json_micro_summary_response(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(
            return_value='{"overview":"A repo","key_features":[],"tech_stack":{"languages":[],"frameworks":[],"tools":[]},"architecture_patterns":[],"skill_signals":[]}'
        )

        result = assistant_with_table_manager.summarize_repo_micro_summary_json(
            repo_name="repo1",
            repo_context={"repo_name": "repo1", "readme_chunk": "README"},
        )

        assert result["overview"] == "A repo"
        call_kwargs = assistant_with_table_manager.call_ai_api.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_invalid_json_root_returns_error_payload(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(return_value='["not-a-dict"]')

        result = assistant_with_table_manager.summarize_repo_micro_summary_json(
            repo_name="repo1",
            repo_context={"repo_name": "repo1"},
        )

        assert result["error"] == "invalid_json_root"
        assert "raw_sample" in result


class TestExpandRepo:
    def test_returns_configured_message_without_client(self, assistant_with_table_manager):
        assistant_with_table_manager.client = None

        result = assistant_with_table_manager.expand_repo(
            username="test_user",
            repo_name="repo1",
            micro_summary={"overview": "A repo"},
            repo_metadata={"repo_name": "repo1"},
        )

        assert result == "<p>AI service not configured.</p>"

    def test_returns_none_on_ai_service_error(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(side_effect=AIServiceError("boom"))

        result = assistant_with_table_manager.expand_repo(
            username="test_user",
            repo_name="repo1",
            micro_summary={"overview": "A repo"},
            repo_metadata={"repo_name": "repo1"},
        )

        assert result is None

    def test_returns_markdown_on_success(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(return_value="## Summary")

        result = assistant_with_table_manager.expand_repo(
            username="test_user",
            repo_name="repo1",
            micro_summary={"overview": "A repo"},
            repo_metadata={"repo_name": "repo1", "description": "desc"},
        )

        assert result == "## Summary"


class TestProfileAndQuerySummaries:
    def test_summarize_profile_returns_not_configured_marker_without_client(self, assistant_with_table_manager):
        assistant_with_table_manager.client = None

        result = assistant_with_table_manager.summarize_profile(
            username="test_user",
            profile={"bio": "Developer"},
            aggregate={"repos_included": []},
        )

        assert result == "_AI service not configured._"

    def test_summarize_profile_returns_none_on_ai_service_error(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(side_effect=AIServiceError("boom"))

        result = assistant_with_table_manager.summarize_profile(
            username="test_user",
            profile={"bio": "Developer"},
            aggregate={"repos_included": []},
        )

        assert result is None

    def test_summarize_profile_builds_payload_from_profile_and_aggregate(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(return_value="## Overview")

        aggregate = {
            "username": "test_user",
            "repos_included": ["repo1"],
            "skills": [{"skill": "python", "frequency": 1, "avg_confidence": 0.9, "score": 0.9, "evidence": ["api"]}],
            "domains": [{"domain": "Python", "count": 1}],
            "experience_signals": {"architecture_patterns": [{"pattern": "layered", "count": 1}], "repo_count": 1},
        }
        profile = {"login": "test_user", "bio": "Developer", "company": "Acme", "ignored": "value"}

        result = assistant_with_table_manager.summarize_profile(
            username="test_user",
            profile=profile,
            aggregate=aggregate,
        )

        assert result == "## Overview"
        payload = assistant_with_table_manager.call_ai_api.call_args.args[1]
        assert "ignored" not in payload
        assert "layered" in payload

    def test_summarize_query_returns_not_configured_marker_without_client(self, assistant_with_table_manager):
        assistant_with_table_manager.client = None

        result = assistant_with_table_manager.summarize_query(
            username="test_user",
            query="What backend systems?",
            profile={"bio": "Developer"},
            aggregate={"repos_included": []},
        )

        assert result == "_AI service not configured._"

    def test_summarize_query_returns_none_on_ai_service_error(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(side_effect=AIServiceError("boom"))

        result = assistant_with_table_manager.summarize_query(
            username="test_user",
            query="What backend systems?",
            profile={"bio": "Developer"},
            aggregate={"repos_included": []},
        )

        assert result is None

    def test_summarize_query_passes_query_into_payload(self, assistant_with_table_manager):
        assistant_with_table_manager.client = MagicMock()
        assistant_with_table_manager.call_ai_api = MagicMock(return_value="Direct answer")

        result = assistant_with_table_manager.summarize_query(
            username="test_user",
            query="What backend systems?",
            profile={"login": "test_user", "bio": "Developer"},
            aggregate={"repos_included": ["repo1"], "skills": [], "domains": [], "experience_signals": {"architecture_patterns": [], "repo_count": 1}},
        )

        assert result == "Direct answer"
        payload = assistant_with_table_manager.call_ai_api.call_args.args[1]
        assert "What backend systems?" in payload


class TestHelperMethods:
    def test_get_model_name(self, assistant_with_table_manager):
        assert assistant_with_table_manager._get_model_name("default") == "gpt-5-nano"
        assert assistant_with_table_manager._get_model_name("balanced") == "gpt-4o-mini"
        assert assistant_with_table_manager._get_model_name("missing") == "gpt-5-nano"

    def test_select_profile_fields_filters_unknown_and_empty_values(self, assistant_with_table_manager):
        profile = {
            "login": "test_user",
            "bio": "Developer",
            "company": "",
            "location": None,
            "public_repos": 5,
            "ignored": "value",
        }

        result = assistant_with_table_manager._select_profile_fields(profile)

        assert result == {"login": "test_user", "bio": "Developer", "public_repos": 5}

    def test_compact_aggregate_keeps_only_expected_fields(self, assistant_with_table_manager):
        aggregate = {
            "username": "test_user",
            "repos_included": ["repo1", "repo2"],
            "skills": [{"skill": "python", "frequency": 2, "avg_confidence": 0.95, "score": 1.9, "evidence": ["api"], "ignored": True}],
            "domains": [{"domain": "Python", "count": 2, "extra": "x"}],
            "experience_signals": {"architecture_patterns": [{"pattern": "layered", "count": 1, "ignored": True}], "repo_count": 2},
            "generated_at": "2026-01-01T00:00:00+00:00",
        }

        result = assistant_with_table_manager._compact_aggregate(aggregate)

        assert result["skills"][0] == {
            "skill": "python",
            "frequency": 2,
            "avg_confidence": 0.95,
            "score": 1.9,
            "evidence": ["api"],
        }
        assert result["domains"][0] == {"domain": "Python", "count": 2}
        assert result["experience_signals"]["architecture_patterns"][0] == {"pattern": "layered", "count": 1}

    def test_build_expand_payload_compacts_metadata(self, assistant_with_table_manager):
        result = assistant_with_table_manager._build_expand_payload(
            repo_name="repo1",
            repo_metadata={"repo_name": "repo1", "description": "desc", "stars_count": 3, "ignored": "x"},
            micro_summary={"overview": "A repo"},
        )

        assert result["repo_name"] == "repo1"
        assert result["repo_metadata"] == {"repo_name": "repo1", "description": "desc", "stars_count": 3}


class TestPromptBuilders:
    def test_repo_micro_summary_system_prompt_enforces_json(self, assistant_with_table_manager):
        prompt = assistant_with_table_manager._build_repo_micro_summary_system("repo1")

        assert "json only" in prompt.lower()
        assert "do not return markdown" in prompt.lower()
        assert "overview" in prompt
        assert "skill_signals" in prompt

    def test_expand_micro_summary_system_prompt_mentions_markdown(self, assistant_with_table_manager):
        prompt = assistant_with_table_manager._build_expand_micro_summary_system("test_user", "repo1")

        assert "repo1" in prompt
        assert "test_user" in prompt
        assert "markdown" in prompt.lower()

    def test_profile_summary_system_prompt_mentions_markdown_and_recruiting(self, assistant_with_table_manager):
        prompt = assistant_with_table_manager._build_profile_summary_system("test_user")

        assert "test_user" in prompt
        assert "markdown" in prompt.lower()
        assert "recruit" in prompt.lower()

    def test_query_from_summaries_system_prompt_requires_username_and_query(self, assistant_with_table_manager):
        prompt = assistant_with_table_manager._build_query_from_summaries_system(
            "test_user",
            "What backend systems has this candidate built?",
        )

        assert "test_user" in prompt
        assert "What backend systems has this candidate built?" in prompt
        assert "repository names" in prompt.lower() or "repository" in prompt.lower()
