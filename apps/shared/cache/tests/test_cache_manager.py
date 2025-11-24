"""
Unit tests for CacheManager.

Tests cache operations:
- Initialization with various credential types
- Get/set/delete operations
- TTL and expiration handling
- Fingerprint tracking
- Cache decorator functionality
"""
import pytest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, patch, call
from apps.shared.cache.cache_manager import CacheManager


class TestCacheManagerInitialization:
    """Test CacheManager initialization scenarios."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        cache = CacheManager()
        
        assert cache.container_name == "github-cache"
        assert cache.default_ttl == 21600
        assert cache.use_cache is True
        assert cache._initialized is False
        
    def test_init_with_custom_params(self):
        """Test initialization with custom parameters."""
        cache = CacheManager(
            container_name="custom-cache",
            default_ttl=3600,
            use_cache=False
        )
        
        assert cache.container_name == "custom-cache"
        assert cache.default_ttl == 3600
        assert cache.use_cache is False
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    @patch('apps.shared.cache.cache_manager.DefaultAzureCredential')
    def test_init_cache_with_managed_identity(self, mock_credential, mock_blob_client, mock_env_vars):
        """Test cache initialization using Managed Identity."""
        cache = CacheManager()
        cache._init_cache()
        
        # Should try Managed Identity first
        mock_credential.assert_called_once()
        assert cache.blob_service_client is not None
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_init_cache_with_connection_string_fallback(self, mock_blob_client, mock_env_vars):
        """Test cache initialization falling back to connection string."""
        # Simulate MI failure
        with patch('apps.shared.cache.cache_manager.DefaultAzureCredential', 
                   side_effect=Exception("MI not available")):
            cache = CacheManager()
            cache._init_cache()
            
            # Should fall back to connection string
            mock_blob_client.from_connection_string.assert_called_once()
            
    def test_init_cache_disabled(self):
        """Test that cache initialization is skipped when disabled."""
        cache = CacheManager(use_cache=False)
        cache._init_cache()
        
        assert cache.blob_service_client is None
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_init_cache_creates_container(self, mock_blob_client, mock_env_vars):
        """Test that container is created during initialization."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        cache = CacheManager()
        cache._init_cache()
        
        mock_instance.create_container.assert_called_once_with("github-cache")


