"""
Comprehensive tests for github_api.py and github_graphql_api.py.

Tests the refactored make_request() and make_request_gql() methods
with multi-step error detection framework.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest
import requests

from foliohive_shared.github.api_usage import ApiUsageTracker
from foliohive_shared.github.github_api import GitHubAPI
from foliohive_shared.github.github_graphql_api import GitHubGraphQLAPI


class TestGitHubAPIRestEndpoint:
    """Test suite for GitHubAPI.make_request() with error detection."""

    @pytest.fixture
    def api_client(self):
        """Create a GitHubAPI client with mocked session."""
        with patch('foliohive_shared.github.github_api.GitHubAPI._build_session') as mock_build:
            mock_build.return_value = MagicMock()
            client = GitHubAPI(token="test_token", username="testuser")
            return client

    # Successful requests
    def test_successful_json_response(self, api_client):
        """Successfully fetch and parse JSON response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {"key": "value"}

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result == {"key": "value"}

    def test_successful_text_response_accept_raw(self, api_client):
        """Successfully fetch raw text response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = "raw content"

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo/contents/file", accept_raw=True)
        
        assert result == "raw content"

    def test_successful_200_to_299_range(self, api_client):
        """Handle all 2xx status codes."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {"created": True}

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result == {"created": True}

    # Exception handling - POST/Request level
    def test_timeout_exception(self, api_client):
        """Catch requests.Timeout separately."""
        api_client.session.request.side_effect = requests.Timeout("timeout")

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    def test_connection_error_exception(self, api_client):
        """Catch requests.ConnectionError separately."""
        api_client.session.request.side_effect = requests.ConnectionError("connection failed")

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    def test_request_exception(self, api_client):
        """Catch generic RequestException."""
        api_client.session.request.side_effect = requests.RequestException("generic error")

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    # Response object validation
    def test_response_is_none(self, api_client):
        """Validate response object is not None after request."""
        api_client.session.request.return_value = None

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    # Status code and headers
    def test_headers_access_failure(self, api_client):
        """Handle failure when accessing response.headers."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = PropertyMock(side_effect=Exception("headers access failed"))
        mock_response.json.return_value = {"key": "value"}

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        # Should still return JSON despite headers access failure
        assert result == {"key": "value"}

    def test_rate_limit_check_failure(self, api_client):
        """Handle failure in rate limit check."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.headers = PropertyMock(side_effect=Exception("headers access failed"))

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    # Usage tracking
    def test_usage_tracking(self, api_client):
        """Record usage with status code and rate_remaining."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "59",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Reset": "1234567890"
        }
        mock_response.json.return_value = {"key": "value"}

        api_client.session.request.return_value = mock_response
        usage = ApiUsageTracker(owner="test", repo="repo")

        result = api_client.make_request(
            "GET",
            "repos/test/repo",
            usage=usage,
            purpose="test_purpose"
        )

        assert result == {"key": "value"}
        assert usage.totals.get("requests", 0) > 0

    def test_usage_recording_failure(self, api_client):
        """Handle failure in usage recording gracefully."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {"key": "value"}

        api_client.session.request.return_value = mock_response
        
        usage = Mock()
        usage.record_request.side_effect = Exception("recording failed")

        result = api_client.make_request(
            "GET",
            "repos/test/repo",
            usage=usage
        )

        # Should still return result despite usage recording failure
        assert result == {"key": "value"}

    # Rate limiting
    def test_rate_limit_exceeded(self, api_client):
        """Handle rate limit exceeded (403 with remaining=0)."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Reset": "1234567890"
        }
        mock_response.text = "Forbidden"

        api_client.session.request.return_value = mock_response
        usage = ApiUsageTracker(owner="test", repo="repo")

        result = api_client.make_request(
            "GET",
            "repos/test/repo",
            usage=usage
        )

        assert result is None

    # Specific status codes
    def test_404_not_found(self, api_client):
        """Handle 404 not found."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.headers = {"X-RateLimit-Remaining": "60"}

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/notfound")
        
        assert result is None

    # JSON parsing and fallback
    def test_json_parse_error_with_text_fallback(self, api_client):
        """Fall back to text when JSON parsing fails."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "fallback text content"

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result == "fallback text content"

    def test_json_access_error(self, api_client):
        """Handle exception when calling response.json()."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.side_effect = Exception("json() failed")

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    # Error responses
    def test_error_status_codes(self, api_client):
        """Handle non-2xx status codes."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = "Internal Server Error"

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    def test_error_response_text_access_failure(self, api_client):
        """Handle failure when reading error response body."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = PropertyMock(side_effect=Exception("text access failed"))

        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")
        
        assert result is None

    # Utility methods
    def test_decode_file_content(self, api_client):
        """Test decode_file_content utility method."""
        import base64
        
        original = "File content"
        encoded = base64.b64encode(original.encode('utf-8')).decode('utf-8')
        
        file_data = {"content": encoded}
        result = api_client.decode_file_content(file_data)
        
        assert result == original

    def test_get_user_profile(self, api_client):
        """Test get_user_profile convenience method."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "60",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Reset": "1234567890"
        }
        mock_response.json.return_value = {"login": "testuser", "id": 123}

        api_client.session.request.return_value = mock_response

        result = api_client.get_user_profile("testuser")
        
        assert result["login"] == "testuser"
        assert result["id"] == 123


class TestGitHubGraphQLAPIEndpoint:
    """Test suite for GitHubGraphQLAPI.make_request_gql()."""

    @pytest.fixture
    def graphql_client(self):
        """Create a GitHubGraphQLAPI client with mocked session."""
        mock_session = MagicMock()
        client = GitHubGraphQLAPI(token="test_token", session=mock_session)
        return client

    # Successful requests
    def test_successful_graphql_response(self, graphql_client):
        """Successfully execute GraphQL query and parse response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {
            "data": {"viewer": {"login": "testuser"}}
        }

        graphql_client.session.post.return_value = mock_response

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result["data"]["viewer"]["login"] == "testuser"

    # Exception handling
    def test_timeout_exception(self, graphql_client):
        """Catch requests.Timeout separately."""
        graphql_client.session.post.side_effect = requests.Timeout("timeout")

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    def test_connection_error_exception(self, graphql_client):
        """Catch requests.ConnectionError separately."""
        graphql_client.session.post.side_effect = requests.ConnectionError("connection failed")

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    def test_request_exception(self, graphql_client):
        """Catch generic RequestException."""
        graphql_client.session.post.side_effect = requests.RequestException("generic error")

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    def test_unexpected_exception(self, graphql_client):
        """Catch unexpected exceptions during POST."""
        graphql_client.session.post.side_effect = Exception("unexpected error")

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    # Response object validation
    def test_response_is_none(self, graphql_client):
        """Validate response object is not None after POST."""
        graphql_client.session.post.return_value = None

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    # Headers access
    def test_headers_access_failure(self, graphql_client):
        """Handle failure when accessing response.headers."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = PropertyMock(side_effect=Exception("headers access failed"))
        mock_response.json.return_value = {"data": {"viewer": {"login": "testuser"}}}

        graphql_client.session.post.return_value = mock_response

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        # Should still return data despite headers access failure
        assert result["data"]["viewer"]["login"] == "testuser"

    # Usage tracking
    def test_usage_tracking(self, graphql_client):
        """Record usage with status code and rate_remaining."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "100"
        }
        mock_response.json.return_value = {"data": {}}

        graphql_client.session.post.return_value = mock_response
        usage = ApiUsageTracker(owner="test", repo="repo")

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            usage=usage,
            purpose="test"
        )

        assert result == {"data": {}}
        assert usage.totals.get("requests", 0) > 0

    def test_usage_recording_failure(self, graphql_client):
        """Handle failure in usage recording gracefully."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "100"}
        mock_response.json.return_value = {"data": {}}

        graphql_client.session.post.return_value = mock_response
        
        usage = Mock()
        usage.record_request.side_effect = Exception("recording failed")

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            usage=usage,
            purpose="test"
        )

        # Should still return result despite usage recording failure
        assert result == {"data": {}}

    # Rate limiting
    def test_rate_limit_exceeded(self, graphql_client):
        """Handle rate limit exceeded (403 with remaining=0)."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.headers = {
            "X-RateLimit-Remaining": "0"
        }

        graphql_client.session.post.return_value = mock_response
        usage = ApiUsageTracker(owner="test", repo="repo")

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            usage=usage,
            purpose="test"
        )

        assert result is None

    # HTTP error responses
    def test_http_error_response(self, graphql_client):
        """Handle non-200 HTTP status codes."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = "Internal Server Error"

        graphql_client.session.post.return_value = mock_response

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    def test_http_error_response_body_read_failure(self, graphql_client):
        """Handle failure when reading error response body."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = PropertyMock(side_effect=Exception("text access failed"))

        graphql_client.session.post.return_value = mock_response

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    # JSON parsing
    def test_json_parse_error(self, graphql_client):
        """Handle JSON parse errors."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.side_effect = ValueError("Invalid JSON")

        graphql_client.session.post.return_value = mock_response

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    def test_json_access_error(self, graphql_client):
        """Handle exceptions when calling response.json()."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.side_effect = Exception("json() failed")

        graphql_client.session.post.return_value = mock_response

        result = graphql_client.make_request_gql(
            query="query { viewer { login } }",
            purpose="test"
        )

        assert result is None

    # GraphQL-specific: GraphQL errors in response
    def test_graphql_errors_in_response(self, graphql_client):
        """Handle GraphQL errors in successful HTTP response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {
            "errors": [{"message": "field not found"}],
            "data": None
        }

        graphql_client.session.post.return_value = mock_response

        result = graphql_client.make_request_gql(
            query="query { viewer { badField } }",
            purpose="test"
        )

        # GraphQL errors are returned (caller decides how to handle)
        assert "errors" in result
        assert result["errors"][0]["message"] == "field not found"


class TestGitHubGraphQLAPIBlobFetch:
    """Test the fetch_blobs_gql() integration method."""

    @pytest.fixture
    def graphql_client(self):
        """Create a GitHubGraphQLAPI client."""
        client = GitHubGraphQLAPI(token="test_token")
        return client

    def test_fetch_blobs_empty_paths(self, graphql_client):
        """Handle empty paths list."""
        result = graphql_client.fetch_blobs_gql(
            owner="testuser",
            repo="testrepo",
            paths=[]
        )

        assert result == {}

    def test_fetch_blobs_successful(self, graphql_client):
        """Successfully fetch multiple blobs."""
        with patch.object(
            graphql_client,
            'make_request_gql',
            return_value={
                "data": {
                    "repository": {
                        "f0": {"text": "content0", "isBinary": False},
                        "f1": {"text": "content1", "isBinary": False}
                    }
                }
            }
        ):
            result = graphql_client.fetch_blobs_gql(
                owner="testuser",
                repo="testrepo",
                paths=["file1.txt", "file2.txt"]
            )

            assert result["file1.txt"] == "content0"
            assert result["file2.txt"] == "content1"

    def test_fetch_blobs_binary_files(self, graphql_client):
        """Skip binary files."""
        with patch.object(
            graphql_client,
            'make_request_gql',
            return_value={
                "data": {
                    "repository": {
                        "f0": {"text": None, "isBinary": True},
                        "f1": {"text": "content1", "isBinary": False}
                    }
                }
            }
        ):
            result = graphql_client.fetch_blobs_gql(
                owner="testuser",
                repo="testrepo",
                paths=["image.png", "readme.txt"]
            )

            assert result["image.png"] is None
            assert result["readme.txt"] == "content1"

    def test_fetch_blobs_null_nodes(self, graphql_client):
        """Handle null blob nodes."""
        with patch.object(
            graphql_client,
            'make_request_gql',
            return_value={
                "data": {
                    "repository": {
                        "f0": None,
                        "f1": {"text": "content1", "isBinary": False}
                    }
                }
            }
        ):
            result = graphql_client.fetch_blobs_gql(
                owner="testuser",
                repo="testrepo",
                paths=["missing.txt", "readme.txt"]
            )

            assert result["missing.txt"] is None
            assert result["readme.txt"] == "content1"

    def test_fetch_blobs_invalid_payload(self, graphql_client):
        """Handle invalid payload (not dict)."""
        with patch.object(
            graphql_client,
            'make_request_gql',
            return_value=None
        ):
            result = graphql_client.fetch_blobs_gql(
                owner="testuser",
                repo="testrepo",
                paths=["file1.txt", "file2.txt"]
            )

            # Should return None for all paths
            assert result["file1.txt"] is None
            assert result["file2.txt"] is None

    def test_fetch_blobs_invalid_repository_data(self, graphql_client):
        """Handle invalid repository data in response."""
        with patch.object(
            graphql_client,
            'make_request_gql',
            return_value={
                "data": {
                    "repository": None
                }
            }
        ):
            result = graphql_client.fetch_blobs_gql(
                owner="testuser",
                repo="testrepo",
                paths=["file1.txt"]
            )

            assert result["file1.txt"] is None

    def test_fetch_blobs_with_usage_tracking(self, graphql_client):
        """Track usage during blob fetch."""
        usage = ApiUsageTracker(owner="testuser", repo="testrepo")

        with patch.object(
            graphql_client,
            'make_request_gql',
            return_value={
                "data": {
                    "repository": {
                        "f0": {"text": "content", "isBinary": False}
                    }
                }
            }
        ) as mock_make_request:
            result = graphql_client.fetch_blobs_gql(
                owner="testuser",
                repo="testrepo",
                paths=["file.txt"],
                usage=usage
            )

            assert result["file.txt"] == "content"
            # Verify make_request_gql was called with usage
            mock_make_request.assert_called_once()
            assert mock_make_request.call_args[1]["usage"] == usage
