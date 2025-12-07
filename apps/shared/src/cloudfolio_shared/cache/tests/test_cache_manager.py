"""Unit tests for the trimmed ``CacheManager`` implementation."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from cloudfolio_shared.cache.cache_manager import CacheManager


@pytest.fixture()
def mock_env_vars(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values = {
        "BLOB_SERVICE_URI": "https://example.blob.core.windows.net",
        "AzureWebJobsStorage__blobServiceUri": "https://example.blob.core.windows.net",
        "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield values
    for key in values:
        monkeypatch.delenv(key, raising=False)


class TestCacheManagerInitialization:
    def test_init_with_defaults(self):
        cache = CacheManager()

        assert cache.container_name == "github-cache"
        assert cache.default_ttl == 21600
        assert cache.use_cache is True
        assert cache._initialized is False

    def test_init_with_custom_parameters(self):
        cache = CacheManager(container_name="custom", default_ttl=900, use_cache=False)

        assert cache.container_name == "custom"
        assert cache.default_ttl == 900
        assert cache.use_cache is False

    @patch("cloudfolio_shared.cache.cache_manager.BlobServiceClient")
    @patch("cloudfolio_shared.cache.cache_manager.DefaultAzureCredential")
    def test_ensure_initialized_uses_managed_identity(self, mock_credential, mock_blob_client, mock_env_vars):
        service_client = MagicMock()
        mock_blob_client.return_value = service_client

        cache = CacheManager()
        cache._ensure_initialized()

        mock_credential.assert_called_once()
        mock_blob_client.assert_called_once_with(
            account_url=mock_env_vars["BLOB_SERVICE_URI"],
            credential=mock_credential.return_value,
        )
        service_client.create_container.assert_called_once_with("github-cache")
        assert cache._initialized is True

    @patch("cloudfolio_shared.cache.cache_manager.BlobServiceClient")
    @patch("cloudfolio_shared.cache.cache_manager.DefaultAzureCredential", side_effect=Exception("MI unavailable"))
    def test_ensure_initialized_falls_back_to_connection_string(
        self,
        _mock_credential,
        mock_blob_client,
        mock_env_vars,
        monkeypatch,
    ):
        service_client = MagicMock()
        mock_blob_client.from_connection_string.return_value = service_client
        monkeypatch.delenv("BLOB_SERVICE_URI", raising=False)
        monkeypatch.delenv("AzureWebJobsStorage__blobServiceUri", raising=False)

        cache = CacheManager()
        cache._ensure_initialized()

        mock_blob_client.from_connection_string.assert_called_once_with(mock_env_vars["AzureWebJobsStorage"])
        service_client.create_container.assert_called_once_with("github-cache")
        assert cache._initialized is True

    def test_ensure_initialized_handles_missing_configuration(self, monkeypatch):
        monkeypatch.delenv("BLOB_SERVICE_URI", raising=False)
        monkeypatch.delenv("AzureWebJobsStorage__blobServiceUri", raising=False)
        monkeypatch.delenv("AzureWebJobsStorage", raising=False)

        cache = CacheManager()
        cache._ensure_initialized()

        assert cache._blob_service_client is None
        assert cache._initialized is False
        assert cache._init_failed is True


class TestCacheManagerGet:
    def _set_blob_client(self, cache: CacheManager) -> MagicMock:
        service_client = MagicMock()
        cache._blob_service_client = service_client
        cache._initialized = True
        return service_client

    def test_get_when_disabled(self):
        cache = CacheManager(use_cache=False)

        result = cache.get("any")

        assert result == {"status": "disabled", "data": None}

    def test_get_missing_entry(self):
        cache = CacheManager()
        service_client = self._set_blob_client(cache)
        blob_client = MagicMock()
        blob_client.exists.return_value = False
        service_client.get_blob_client.return_value = blob_client

        result = cache.get("missing")

        assert result == {"status": "missing", "data": None}
        service_client.get_blob_client.assert_called_once_with("github-cache", "missing")

    def test_get_valid_entry(self):
        cache = CacheManager()
        service_client = self._set_blob_client(cache)
        blob_client = MagicMock()
        service_client.get_blob_client.return_value = blob_client
        blob_client.exists.return_value = True

        properties = MagicMock()
        properties.metadata = {"fingerprint": "abc123"}
        properties.last_modified = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        properties.size = 1024
        blob_client.get_blob_properties.return_value = properties
        blob_client.download_blob.return_value.readall.return_value = json.dumps({"data": {"k": "v"}}).encode()

        result = cache.get("key")

        assert result["status"] == "valid"
        assert result["data"] == {"k": "v"}
        assert result["fingerprint"] == "abc123"
        assert result["size_bytes"] == 1024

    def test_get_expired_entry(self):
        cache = CacheManager()
        service_client = self._set_blob_client(cache)
        blob_client = MagicMock()
        service_client.get_blob_client.return_value = blob_client
        blob_client.exists.return_value = True

        expired_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        properties = MagicMock()
        properties.metadata = {"expires_at": expired_at}
        properties.last_modified = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        properties.size = 2048
        blob_client.get_blob_properties.return_value = properties

        result = cache.get("expired")

        assert result["status"] == "expired"
        blob_client.delete_blob.assert_called_once_with()
        assert result["size_bytes"] == 2048

    def test_get_handles_exceptions(self):
        cache = CacheManager()
        service_client = self._set_blob_client(cache)
        blob_client = MagicMock()
        service_client.get_blob_client.return_value = blob_client
        blob_client.exists.side_effect = RuntimeError("boom")

        result = cache.get("key")

        assert result == {"status": "error", "data": None}


class TestCacheManagerSave:
    def _prepare_blob(self, cache: CacheManager) -> MagicMock:
        service_client = MagicMock()
        blob_client = MagicMock()
        service_client.get_blob_client.return_value = blob_client
        cache._blob_service_client = service_client
        cache._initialized = True
        return blob_client

    def test_save_disabled(self):
        cache = CacheManager(use_cache=False)

        assert cache.save("key", {"value": 1}) is False

    def test_save_persists_payload(self):
        cache = CacheManager()
        blob_client = self._prepare_blob(cache)

        assert cache.save("key", {"value": 1}, ttl=60, fingerprint="abc") is True

        upload_kwargs = blob_client.upload_blob.call_args.kwargs
        metadata = upload_kwargs["metadata"]
        assert metadata["fingerprint"] == "abc"
        assert "expires_at" in metadata

    def test_save_handles_errors(self):
        cache = CacheManager()
        blob_client = self._prepare_blob(cache)
        blob_client.upload_blob.side_effect = RuntimeError("nope")

        assert cache.save("key", {"value": 1}) is False


class TestCacheManagerDelete:
    def _prepare_blob(self, cache: CacheManager) -> MagicMock:
        service_client = MagicMock()
        blob_client = MagicMock()
        service_client.get_blob_client.return_value = blob_client
        cache._blob_service_client = service_client
        cache._initialized = True
        return blob_client

    def test_delete_disabled(self):
        cache = CacheManager(use_cache=False)

        assert cache.delete("key") is False

    def test_delete_existing_entry(self):
        cache = CacheManager()
        blob_client = self._prepare_blob(cache)
        blob_client.exists.return_value = True

        assert cache.delete("key") is True
        blob_client.delete_blob.assert_called_once_with()

    def test_delete_missing_entry(self):
        cache = CacheManager()
        blob_client = self._prepare_blob(cache)
        blob_client.exists.return_value = False

        assert cache.delete("missing") is True
        blob_client.delete_blob.assert_not_called()

    def test_delete_handles_errors(self):
        cache = CacheManager()
        blob_client = self._prepare_blob(cache)
        blob_client.exists.side_effect = RuntimeError("fail")

        assert cache.delete("key") is False


class TestCacheDecorator:
    def test_returns_cached_data(self):
        cache = CacheManager()
        cache.use_cache = True
        cache.get = Mock(return_value={"status": "valid", "data": "cached"})
        cache.save = Mock()

        @cache.cache_decorator(lambda username: f"repos_{username}")
        def loader(username: str) -> str:
            return "fresh"

        assert loader("alice") == "cached"
        cache.save.assert_not_called()

    def test_populates_cache_on_miss(self):
        cache = CacheManager()
        cache.use_cache = True
        cache.get = Mock(return_value={"status": "missing", "data": None})
        cache.save = Mock(return_value=True)

        @cache.cache_decorator(lambda username: f"repos_{username}", ttl=42)
        def loader(username: str) -> str:
            return f"fresh-{username}"

        assert loader("bob") == "fresh-bob"
        cache.save.assert_called_once_with("repos_bob", "fresh-bob", ttl=42)


class TestCacheManagerCacheKeyGeneration:
    def test_bundle_key(self):
        assert CacheManager.generate_cache_key(kind="bundle", username="user") == "repos_bundle_context_user"

    def test_repo_key(self):
        assert CacheManager.generate_cache_key(kind="repo", username="user", repo="my-repo") == "repo_level_bundle_user_my-repo"

    def test_model_key(self):
        assert CacheManager.generate_cache_key(kind="model", fingerprint="abc") == "model_abc"

    def test_model_metadata_key(self):
        assert CacheManager.generate_cache_key(kind="model") == "fine_tuned_model_metadata"

    def test_missing_username_raises(self):
        with pytest.raises(ValueError):
            CacheManager.generate_cache_key(kind="bundle")

    def test_repo_missing_username_raises(self):
        with pytest.raises(ValueError):
            CacheManager.generate_cache_key(kind="repo", repo="repo")

    def test_repo_name_sanitization(self):
        key = CacheManager.generate_cache_key(kind="repo", username="user", repo="my/repo name")
        assert key == "repo_level_bundle_user_my_repo_name"


@pytest.mark.parametrize(
    "kind,username,repo,fingerprint,expected_prefix",
    [
        ("bundle", "user1", None, None, "repos_bundle_context_"),
        ("repo", "user2", "test-repo", None, "repo_level_bundle_"),
        ("model", None, None, "abc123", "model_"),
    ],
)
def test_generate_cache_key_parametrized(kind: str, username: str, repo: str, fingerprint: str, expected_prefix: str):
    kwargs: dict[str, Any] = {"kind": kind}
    if username:
        kwargs["username"] = username
    if repo:
        kwargs["repo"] = repo
    if fingerprint:
        kwargs["fingerprint"] = fingerprint

    key = CacheManager.generate_cache_key(**kwargs)
    assert key.startswith(expected_prefix)
