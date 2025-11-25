"""
Unit tests for GitHubAPI.

Tests GitHub API interactions:
- make_request with various methods and responses
- decode_file_content
- Error handling
"""
import pytest
import base64
import responses
from apps.shared.github.github_api import GitHubAPI


DEFAULT_USERNAME = 'testuser'


class TestGitHubAPIInitialization:
    """Test GitHubAPI initialization."""

    def test_init_with_token_and_username(self):
        """Test initialization with explicit token and username."""
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        
        assert api.token == 'test_token'
        assert api.username == DEFAULT_USERNAME
        assert 'Authorization' in api.headers
        assert api.headers['Authorization'] == 'token test_token'
        
    def test_init_with_env_vars(self, mock_env_vars):
        """Test initialization using environment variables when no args provided."""
        api = GitHubAPI(username=DEFAULT_USERNAME)

        assert api.token == 'test_github_token_123'
        assert api.username == 'env-user'
        assert 'Authorization' in api.headers

    def test_init_without_token(self, monkeypatch):
        """Test initialization without token (unauthenticated)."""
        monkeypatch.delenv('GITHUB_TOKEN', raising=False)

        api = GitHubAPI(username=DEFAULT_USERNAME)

        assert api.token is None
        assert api.headers == {}

    def test_init_without_username_raises(self, monkeypatch):
        """Ensure a username is required when none is configured."""
        monkeypatch.delenv('GITHUB_USERNAME', raising=False)
        with pytest.raises(ValueError):
            GitHubAPI(token='test_token')
        