class TestCacheManagerGet:
    """Test CacheManager get operations."""

    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_get_valid_cache_entry(self, mock_blob_client, mock_env_vars):
        """Test retrieving a valid cache entry."""
        # Setup mock
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        mock_blob.exists.return_value = True
        
        # Mock properties
        mock_properties = MagicMock()
        mock_properties.metadata = {}
        mock_properties.last_modified = datetime(2025, 1, 15, 12, 0, 0)
        mock_properties.size = 1024
        mock_blob.get_blob_properties.return_value = mock_properties
        
        # Mock data
        cache_data = {
            'data': {'test': 'value'},
            'cached_at': '2025-01-15T12:00:00'
        }
        mock_blob.download_blob.return_value.readall.return_value = json.dumps(cache_data).encode()
        
        # Test
        cache = CacheManager()
        result = cache.get('test_key')
        
        assert result['status'] == 'valid'
        assert result['data'] == {'test': 'value'}
        assert result['size_bytes'] == 1024
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_get_missing_cache_entry(self, mock_blob_client, mock_env_vars):
        """Test retrieving a non-existent cache entry."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        mock_blob.exists.return_value = False
        
        cache = CacheManager()
        result = cache.get('missing_key')
        
        assert result['status'] == 'missing'
        assert result['data'] is None
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_get_expired_cache_entry(self, mock_blob_client, mock_env_vars):
        """Test retrieving an expired cache entry."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        mock_blob.exists.return_value = True
        
        # Set expired timestamp
        expired_time = (datetime.now() - timedelta(hours=1)).isoformat()
        mock_properties = MagicMock()
        mock_properties.metadata = {'expires_at': expired_time}
        mock_properties.last_modified = datetime(2025, 1, 15, 12, 0, 0)
        mock_properties.size = 1024
        mock_blob.get_blob_properties.return_value = mock_properties
        
        cache = CacheManager()
        result = cache.get('expired_key')
        
        assert result['status'] == 'expired'
        assert result['data'] is None
        mock_blob.delete_blob.assert_called_once()
        
    def test_get_cache_disabled(self):
        """Test get when cache is disabled."""
        cache = CacheManager(use_cache=False)
        result = cache.get('test_key')
        
        assert result['status'] == 'disabled'
        assert result['data'] is None
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_get_with_fingerprint(self, mock_blob_client, mock_env_vars):
        """Test retrieving cache entry with fingerprint metadata."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        mock_blob.exists.return_value = True
        
        mock_properties = MagicMock()
        mock_properties.metadata = {'fingerprint': 'abc123def456'}
        mock_properties.last_modified = datetime(2025, 1, 15, 12, 0, 0)
        mock_properties.size = 1024
        mock_blob.get_blob_properties.return_value = mock_properties
        
        cache_data = {'data': {'test': 'value'}, 'cached_at': '2025-01-15T12:00:00'}
        mock_blob.download_blob.return_value.readall.return_value = json.dumps(cache_data).encode()
        
        cache = CacheManager()
        result = cache.get('test_key')
        
        assert result['status'] == 'valid'
        assert result['fingerprint'] == 'abc123def456'


class TestCacheManagerSave:
    """Test CacheManager save operations."""

    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_save_without_ttl(self, mock_blob_client, mock_env_vars):
        """Test saving data without TTL (no expiration)."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        
        cache = CacheManager()
        test_data = {'test': 'value'}
        result = cache.save('test_key', test_data)
        
        assert result is True
        mock_blob.upload_blob.assert_called_once()
        
        # Check that no expires_at was set
        call_args = mock_blob.upload_blob.call_args
        metadata = call_args.kwargs['metadata']
        assert 'expires_at' not in metadata
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_save_with_ttl(self, mock_blob_client, mock_env_vars):
        """Test saving data with TTL."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        
        cache = CacheManager()
        test_data = {'test': 'value'}
        result = cache.save('test_key', test_data, ttl=3600)
        
        assert result is True
        
        # Check that expires_at was set
        call_args = mock_blob.upload_blob.call_args
        metadata = call_args.kwargs['metadata']
        assert 'expires_at' in metadata
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_save_with_fingerprint(self, mock_blob_client, mock_env_vars):
        """Test saving data with fingerprint."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        
        cache = CacheManager()
        test_data = {'test': 'value'}
        result = cache.save('test_key', test_data, fingerprint='abc123')
        
        assert result is True
        
        call_args = mock_blob.upload_blob.call_args
        metadata = call_args.kwargs['metadata']
        assert metadata['fingerprint'] == 'abc123'
        
    def test_save_cache_disabled(self):
        """Test save when cache is disabled."""
        cache = CacheManager(use_cache=False)
        result = cache.save('test_key', {'test': 'value'})
        
        assert result is False
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_save_handles_errors(self, mock_blob_client, mock_env_vars):
        """Test that save handles errors gracefully."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        mock_blob.upload_blob.side_effect = Exception("Upload failed")
        
        cache = CacheManager()
        result = cache.save('test_key', {'test': 'value'})
        
        assert result is False


class TestCacheManagerDelete:
    """Test CacheManager delete operations."""

    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_delete_existing_entry(self, mock_blob_client, mock_env_vars):
        """Test deleting an existing cache entry."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        mock_blob.exists.return_value = True
        
        cache = CacheManager()
        result = cache.delete('test_key')
        
        assert result is True
        mock_blob.delete_blob.assert_called_once()
        
    @patch('apps.shared.cache.cache_manager.BlobServiceClient')
    def test_delete_non_existing_entry(self, mock_blob_client, mock_env_vars):
        """Test deleting a non-existent entry returns True."""
        mock_instance = MagicMock()
        mock_blob_client.return_value = mock_instance
        
        mock_blob = MagicMock()
        mock_instance.get_blob_client.return_value = mock_blob
        mock_blob.exists.return_value = False
        
        cache = CacheManager()
        result = cache.delete('missing_key')
        
        assert result is True
        mock_blob.delete_blob.assert_not_called()
        
    def test_delete_cache_disabled(self):
        """Test delete when cache is disabled."""
        cache = CacheManager(use_cache=False)
        result = cache.delete('test_key')
        
        assert result is False


class TestCacheManagerCacheKeyGeneration:
    """Test cache key generation utilities."""

    def test_generate_cache_key_bundle(self):
        """Test generating cache key for user bundle."""
        key = CacheManager.generate_cache_key(kind='bundle', username='testuser')
        assert key == 'repos_bundle_context_testuser'
        
    def test_generate_cache_key_repo(self):
        """Test generating cache key for individual repo."""
        key = CacheManager.generate_cache_key(kind='repo', username='testuser', repo='my-repo')
        assert key == 'repo_level_bundle_testuser_my-repo'
        
    def test_generate_cache_key_model(self):
        """Test generating cache key for model."""
        key = CacheManager.generate_cache_key(kind='model', fingerprint='abc123')
        assert key == 'model_abc123'
        
    def test_generate_cache_key_model_metadata(self):
        """Test generating cache key for model metadata."""
        key = CacheManager.generate_cache_key(kind='model')
        assert key == 'fine_tuned_model_metadata'
        
    def test_generate_cache_key_default_username(self):
        """Test that default username is used when not provided."""
        key = CacheManager.generate_cache_key(kind='bundle')
        assert 'yungryce' in key
        
    def test_generate_cache_key_sanitizes_repo_name(self):
        """Test that repo names with special characters are sanitized."""
        key = CacheManager.generate_cache_key(kind='repo', username='testuser', repo='my/repo name')
        assert key == 'repo_level_bundle_testuser_my_repo_name'


@pytest.mark.parametrize("kind,username,repo,expected_prefix", [
    ('bundle', 'user1', None, 'repos_bundle_context_'),
    ('repo', 'user2', 'test-repo', 'repo_level_bundle_'),
    ('model', 'user3', None, 'model_'),
])
def test_generate_cache_key_parametrized(kind, username, repo, expected_prefix):
    """Parametrized test for cache key generation."""
    kwargs = {'kind': kind, 'username': username}
    if repo:
        kwargs['repo'] = repo
    
    key = CacheManager.generate_cache_key(**kwargs)
    assert key.startswith(expected_prefix)
