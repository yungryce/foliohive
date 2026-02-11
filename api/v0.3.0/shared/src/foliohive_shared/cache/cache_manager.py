"""Lightweight blob-backed cache utilities.

This module purposefully keeps the cache story simple: hot metadata now lives in
Azure Table Storage (via ``foliohive_shared.table``), while this helper
manages the remaining blob-based payloads such as repo bundles or large
intermediate artifacts. The public surface area stays compatible with the
existing callers (simple ``get``/``save`` helpers plus the decorator used by the
GitHub client) but the implementation is trimmed to the essentials.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Optional

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .hot_cache import create_default_hot_cache

logger = logging.getLogger(__name__)

_DEFAULT_CONTAINER = "github-cache"
_BUNDLE_CACHE_PREFIX = "repos_bundle_context_"
_REPO_BUNDLE_PREFIX = "repo_level_bundle_"

_HOT_CACHE = create_default_hot_cache()


def _hot_cache_enabled() -> bool:
    return os.getenv("CF_HOT_CACHE_ENABLED", "true").lower() == "true"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.rstrip("Z") + ("+00:00" if value.endswith("Z") else "")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        logger.debug("cache-manager: unable to parse iso timestamp %s", value)
        return None


def _is_bundle_blob(name: str) -> bool:
    return name.startswith(_BUNDLE_CACHE_PREFIX) or name.startswith(_REPO_BUNDLE_PREFIX)


class CacheManager:
    """Minimal blob cache facade with optional TTL support."""

    def __init__(
        self,
        *,
        container_name: str = _DEFAULT_CONTAINER,
        default_ttl: int = 21600,
        use_cache: bool = True,
    ) -> None:
        self.container_name = container_name
        self.default_ttl = default_ttl
        self.use_cache = use_cache

        self._initialized = False
        self._init_failed = False
        self._blob_service_client: Optional[BlobServiceClient] = None

    @property
    def blob_service_client(self) -> Optional[BlobServiceClient]:
        """Maintain backward compatibility for legacy callers."""

        return self._blob_service_client

    @blob_service_client.setter
    def blob_service_client(self, value: Optional[BlobServiceClient]) -> None:
        self._blob_service_client = value

    # ------------------------------------------------------------------
    # Client bootstrap
    # ------------------------------------------------------------------
    def _ensure_initialized(self) -> None:
        if not self.use_cache:
            return
        if self._initialized or self._init_failed:
            return

        self._blob_service_client = self._create_service_client()
        if not self._blob_service_client:
            self._init_failed = True
            return

        self._ensure_container()
        self._initialized = True

    def _create_service_client(self) -> Optional[BlobServiceClient]:
        account_url = os.getenv("BLOB_SERVICE_URI") or os.getenv("AzureWebJobsStorage__blobServiceUri")
        connection_string = os.getenv("AzureWebJobsStorage")

        if account_url:
            try:
                credential = DefaultAzureCredential()
                logger.info("cache-manager: using managed identity for blob access")
                return BlobServiceClient(account_url=account_url, credential=credential)
            except (ClientAuthenticationError, HttpResponseError) as exc:
                logger.warning("cache-manager: managed identity auth failed: %s", exc)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("cache-manager: unexpected managed identity error: %s", exc)

        if connection_string:
            try:
                logger.info("cache-manager: using connection string for blob access")
                return BlobServiceClient.from_connection_string(connection_string)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("cache-manager: connection string auth failed: %s", exc)

        logger.warning("cache-manager: blob service client unavailable")
        return None

    def _ensure_container(self) -> None:
        if not self._blob_service_client:
            return
        try:
            self._blob_service_client.create_container(self.container_name)
        except Exception as exc:
            if "ContainerAlreadyExists" not in str(exc):
                logger.warning("cache-manager: failed to ensure container %s: %s", self.container_name, exc)

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------
    @staticmethod
    def generate_cache_key(*args, **kwargs):
        kind = kwargs.get("kind") or kwargs.get("scope") or "bundle"
        username = kwargs.get("username")
        repo = kwargs.get("repo")
        fingerprint = kwargs.get("fingerprint")
        file_type = kwargs.get("file_type")
        filename = kwargs.get("filename")

        if kind != "model" and not username:
            raise ValueError("Username is required to generate cache key")

        if kind == "file":
            if not repo or not file_type or not filename:
                raise ValueError("repo, file_type, and filename are required for kind='file'")
            safe_repo = str(repo).replace("/", "_").replace(" ", "_")
            safe_filename = str(filename).replace("/", "_").replace(" ", "_")
            return f"file_{username}_{safe_repo}_{file_type}_{safe_filename}"
        if kind == "repo" and repo:
            safe_repo = str(repo).replace("/", "_").replace(" ", "_")
            return f"repo_level_bundle_{username}_{safe_repo}"
        if kind == "model":
            return f"model_{fingerprint}" if fingerprint else "fine_tuned_model_metadata"
        return f"repos_bundle_context_{username}"

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def get(self, cache_key: str) -> Dict[str, Any]:
        if _hot_cache_enabled():
            cached_value = _HOT_CACHE.get(cache_key)
            if cached_value is not None:
                logger.info("cache-manager: hot cache hit key=%s", cache_key)
                return {
                    "status": "valid",
                    "data": cached_value,
                    "fingerprint": None,
                    "last_modified": None,
                    "size_bytes": None,
                }
            else:
                logger.info("cache-manager: hot cache miss key=%s", cache_key)
        self._ensure_initialized()
        if not self.use_cache or not self._blob_service_client:
            return {"status": "disabled", "data": None}

        blob_client = self._blob_service_client.get_blob_client(self.container_name, cache_key)
        try:
            if not blob_client.exists():
                logger.info("cache-manager: blob miss key=%s", cache_key)
                return {"status": "missing", "data": None}

            properties = blob_client.get_blob_properties()
            metadata = properties.metadata or {}
            expires_at = metadata.get("expires_at")
            parsed_expiry = _parse_iso(expires_at)
            if parsed_expiry and _utcnow() >= parsed_expiry:
                try:
                    blob_client.delete_blob()
                except Exception:  # pragma: no cover - fire-and-forget
                    logger.debug("cache-manager: failed to delete expired key %s", cache_key)
                return {
                    "status": "expired",
                    "data": None,
                    "metadata": metadata,
                    "expires_at": expires_at,
                    "last_modified": properties.last_modified.isoformat() if properties.last_modified else None,
                    "size_bytes": properties.size,
                }

            raw_payload = blob_client.download_blob().readall()
            payload = json.loads(raw_payload)
            logger.info(
                "cache-manager: blob hit key=%s size=%s",
                cache_key,
                properties.size,
            )
            return {
                "status": "valid",
                "data": payload.get("data"),
                "fingerprint": metadata.get("fingerprint"),
                "last_modified": properties.last_modified.isoformat() if properties.last_modified else None,
                "size_bytes": properties.size,
            }
        except Exception as exc:
            logger.warning("cache-manager: error retrieving key %s: %s", cache_key, exc)
            return {"status": "error", "data": None}

    def save(
        self,
        cache_key: str,
        data: Any,
        ttl: Optional[int] = None,
        *,
        fingerprint: Optional[str] = None,
    ) -> bool:
        if _hot_cache_enabled():
            hot_ttl = ttl if ttl is not None else self.default_ttl
            _HOT_CACHE.set(cache_key, data, ttl=hot_ttl)
            logger.info("cache-manager: hot cache set key=%s ttl=%s", cache_key, hot_ttl)
        self._ensure_initialized()
        if not self.use_cache or not self._blob_service_client:
            return False

        metadata: Dict[str, str] = {}
        if fingerprint:
            metadata["fingerprint"] = fingerprint
        if ttl is not None:
            expires_at = (_utcnow() + timedelta(seconds=int(ttl))).isoformat()
            metadata["expires_at"] = expires_at

        cache_payload = {
            "data": data,
            "cached_at": _utcnow().isoformat(),
        }

        blob_client = self._blob_service_client.get_blob_client(self.container_name, cache_key)
        try:
            blob_client.upload_blob(
                json.dumps(cache_payload, separators=(",", ":")),
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json", content_encoding="utf-8"),
                metadata=metadata,
            )
            logger.info(
                "cache-manager: blob save key=%s expires_at=%s fingerprint=%s",
                cache_key,
                metadata.get("expires_at"),
                metadata.get("fingerprint") or "<none>",
            )
            return True
        except Exception as exc:
            logger.warning("cache-manager: error saving key %s: %s", cache_key, exc)
            return False

    def delete(self, cache_key: str) -> bool:
        self._ensure_initialized()
        if not self.use_cache or not self._blob_service_client:
            return False
        blob_client = self._blob_service_client.get_blob_client(self.container_name, cache_key)
        try:
            if blob_client.exists():
                blob_client.delete_blob()
            return True
        except Exception as exc:
            logger.warning("cache-manager: error deleting key %s: %s", cache_key, exc)
            return False

    def cleanup_stale_non_bundle_blobs(
        self,
        prefixes: Iterable[str],
        *,
        max_age_hours: Optional[int] = None,
    ) -> int:
        """Delete expired or stale non-bundle blobs under the provided prefixes."""
        self._ensure_initialized()
        if not self.use_cache or not self._blob_service_client:
            return 0

        container_client = self._blob_service_client.get_container_client(self.container_name)
        deleted = 0
        now = _utcnow()
        max_age_delta = timedelta(hours=max_age_hours) if max_age_hours else None

        for prefix in prefixes:
            if not prefix:
                continue
            try:
                blobs = container_client.list_blobs(name_starts_with=prefix, include=["metadata"])
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("cache-manager: unable to list blobs for prefix %s: %s", prefix, exc)
                continue
            for blob in blobs:
                name = getattr(blob, "name", "") or ""
                if not name or _is_bundle_blob(name):
                    continue

                metadata = getattr(blob, "metadata", None) or {}
                expires_at = metadata.get("expires_at")
                parsed_expiry = _parse_iso(expires_at)
                if parsed_expiry and now >= parsed_expiry:
                    try:
                        container_client.delete_blob(name)
                        deleted += 1
                    except Exception as exc:  # pragma: no cover
                        logger.warning("cache-manager: failed to delete expired blob %s: %s", name, exc)
                    continue

                if max_age_delta:
                    last_modified = getattr(blob, "last_modified", None)
                    if last_modified and now - last_modified >= max_age_delta:
                        try:
                            container_client.delete_blob(name)
                            deleted += 1
                        except Exception as exc:  # pragma: no cover
                            logger.warning("cache-manager: failed to delete stale blob %s: %s", name, exc)
        return deleted

    # ------------------------------------------------------------------
    # Decorator helper used by GitHub client
    # ------------------------------------------------------------------
    def cache_decorator(
        self,
        cache_key_func: Callable,
        ttl: Optional[int] = None,
        *,
        on_cache_hit: Optional[Callable[[dict], None]] = None,
    ):
        def decorator(func: Callable):
            signature = inspect.signature(func)
            resolve_key = self._build_cache_key_resolver(cache_key_func)

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not self.use_cache:
                    return func(*args, **kwargs)

                bound = self._bind_arguments(signature, args, kwargs, func.__name__)
                if bound is None:
                    return func(*args, **kwargs)

                cache_key = resolve_key(bound)
                if not cache_key:
                    return func(*args, **kwargs)

                cached = self.get(cache_key)
                if cached.get("status") == "valid":
                    if on_cache_hit:
                        try:
                            on_cache_hit(bound)
                        except Exception as exc:  # pragma: no cover - defensive
                            logger.debug("cache-manager: cache hit hook failed for %s: %s", func.__name__, exc)
                    return cached.get("data")

                result = func(*args, **kwargs)
                if cached.get("status") != "disabled":
                    self.save(cache_key, result, ttl=ttl)
                return result

            return wrapper

        return decorator

    @staticmethod
    def _bind_arguments(signature: inspect.Signature, args: tuple, kwargs: dict, func_name: str) -> Optional[dict]:
        try:
            bound = signature.bind_partial(*args, **kwargs)
        except TypeError:
            logger.warning("cache-manager: failed to bind cache arguments for %s", func_name)
            return None
        bound.arguments.pop("self", None)
        bound.arguments.pop("cls", None)
        return dict(bound.arguments)

    @staticmethod
    def _build_cache_key_resolver(cache_key_func: Callable) -> Callable[[dict], Optional[str]]:
        cache_sig = inspect.signature(cache_key_func)
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in cache_sig.parameters.values())
        allowed = set(cache_sig.parameters.keys()) if not accepts_kwargs else None

        def resolver(bound_arguments: dict) -> Optional[str]:
            try:
                payload = bound_arguments if accepts_kwargs else {k: bound_arguments.get(k) for k in allowed}
                return cache_key_func(**payload)
            except Exception as exc:
                logger.warning("cache-manager: cache key generation failed: %s", exc)
                return None

        return resolver


# Global instance mimicking the historic import style
cache_manager = CacheManager()