class TestGitHubAPIMakeRequest:
    """Test GitHubAPI make_request method."""

    @responses.activate
    def test_make_request_success_json(self):
        """Test successful request returning JSON."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/test-repo',
            json={'id': 123, 'name': 'test-repo'},
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request('GET', '/repos/testuser/test-repo')
        
        assert result['id'] == 123
        assert result['name'] == 'test-repo'
        
    @responses.activate
    def test_make_request_success_text(self):
        """Test successful request returning plain text."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/test-repo/contents/README.md',
            body='# Test README',
            status=200,
            headers={'Content-Type': 'text/plain'}
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request(
            'GET',
            '/repos/testuser/test-repo/contents/README.md',
            accept_raw=True
        )
        
        assert result == '# Test README'
        
    @responses.activate
    def test_make_request_with_params(self):
        """Test request with query parameters."""
        responses.add(
            responses.GET,
            'https://api.github.com/users/testuser/repos',
            json=[{'id': 1}, {'id': 2}],
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request(
            'GET',
            '/users/testuser/repos',
            params={'type': 'owner', 'per_page': 100}
        )
        
        assert len(result) == 2
        
    @responses.activate
    def test_make_request_with_custom_headers(self):
        """Test request with custom headers."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/test-repo',
            json={'id': 123},
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request(
            'GET',
            '/repos/testuser/test-repo',
            headers={'X-Custom-Header': 'custom-value'}
        )
        
        assert result['id'] == 123
        
        # Verify custom header was sent
        assert len(responses.calls) == 1
        assert 'X-Custom-Header' in responses.calls[0].request.headers
        
    @responses.activate
    def test_make_request_with_accept_raw(self):
        """Test request with accept_raw flag."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/test-repo/readme',
            body='Raw content',
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request(
            'GET',
            '/repos/testuser/test-repo/readme',
            accept_raw=True
        )
        
        assert result == 'Raw content'
        
        # Verify Accept header was set
        assert len(responses.calls) == 1
        assert responses.calls[0].request.headers['Accept'] == 'application/vnd.github.v3.raw'
        
    @responses.activate
    def test_make_request_404_returns_none(self):
        """Test that 404 responses return None."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/nonexistent',
            json={'message': 'Not Found'},
            status=404
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request('GET', '/repos/testuser/nonexistent')
        
        assert result is None
        
    @responses.activate
    def test_make_request_error_raises_exception(self):
        """Test that non-404 errors raise exceptions."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/test-repo',
            json={'message': 'Bad credentials'},
            status=401
        )
        
        api = GitHubAPI(token='invalid_token', username=DEFAULT_USERNAME)
        
        with pytest.raises(Exception):
            api.make_request('GET', '/repos/testuser/test-repo')
            
    @responses.activate
    def test_make_request_rate_limit(self):
        """Test handling of rate limit errors."""
        responses.add(
            responses.GET,
            'https://api.github.com/users/testuser',
            json={'message': 'API rate limit exceeded'},
            status=403
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        
        with pytest.raises(Exception):
            api.make_request('GET', '/users/testuser')
            
    @responses.activate
    def test_make_request_post_with_data(self):
        """Test POST request with JSON data."""
        responses.add(
            responses.POST,
            'https://api.github.com/repos/testuser/test-repo/issues',
            json={'id': 456, 'number': 1},
            status=201
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request(
            'POST',
            '/repos/testuser/test-repo/issues',
            data={'title': 'Test Issue', 'body': 'Test body'}
        )
        
        assert result['id'] == 456
        assert result['number'] == 1
        
    @responses.activate
    def test_make_request_strips_leading_slash(self):
        """Test that leading slash in endpoint is handled."""
        responses.add(
            responses.GET,
            'https://api.github.com/users/testuser',
            json={'login': 'testuser'},
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        
        # Both should work
        result1 = api.make_request('GET', '/users/testuser')
        result2 = api.make_request('GET', 'users/testuser')
        
        assert result1['login'] == 'testuser'
        assert result2['login'] == 'testuser'
        
    @responses.activate
    def test_make_request_timeout(self):
        """Test that timeout parameter is passed to requests."""
        responses.add(
            responses.GET,
            'https://api.github.com/users/testuser',
            json={'login': 'testuser'},
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        result = api.make_request('GET', '/users/testuser', timeout=60)
        
        assert result['login'] == 'testuser'


class TestGitHubAPIDecodeFileContent:
    """Test GitHubAPI decode_file_content method."""

    def test_decode_file_content_success(self, mock_github_response_file):
        """Test successfully decoding base64 file content."""
        api = GitHubAPI(username=DEFAULT_USERNAME)
        
        decoded = api.decode_file_content(mock_github_response_file)
        
        assert decoded == "# Test README\n\nThis is a test file."
        
    def test_decode_file_content_empty(self):
        """Test decoding file with empty content."""
        api = GitHubAPI(username=DEFAULT_USERNAME)
        file_data = {'content': '', 'encoding': 'base64'}
        
        decoded = api.decode_file_content(file_data)
        
        assert decoded is None
        
    def test_decode_file_content_missing_content(self):
        """Test decoding file without content field."""
        api = GitHubAPI(username=DEFAULT_USERNAME)
        file_data = {'name': 'test.txt', 'encoding': 'base64'}
        
        decoded = api.decode_file_content(file_data)
        
        assert decoded is None
        
    def test_decode_file_content_invalid_base64(self):
        """Test handling of invalid base64 content."""
        api = GitHubAPI(username=DEFAULT_USERNAME)
        file_data = {
            'content': 'invalid!@#$%base64',
            'encoding': 'base64'
        }
        
        decoded = api.decode_file_content(file_data)
        
        # Should return None on decode failure
        assert decoded is None
        
    def test_decode_file_content_unicode(self):
        """Test decoding file with unicode content."""
        api = GitHubAPI(username=DEFAULT_USERNAME)
        
        content = "Hello 世界 🌍"
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        file_data = {'content': encoded, 'encoding': 'base64'}
        
        decoded = api.decode_file_content(file_data)
        
        assert decoded == "Hello 世界 🌍"
        
    def test_decode_file_content_multiline(self):
        """Test decoding file with newlines (GitHub base64 format)."""
        api = GitHubAPI(username=DEFAULT_USERNAME)
        
        # GitHub returns base64 with newlines
        content = "Line 1\nLine 2\nLine 3"
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        # Add newlines like GitHub does
        encoded_with_newlines = '\n'.join([encoded[i:i+60] for i in range(0, len(encoded), 60)])
        
        file_data = {'content': encoded_with_newlines, 'encoding': 'base64'}
        
        decoded = api.decode_file_content(file_data)
        
        assert decoded == "Line 1\nLine 2\nLine 3"


class TestGitHubAPIIntegration:
    """Integration-style tests for common workflows."""

    @responses.activate
    def test_fetch_user_repos(self):
        """Test fetching user repositories."""
        responses.add(
            responses.GET,
            'https://api.github.com/users/testuser/repos',
            json=[
                {'id': 1, 'name': 'repo1', 'language': 'Python'},
                {'id': 2, 'name': 'repo2', 'language': 'JavaScript'}
            ],
            status=200
        )
        
        api = GitHubAPI(username=DEFAULT_USERNAME, token='test_token')
        repos = api.make_request('GET', f'/users/{api.username}/repos')
        
        assert len(repos) == 2
        assert repos[0]['name'] == 'repo1'
        assert repos[1]['language'] == 'JavaScript'
        
    @responses.activate
    def test_fetch_repo_details(self):
        """Test fetching repository details."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/test-repo',
            json={
                'id': 123,
                'name': 'test-repo',
                'description': 'A test repository',
                'language': 'Python',
                'stargazers_count': 10
            },
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        repo = api.make_request('GET', '/repos/testuser/test-repo')
        
        assert repo['id'] == 123
        assert repo['language'] == 'Python'
        assert repo['stargazers_count'] == 10
        
    @responses.activate
    def test_fetch_and_decode_readme(self, mock_github_response_file):
        """Test workflow of fetching and decoding README."""
        responses.add(
            responses.GET,
            'https://api.github.com/repos/testuser/test-repo/readme',
            json=mock_github_response_file,
            status=200
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        
        # Fetch README
        readme_data = api.make_request('GET', '/repos/testuser/test-repo/readme')
        
        # Decode content
        decoded_content = api.decode_file_content(readme_data)
        
        assert decoded_content == "# Test README\n\nThis is a test file."


@pytest.mark.parametrize("status_code,should_return_none", [
    (200, False),
    (201, False),
    (404, True),
    (401, False),  # Should raise
    (403, False),  # Should raise
    (500, False),  # Should raise
])
def test_make_request_status_code_handling(status_code, should_return_none):
    """Parametrized test for various HTTP status codes."""
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            'https://api.github.com/test',
            json={'test': 'data'},
            status=status_code
        )
        
        api = GitHubAPI(token='test_token', username=DEFAULT_USERNAME)
        
        if should_return_none:
            result = api.make_request('GET', '/test')
            assert result is None
        elif status_code in (200, 201):
            result = api.make_request('GET', '/test')
            assert result == {'test': 'data'}
        else:
            with pytest.raises(Exception):
                api.make_request('GET', '/test')
