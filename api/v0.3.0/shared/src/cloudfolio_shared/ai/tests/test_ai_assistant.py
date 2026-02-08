"""Unit tests for AIAssistant."""

from unittest.mock import patch, MagicMock
from cloudfolio_shared.ai.ai_assistant import AIAssistant


class TestAIAssistantInitialization:
    """Test AIAssistant initialization."""

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key_123'})
    def test_init_with_api_key(self):
        """Test initialization with API key configured."""
        assistant = AIAssistant(username="test_user")
        
        assert assistant.username == "test_user"
        assert assistant.openai_api_key == "test_key_123"
        assert assistant.client is not None

    @patch.dict('os.environ', {}, clear=True)
    def test_init_without_api_key(self):
        """Test initialization without API key."""
        assistant = AIAssistant(username="test_user")
        
        assert assistant.username == "test_user"
        assert assistant.openai_api_key is None
        assert assistant.client is None

    def test_init_without_username(self):
        """Test initialization without username."""
        assistant = AIAssistant()
        
        assert assistant.username is None


class TestProcessQueryWithBundle:
    """Test process_query_with_bundle method."""

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    def test_empty_bundle_returns_no_repos_response(self):
        """Test handling of empty bundle context."""
        assistant = AIAssistant(username="test_user")
        
        bundle_context = {"repositories": [], "repos_included": 0}
        result = assistant.process_query_with_bundle("test query", bundle_context)
        
        assert "No repositories found" in result["response"]
        assert result["repositories_used"] == []
        assert result["total_repositories"] == 0
        assert result["query"] == "test query"

    @patch.dict('os.environ', {}, clear=True)
    def test_without_api_key_returns_disabled_message(self):
        """Test response when API key is not configured."""
        assistant = AIAssistant(username="test_user")
        
        bundle_context = {
            "repositories": [{"name": "repo1", "stars": 10}],
            "repos_included": 1
        }
        result = assistant.process_query_with_bundle("test query", bundle_context)
        
        assert "not configured" in result["response"] or "OPENAI_API_KEY" in result["response"]
        assert len(result["repositories_used"]) == 1
        assert result["repositories_used"][0]["name"] == "repo1"

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_successful_bundle_processing(self, mock_openai_class):
        """Test successful query processing with bundle."""
        # Mock OpenAI client
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="AI response here"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        
        bundle_context = {
            "repositories": [
                {"name": "repo1", "stars": 10, "primary_language": "Python"},
                {"name": "repo2", "stars": 5, "primary_language": "JavaScript"}
            ],
            "repos_included": 2,
            "selection_strategy": "recent"
        }
        
        result = assistant.process_query_with_bundle("What languages?", bundle_context)
        
        assert result["response"] == "AI response here"
        assert len(result["repositories_used"]) == 2
        assert result["total_repositories"] == 2
        assert result["query"] == "What languages?"
        assert result["repositories_used"][0]["primary_language"] == "Python"


class TestBuildQueryBundleSystem:
    """Test _build_query_bundle_system method."""

    def test_builds_system_prompt_with_repos(self):
        """Test system prompt includes repository information."""
        assistant = AIAssistant(username="test_user")
        
        bundle_context = {
            "repositories": [
                {
                    "name": "test-repo",
                    "description": "A test repository",
                    "primary_language": "Python",
                    "stars": 42,
                    "forks": 10,
                    "readme_summary": "# README\nThis is a test.",
                    "config_summaries": [
                        {"filename": "requirements.txt", "content": "flask==2.0.0"}
                    ]
                }
            ],
            "repos_included": 1,
            "selection_strategy": "recent"
        }
        
        prompt = assistant._build_query_bundle_system("What Python frameworks?", bundle_context)
        
        # Check key components
        assert "test_user" in prompt.lower() or "candidate" in prompt.lower()
        assert "test-repo" in prompt
        assert "Python" in prompt
        assert "42 stars" in prompt
        assert "README" in prompt
        assert "requirements.txt" in prompt
        assert "What Python frameworks?" in prompt

    def test_strategy_description_in_prompt(self):
        """Test strategy is explained in system prompt."""
        assistant = AIAssistant(username="test_user")
        
        # Test that strategies are described meaningfully
        strategies_to_test = [
            ("recent", "recent"),
            ("random", "random"),
            ("top_starred", "most starred")  # Implementation says "most starred (most popular)"
        ]
        
        for strategy, expected_desc in strategies_to_test:
            bundle_context = {
                "repositories": [{"name": "repo1", "stars": 10}],
                "repos_included": 1,
                "selection_strategy": strategy
            }
            
            prompt = assistant._build_query_bundle_system("test", bundle_context)
            
            # Each strategy should have a meaningful description
            assert expected_desc in prompt.lower()

    def test_handles_missing_optional_fields(self):
        """Test prompt building with minimal repo data."""
        assistant = AIAssistant(username="test_user")
        
        bundle_context = {
            "repositories": [{"name": "minimal-repo"}],
            "repos_included": 1,
            "selection_strategy": "recent"
        }
        
        prompt = assistant._build_query_bundle_system("test query", bundle_context)
        
        assert "minimal-repo" in prompt
        assert prompt is not None
        assert len(prompt) > 0


