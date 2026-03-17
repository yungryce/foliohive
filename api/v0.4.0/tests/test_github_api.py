"""Tests for the current GitHubAPI REST and GraphQL behavior."""

from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import requests

from foliohive_shared.github.github_api import GitHubAPI


@pytest.fixture
def api_client():
    """Create a GitHubAPI client with a mocked session and table manager."""
    mock_session_pool = MagicMock()
    mock_session = MagicMock()
    mock_session_pool.get_session.return_value = mock_session
    table_manager = MagicMock()

    client = GitHubAPI(
        token="test_token",
        username="testuser",
        session_pool=mock_session_pool,
        table_manager=table_manager,
    )
    client.session = mock_session
    return client


class TestGitHubAPIRestEndpoint:
    """Test suite for GitHubAPI.make_request()."""

    def test_successful_json_response(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {"key": "value"}
        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")

        assert result == {"key": "value"}
        assert api_client.tracker.totals["requests"] == 1
        assert api_client.tracker.totals["by_endpoint_kind"]["rest"] == 1

    def test_successful_text_response_accept_raw(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = "raw content"
        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo/contents/file", accept_raw=True)

        assert result == "raw content"
        request_headers = api_client.session.request.call_args.kwargs["headers"]
        assert request_headers["Accept"] == "application/vnd.github.v3.raw"

    def test_uses_tracker_purpose_when_none_passed(self, api_client):
        api_client.begin_tracking(repo="sample", purpose="metadata_sync", job_id="job-1")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "59"}
        mock_response.json.return_value = {"ok": True}
        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")

        assert result == {"ok": True}
        assert api_client.tracker.totals["by_kind"]["metadata_sync"] == 1
        assert api_client.tracker.job_id == "job-1"

    def test_records_rate_limit_reset_when_header_is_valid(self, api_client):
        reset_ts = str(int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()))
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "60",
            "X-RateLimit-Reset": reset_ts,
        }
        mock_response.json.return_value = {"ok": True}
        api_client.session.request.return_value = mock_response

        api_client.make_request("GET", "repos/test/repo")

        assert api_client.tracker.rate_limit_remaining == 60
        assert api_client.tracker.rate_limit_reset == "2026-01-01T00:00:00+00:00"

    def test_timeout_exception_is_re_raised_and_recorded(self, api_client):
        api_client.session.request.side_effect = requests.Timeout("timeout")

        with pytest.raises(requests.Timeout):
            api_client.make_request("GET", "repos/test/repo")

        assert api_client.tracker.errors["count"] == 1
        assert api_client.tracker.errors["details"][0]["type"] == "timeout"

    def test_connection_error_is_re_raised_and_recorded(self, api_client):
        api_client.session.request.side_effect = requests.ConnectionError("connection failed")

        with pytest.raises(requests.ConnectionError):
            api_client.make_request("GET", "repos/test/repo")

        assert api_client.tracker.errors["details"][0]["type"] == "connection_error"

    def test_request_exception_is_re_raised_and_recorded(self, api_client):
        api_client.session.request.side_effect = requests.RequestException("generic error")

        with pytest.raises(requests.RequestException):
            api_client.make_request("GET", "repos/test/repo")

        assert api_client.tracker.errors["details"][0]["type"] == "request_error"

    def test_none_response_raises_attribute_error(self, api_client):
        api_client.session.request.return_value = None

        with pytest.raises(AttributeError):
            api_client.make_request("GET", "repos/test/repo")

    def test_headers_lookup_failure_propagates(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        type(mock_response).headers = property(lambda _self: (_ for _ in ()).throw(Exception("headers access failed")))
        api_client.session.request.return_value = mock_response

        with pytest.raises(Exception, match="headers access failed"):
            api_client.make_request("GET", "repos/test/repo")

    def test_rate_limit_403_records_error_and_returns_json(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1735689600",
        }
        mock_response.text = "Forbidden"
        mock_response.json.return_value = {"message": "API rate limit exceeded"}
        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")

        assert result == {"message": "API rate limit exceeded"}
        assert api_client.tracker.rate_limited is True
        assert api_client.tracker.get_error_summary() == "rate_limited"

    def test_404_returns_json_and_records_http_error(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = "Not Found"
        mock_response.json.return_value = {"message": "Not Found"}
        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/notfound")

        assert result == {"message": "Not Found"}
        assert api_client.tracker.get_error_summary() == "http_error"

    def test_json_parse_error_returns_none(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.side_effect = ValueError("Invalid JSON")
        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")

        assert result is None

    def test_error_status_code_still_attempts_json_and_returns_none_on_parse_failure(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.text = "Internal Server Error"
        mock_response.json.side_effect = ValueError("Invalid JSON")
        api_client.session.request.return_value = mock_response

        result = api_client.make_request("GET", "repos/test/repo")

        assert result is None
        assert api_client.tracker.get_error_summary() == "http_error"

    def test_error_response_text_access_failure_propagates(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        type(mock_response).text = property(lambda _self: (_ for _ in ()).throw(Exception("text access failed")))
        api_client.session.request.return_value = mock_response

        with pytest.raises(Exception, match="text access failed"):
            api_client.make_request("GET", "repos/test/repo")

    def test_decode_file_content(self, api_client):
        original = "File content"
        encoded = b64encode(original.encode("utf-8")).decode("utf-8")

        file_data = {"content": encoded}
        result = api_client.decode_file_content(file_data)

        assert result == original

    def test_begin_tracking_resets_tracker_context(self, api_client):
        tracker = api_client.begin_tracking(repo="repo-a", purpose="readme_fetch", job_id="job-9")

        assert tracker is api_client.tracker
        assert tracker.repo == "repo-a"
        assert tracker.purpose == "readme_fetch"
        assert tracker.job_id == "job-9"


class TestGitHubAPIGraphQLEndpoint:
    """Test suite for GitHubAPI.make_request_gql()."""

    def test_successful_graphql_response(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "100"}
        mock_response.json.return_value = {"data": {"viewer": {"login": "testuser"}}}
        api_client.session.post.return_value = mock_response

        result = api_client.make_request_gql(query="query { viewer { login } }")

        assert result == {"data": {"viewer": {"login": "testuser"}}}
        assert api_client.tracker.totals["by_endpoint_kind"]["graphql"] == 1

    def test_graphql_timeout_is_re_raised_and_recorded(self, api_client):
        api_client.session.post.side_effect = requests.Timeout("timeout")

        with pytest.raises(requests.Timeout):
            api_client.make_request_gql(query="query { viewer { login } }")

        assert api_client.tracker.errors["details"][0]["type"] == "timeout"

    def test_graphql_connection_error_is_re_raised_and_recorded(self, api_client):
        api_client.session.post.side_effect = requests.ConnectionError("connection failed")

        with pytest.raises(requests.ConnectionError):
            api_client.make_request_gql(query="query { viewer { login } }")

        assert api_client.tracker.errors["details"][0]["type"] == "connection_error"

    def test_graphql_request_exception_is_re_raised_and_recorded(self, api_client):
        api_client.session.post.side_effect = requests.RequestException("generic error")

        with pytest.raises(requests.RequestException):
            api_client.make_request_gql(query="query { viewer { login } }")

        assert api_client.tracker.errors["details"][0]["type"] == "request_error"

    def test_graphql_none_response_raises_attribute_error(self, api_client):
        api_client.session.post.return_value = None

        with pytest.raises(AttributeError):
            api_client.make_request_gql(query="query { viewer { login } }")

    def test_graphql_rate_limited_returns_none(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"X-RateLimit-Remaining": "0"}
        mock_response.text = "Forbidden"
        api_client.session.post.return_value = mock_response

        result = api_client.make_request_gql(query="query { viewer { login } }")

        assert result is None
        assert api_client.tracker.rate_limited is True

    def test_graphql_errors_in_response_return_none(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {
            "errors": [{"message": "field not found"}],
            "data": None,
        }
        api_client.session.post.return_value = mock_response

        result = api_client.make_request_gql(query="query { viewer { badField } }")

        assert result is None
        assert api_client.tracker.get_error_summary() == "graphql_error"

    def test_graphql_http_500_returns_none_after_json_parse(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.return_value = {"message": "Internal Server Error"}
        api_client.session.post.return_value = mock_response

        result = api_client.make_request_gql(query="query { viewer { login } }")

        assert result is None
        assert api_client.tracker.totals["by_endpoint_kind"]["graphql"] == 1

    def test_graphql_json_parse_error_propagates(self, api_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"X-RateLimit-Remaining": "60"}
        mock_response.json.side_effect = ValueError("Invalid JSON")
        api_client.session.post.return_value = mock_response

        with pytest.raises(ValueError, match="Invalid JSON"):
            api_client.make_request_gql(query="query { viewer { login } }")


class TestGitHubAPITrackingHelpers:
    """Test tracking helpers built into GitHubAPI."""

    def test_track_operation_persists_on_exit(self, api_client):
        with api_client.track_operation(repo="repo-a", purpose="metadata_sync", job_id="job-1") as tracker:
            assert tracker.repo == "repo-a"
            assert tracker.purpose == "metadata_sync"

        api_client.table_manager.upsert_api_usage.assert_called()
