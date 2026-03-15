"""Lightweight blob-backed cache utilities.

This module purposefully keeps the cache story simple: hot metadata now lives in
Azure Table Storage (via ``foliohive_shared.table``), while this helper
manages the remaining blob-based payloads such as repo bundles or large
intermediate artifacts. The public surface area stays compatible with the
existing callers (simple ``get``/``save`` helpers) and the implementation is
trimmed to the essentials.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .hot_cache import create_default_hot_cache

logger = logging.getLogger(__name__)

_DEFAULT_CONTAINER = "github-cache"
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
        return None


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
                return BlobServiceClient(account_url=account_url, credential=credential)
            except (ClientAuthenticationError, HttpResponseError) as exc:
                logger.warning("cache-manager: managed identity auth failed: %s", exc)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("cache-manager: unexpected managed identity error: %s", exc)

        if connection_string:
            try:
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
    def generate_cache_key(**kwargs):
        """Generate cache key for file storage.
        
        Args:
            username: GitHub username (required for file keys)
            repo: Repository name (required for file keys)
            file_type: File type (e.g., 'readme', 'config') (required for file keys)
            filename: Filename or path (required for file keys)
        
        Returns:
            Cache key string
        
        Raises:
            ValueError: If required parameters are missing
        """
        
        username = kwargs.get("username")
        repo = kwargs.get("repo")
        file_type = kwargs.get("file_type")
        filename = kwargs.get("filename")
        
        if not username:
            raise ValueError("username is required for file cache keys")
        if not repo or not file_type or not filename:
            raise ValueError("repo, file_type, and filename are required for file cache keys")
        
        safe_repo = str(repo).replace("/", "_").replace(" ", "_")
        safe_filename = str(filename).replace("/", "_").replace(" ", "_")
        return f"file_{username}_{safe_repo}_{file_type}_{safe_filename}"


    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def get(self, cache_key: str) -> Dict[str, Any]:
        if _hot_cache_enabled():
            cached_value = _HOT_CACHE.get(cache_key)
            if cached_value is not None:
                return {
                    "status": "valid",
                    "data": cached_value,
                    "last_modified": None,
                    "size_bytes": None,
                }
        self._ensure_initialized()
        if not self.use_cache or not self._blob_service_client:
            return {"status": "disabled", "data": None}

        blob_client = self._blob_service_client.get_blob_client(self.container_name, cache_key)
        try:
            if not blob_client.exists():
                return {"status": "missing", "data": None}

            properties = blob_client.get_blob_properties()
            metadata = properties.metadata or {}
            expires_at = metadata.get("expires_at")
            parsed_expiry = _parse_iso(expires_at)
            if parsed_expiry and _utcnow() >= parsed_expiry:
                try:
                    blob_client.delete_blob()
                except Exception:  # pragma: no cover - fire-and-forget
                    logger.warning("cache-manager: failed to delete expired key %s", cache_key)
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

            return {
                "status": "valid",
                "data": payload.get("data"),
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
    ) -> bool:
        if _hot_cache_enabled():
            hot_ttl = ttl if ttl is not None else self.default_ttl
            _HOT_CACHE.set(cache_key, data, ttl=hot_ttl)
        self._ensure_initialized()
        if not self.use_cache or not self._blob_service_client:
            return False

        metadata: Dict[str, str] = {}
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


    # ------------------------------------------------------------------
    # Repository file retrieval
    # ------------------------------------------------------------------
    def get_primary_readme(
        self,
        username: str,
        repo: str,
    ) -> Optional[str]:
        """Retrieve primary README content from cache.
        
        Args:
            username: GitHub username
            repo: Repository name
            
        Returns:
            README content if cached, None otherwise
        """
        key = self.generate_cache_key(
            username=username,
            repo=repo,
            file_type="readme",
            filename="PRIMARY",
        )
        result = self.get(key)
        
        if result.get("status") == "valid":
            return result.get("data")
        
        return None
    
    def get_readme_files(
        self,
        username: str,
        repo: str,
        paths: list[str],
    ) -> dict[str, str]:
        """Retrieve multiple readme files from cache.
        
        Args:
            username: GitHub username
            repo: Repository name
            paths: List of full paths to readme files
            
        Returns:
            Dict mapping path to content for successfully retrieved files
        """
        results = {}
        
        for path in paths:
            key = self.generate_cache_key(
                username=username,
                repo=repo,
                file_type="readme",
                filename=path,
            )
            result = self.get(key)
            
            if result.get("status") == "valid":
                content = result.get("data")
                if content:
                    results[path] = content
        
        return results
    
    def get_config_files(
        self,
        username: str,
        repo: str,
        paths: list[str],
    ) -> dict[str, str]:
        """Retrieve multiple config files from cache.
        
        Args:
            username: GitHub username
            repo: Repository name
            paths: List of full paths to config files
            
        Returns:
            Dict mapping filename to content for successfully retrieved files
        """
        results = {}
        
        for path in paths:
            key = self.generate_cache_key(
                username=username,
                repo=repo,
                file_type="config",
                filename=path,
            )
            result = self.get(key)
            
            if result.get("status") == "valid":
                content = result.get("data")
                if content:
                    display_name = path.split("/")[-1]
                    results[display_name] = content
        
        return results
    
    def get_repo_files(
        self,
        username: str,
        repo: str,
        *,
        discovered_paths: Optional[list[str]] = None,
        readme_candidates: Optional[list[str]] = None,
        max_readme_files: int = 3,
        max_config_files: int = 5,
        include_readme: bool = True,
    ) -> Dict[str, Any]:
        """Retrieve all cached files for a repository.
        
        This is the main retrieval method that combines primary readme,
        additional readme files, and config files.
        
        Args:
            username: GitHub username
            repo: Repository name
            discovered_paths: List of file paths discovered via path index.
                             If None, only primary readme is retrieved.
            readme_candidates: List of readme filenames to identify (e.g., ["README.md"])
            max_readme_files: Maximum number of additional readme files to retrieve
            max_config_files: Maximum number of config files to retrieve
            include_readme: Whether to retrieve primary readme content (default: True)
            
        Returns:
            Dict with keys:
            - repo_name: Repository name
            - readme_content: Primary readme content (or None) if include_readme=True
            - readme_files: Dict mapping paths to content for additional readmes
            - config_files: Dict mapping filenames to content for config files
        """
        result = {
            "repo_name": repo,
            "readme_content": None,
            "readme_files": {},
            "config_files": {},
        }
        
        # Retrieve primary readme only if requested
        if include_readme:
            result["readme_content"] = self.get_primary_readme(username, repo)
        
        # If no discovered paths provided, return with just primary readme
        if not discovered_paths:
            return result
        
        # Separate paths into readme vs config
        readme_set = set()
        if readme_candidates:
            readme_set = {name.lower() for name in readme_candidates}
        
        readme_paths = []
        config_paths = []
        
        for path in discovered_paths:
            filename = os.path.basename(path).lower()
            if filename in readme_set:
                readme_paths.append(path)
            else:
                config_paths.append(path)
        
        # Retrieve rest of readme files with limit
        if readme_paths:
            limited_readme_paths = readme_paths[:max_readme_files]
            result["readme_files"] = self.get_readme_files(
                username, repo, limited_readme_paths
            )
        
        # Retrieve config files with limit
        if config_paths:
            limited_config_paths = config_paths[:max_config_files]
            result["config_files"] = self.get_config_files(
                username, repo, limited_config_paths
            )
        
        return result




# Global instance mimicking the historic import style
cache_manager = CacheManager()