class TestSummarizeReadmeHTML:
    """Test summarize_readme_html method."""

    @patch.dict('os.environ', {}, clear=True)
    def test_returns_disabled_message_without_api_key(self):
        """Test README summary without API key."""
        assistant = AIAssistant(username="test_user")
        
        result = assistant.summarize_readme_html("# Test README", "test-repo")
        
        assert "not configured" in result or "OPENAI_API_KEY" in result

    def test_returns_empty_message_for_empty_readme(self):
        """Test handling of empty README."""
        assistant = AIAssistant(username="test_user")
        
        result = assistant.summarize_readme_html("", "test-repo")
        
        assert "No README content" in result or result == ""

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_calls_openai_api_with_readme_content(self, mock_openai_class):
        """Test README summarization calls OpenAI API."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="<h2>Summary</h2>"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        result = assistant.summarize_readme_html("# Test README\n\nContent here", "test-repo")
        
        assert result == "<h2>Summary</h2>"
        mock_client.chat.completions.create.assert_called_once()


class TestSummarizeProfileHTML:
    """Test summarize_profile_html method."""

    @patch.dict('os.environ', {}, clear=True)
    def test_returns_disabled_message_without_api_key(self):
        """Test profile summary without API key."""
        assistant = AIAssistant(username="test_user")
        
        result = assistant.summarize_profile_html({"bio": "Test bio"}, "test_user")
        
        assert "not configured" in result or "OPENAI_API_KEY" in result

    def test_returns_empty_message_for_empty_profile(self):
        """Test handling of empty profile."""
        assistant = AIAssistant(username="test_user")
        
        result = assistant.summarize_profile_html({}, "test_user")
        
        assert "No profile data" in result or result == ""

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_calls_openai_api_with_profile_data(self, mock_openai_class):
        """Test profile summarization calls OpenAI API."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="<h2>Profile</h2>"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        profile_data = {"bio": "Developer", "location": "SF"}
        result = assistant.summarize_profile_html(profile_data, "test_user")
        
        assert result == "<h2>Profile</h2>"
        mock_client.chat.completions.create.assert_called_once()


class TestCallAIAPI:
    """Test call_ai_api method."""

    @patch.dict('os.environ', {}, clear=True)
    def test_returns_error_without_client(self):
        """Test API call without configured client."""
        assistant = AIAssistant(username="test_user")
        
        result = assistant.call_ai_api("system", "query", "req_123")
        
        assert "not configured" in result or "OPENAI_API_KEY" in result

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_successful_api_call(self, mock_openai_class):
        """Test successful OpenAI API call with default model."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="AI response"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        result = assistant.call_ai_api("system message", "user query", "req_123")
        
        assert result == "AI response"
        
        # Verify API call parameters
        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["model"] == "gpt-5-nano"  # Default model
        assert len(call_args[1]["messages"]) == 2

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_handles_api_error(self, mock_openai_class):
        """Test error handling in API call."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        result = assistant.call_ai_api("system", "query", "req_123")
        
        assert "error" in result.lower() or "failed" in result.lower()


class TestSystemPromptBuilders:
    """Test system prompt building methods."""

    def test_readme_summary_system_prompt(self):
        """Test README summary system prompt generation."""
        assistant = AIAssistant(username="test_user")
        
        prompt = assistant._build_readme_summary_system("test-repo")
        
        assert "test-repo" in prompt.lower()
        assert "html" in prompt.lower()
        assert "markdown" not in prompt.lower() or "no markdown" in prompt.lower()

    def test_profile_summary_system_prompt(self):
        """Test profile summary system prompt generation."""
        assistant = AIAssistant(username="test_user")
        
        prompt = assistant._build_profile_summary_system("test_user")
        
        assert "test_user" in prompt.lower()
        assert "profile" in prompt.lower()
        assert "html" in prompt.lower()
        assert "recruiter" in prompt.lower()

    def test_readme_prompt_without_repo_name(self):
        """Test README prompt with no repo name."""
        assistant = AIAssistant(username="test_user")
        
        prompt = assistant._build_readme_summary_system(None)
        
        assert "repository" in prompt.lower()
        assert prompt is not None


class TestModelTierSelection:
    """Test model tier selection and configuration."""

    def test_get_model_name_default_tier(self):
        """Test _get_model_name returns gpt-5-nano for default tier."""
        assistant = AIAssistant(username="test_user")
        
        model_name = assistant._get_model_name("default")
        
        assert model_name == "gpt-5-nano"

    def test_get_model_name_balanced_tier(self):
        """Test _get_model_name returns gpt-4o-mini for balanced tier."""
        assistant = AIAssistant(username="test_user")
        
        model_name = assistant._get_model_name("balanced")
        
        assert model_name == "gpt-4o-mini"

    def test_get_model_name_invalid_tier_defaults_to_default(self):
        """Test _get_model_name falls back to default for invalid tier."""
        assistant = AIAssistant(username="test_user")
        
        model_name = assistant._get_model_name("nonexistent_tier")
        
        assert model_name == "gpt-5-nano"

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_call_ai_api_uses_specified_tier(self, mock_openai_class):
        """Test call_ai_api uses correct model for specified tier."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Response"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        assistant.call_ai_api("system", "query", "req_123", model_tier="balanced")
        
        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["model"] == "gpt-4o-mini"

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_summarize_readme_html_accepts_model_tier(self, mock_openai_class):
        """Test summarize_readme_html accepts and uses model_tier parameter."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="<h2>Summary</h2>"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        result = assistant.summarize_readme_html("# README", "repo", model_tier="balanced")
        
        assert result == "<h2>Summary</h2>"
        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["model"] == "gpt-4o-mini"

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_process_query_with_bundle_accepts_model_tier(self, mock_openai_class):
        """Test process_query_with_bundle accepts and uses model_tier parameter."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Query response"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        bundle_context = {
            "repositories": [{"name": "repo1", "stars": 10}],
            "repos_included": 1
        }
        result = assistant.process_query_with_bundle("query", bundle_context, model_tier="balanced")
        
        assert result["response"] == "Query response"
        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["model"] == "gpt-4o-mini"


class TestOpenAISpecificBehavior:
    """Test OpenAI-specific features and error handling."""

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    def test_openai_client_initialization_with_correct_base_url(self):
        """Test OpenAI client is initialized with correct base URL."""
        with patch('cloudfolio_shared.ai.ai_assistant.OpenAI') as mock_openai_class:
            assistant = AIAssistant(username="test_user")
            
            mock_openai_class.assert_called_once_with(
                api_key='test_key',
                base_url='https://api.openai.com/v1'
            )

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_api_call_includes_proper_message_structure(self, mock_openai_class):
        """Test API calls use proper OpenAI message structure."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Response"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        assistant = AIAssistant(username="test_user")
        assistant.call_ai_api("system prompt", "user query", "req_123")
        
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "system prompt"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "user query"

    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'})
    @patch('cloudfolio_shared.ai.ai_assistant.OpenAI')
    def test_handles_openai_initialization_error(self, mock_openai_class):
        """Test graceful handling of OpenAI initialization errors."""
        mock_openai_class.side_effect = Exception("Authentication failed")
        
        assistant = AIAssistant(username="test_user")
        
        assert assistant.client is None
        assert assistant.openai_api_key == "test_key"

