import os
import json
import hashlib
import logging
import functools
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Callable
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings, generate_blob_sas, BlobSasPermissions
from azure.core.exceptions import HttpResponseError, ClientAuthenticationError

logger = logging.getLogger('portfolio.api')

class CacheManager:
    """
    Centralized caching system for the Portfolio API.
    
    This class provides a unified approach to managing caching across different levels:
    1. Low-Level Request Caching: Caches responses from external APIs (e.g., GitHub API)
    2. Bundle Caching: Caches repository bundles (individual and aggregated)
    3. Metadata Caching: Caches metadata for trained models
    
    Features:
    - Dynamic TTL (None for no expiration)
    - Decorator-based caching for easy application to methods
    - Azure Blob Storage backend with proper error handling
    - Change detection based on fingerprints
    
    Attributes:
        container_name (str): Name of the blob container for cache storage
        default_ttl (int): Default time-to-live for cache entries in seconds
        blob_service_client (BlobServiceClient): Azure Blob Storage client
        use_cache (bool): Whether caching is enabled
    """
    def __init__(self, container_name: str = "github-cache", default_ttl: int = 21600, use_cache: bool = True) -> None:
        """
        Initialize the cache manager with Azure Blob Storage backend.

        Args:
            container_name: The name of the blob container to use. Defaults to "github-cache".
            default_ttl: Deprecated. Kept for backward compatibility; not used for new entries.
            use_cache: Whether to enable caching functionality. Defaults to True.
        """
        self.container_name = container_name
        # default_ttl kept for backward compatibility but not used by default
        self.default_ttl = default_ttl
        self.use_cache = use_cache
        self._initialized = False
        self.blob_service_client = None
        # self._init_cache()
        
    def _ensure_initialized(self):
        """Ensure cache is initialized on first use."""
        if self._initialized:
            return
        self._init_cache()
        self._initialized = True
        
    @staticmethod
    def generate_cache_key(*args, **kwargs):
        """
        Bundle-level cache key generator.
        Produces keys for:
          - user bundle:   repos_bundle_context_{username}
          - repo bundle:   repo_context_{username}_{repo}
          - model bundle:  fine_tuned_model_metadata or model_{fingerprint}
        
        Args:
            kind/scope: The type of bundle ('repo', 'model', or 'bundle')
            username: GitHub username
            repo: Repository name (for repo bundles)
            fingerprint: Content fingerprint (for model bundles)
            
        Returns:
            A cache key string appropriate for the bundle type
        """
        kind = kwargs.get('kind') or kwargs.get('scope') or 'bundle'
        username = kwargs.get('username') or 'yungryce'
        repo = kwargs.get('repo')
        fingerprint = kwargs.get('fingerprint')

        if kind == 'repo' and repo:
            safe_repo = str(repo).replace('/', '_').replace(' ', '_')
            return f"repo_level_bundle_{username}_{safe_repo}"
        if kind == 'model':
            return f"model_{fingerprint}" if fingerprint else "fine_tuned_model_metadata"
        # default: user bundle
        return f"repos_bundle_context_{username}"
    
    def _init_cache(self) -> None:
        """
        Initialize Azure Blob Storage connection and create container if needed.

        Prefers Managed Identity via service URI; falls back to connection string for local Durable Functions.
        """
        blob_service_uri = (
            os.getenv('BLOB_SERVICE_URI')  # preferred explicit URI
            or os.getenv('AzureWebJobsStorage__blobServiceUri')  # host identity-based config
        )
        connection_string = os.getenv('AzureWebJobsStorage')  # local dev / fallback

        # Try Managed Identity first (requires blob_service_uri)
        if self.use_cache and blob_service_uri:
            try:
                # Exclude interactive for serverless; works with system/user-assigned MI or Env creds
                credential = DefaultAzureCredential()
                self.blob_service_client = BlobServiceClient(account_url=blob_service_uri, credential=credential)
                logger.info("Initialized Azure Blob client using Managed Identity.")
            except ClientAuthenticationError as auth_err:
                logger.warning(f"Managed Identity auth failed, will try connection string fallback: {auth_err}")
                self.blob_service_client = None
            except HttpResponseError as http_err:
                logger.warning(f"Managed Identity HTTP error, will try connection string fallback: {http_err}")
                self.blob_service_client = None
            except Exception as e:
                logger.warning(f"Managed Identity initialization failed, will try connection string fallback: {e}")
                self.blob_service_client = None

        # Fallback to connection string (needed for local Durable Functions)
        if (self.blob_service_client is None) and self.use_cache and connection_string:
            try:
                self.blob_service_client = BlobServiceClient.from_connection_string(connection_string)
                logger.info("Initialized Azure Blob client using connection string (fallback).")
            except Exception as e:
                logger.error(f"Connection string authentication failed: {e}")
                self.blob_service_client = None

        # Neither MI nor connection string available
        if self.blob_service_client is None:
            logger.warning("Azure Blob client not initialized (no valid credentials/URI). Caching disabled.")
            return

        # Create container if client is available
        try:
            self.blob_service_client.create_container(self.container_name)
            logger.info(f"Created cache container: {self.container_name}")
        except Exception as e:
            if "ContainerAlreadyExists" not in str(e):
                logger.warning(f"Container creation issue: {e}")

    def get(self, cache_key: str) -> Dict[str, Any]:
        """
        Retrieve cache data and metadata for a given key with expiration checking.
        
        Args:
            cache_key: The cache key to retrieve
            
        Returns:
            Dictionary containing:
            - status: 'valid', 'expired', 'missing', 'disabled', or 'error'
            - data: The cached data (None if not valid)
            - metadata: Blob metadata dictionary
            - expires_at: ISO format expiration timestamp (if available)
            - time_until_expiry_seconds: Seconds until expiration (if valid)
            - last_modified: ISO format last modified timestamp
            - size_bytes: Size of the cached data in bytes
        """
        self._ensure_initialized()
        if not self.blob_service_client or not self.use_cache:
            return {'status': 'disabled', 'data': None, 'metadata': None}
        try:
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=cache_key
            )
            if not blob_client.exists():
                return {'status': 'missing', 'data': None, 'metadata': None}

            properties = blob_client.get_blob_properties()
            metadata = properties.metadata or {}

            # If legacy expires_at exists, honor it
            expires_at_str = metadata.get('expires_at')
            if expires_at_str:
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                except Exception:
                    # If malformed, treat as expired and delete
                    expires_at = datetime.min
                if datetime.now() > expires_at:
                    try:
                        blob_client.delete_blob()
                    except Exception:
                        pass
                    return {
                        'status': 'expired',
                        'data': None,
                        'metadata': metadata,
                        'expires_at': expires_at_str,
                        'last_modified': properties.last_modified.isoformat(),
                        'size_bytes': properties.size
                    }

            # Otherwise treat as valid (no explicit no_expiry and not expired)
            data = json.loads(blob_client.download_blob().readall())
            return {
                'status': 'valid',
                'data': data.get('data'),
                'fingerprint': metadata.get('fingerprint'),
                'no_expiry': metadata.get('no_expiry') == 'True',
                'last_modified': properties.last_modified.isoformat(),
                'size_bytes': properties.size
            }
        except Exception as e:
            logger.warning(f"Error retrieving cache entry for key {cache_key}: {str(e)}")
            return {'status': 'error', 'data': None, 'metadata': None}

    def save(self, cache_key: str, data: Any, ttl: Optional[int] = None, fingerprint: Optional[str] = None) -> bool:
        """
        Save data to cache with optional TTL.
        
        Args:
            cache_key: The cache key to store data under
            data: The data to cache (must be JSON serializable)
            ttl: Time-to-live in seconds. None means no expiration.
            
        Returns:
            True if successfully saved, False otherwise
        """
        self._ensure_initialized()
        if not self.blob_service_client or not self.use_cache:
            return False
        try:
            metadata = {}
            cache_data = {
                'data': data,
                'cached_at': datetime.now().isoformat()
            }
            
            # Store fingerprint if provided
            if fingerprint:
                metadata['fingerprint'] = fingerprint
            
            # Honor TTL for backward compatibility; default to non-expiring
            if ttl is not None:
                expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(ttl))).isoformat()
                metadata['expires_at'] = expires_at

            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name, 
                blob=cache_key
            )
            blob_client.upload_blob(
                json.dumps(cache_data, separators=(',', ':')),
                overwrite=True,
                content_settings=ContentSettings(
                    content_type='application/json',
                    content_encoding='utf-8'
                ),
                metadata=metadata
            )
            return True
        except Exception as e:
            logger.warning(f"Error saving to cache for key {cache_key}: {str(e)}")
            return False

    def delete(self, cache_key: str) -> bool:
        """
        Delete a cache entry.
        
        Args:
            cache_key: The cache key to delete
            
        Returns:
            True if successfully deleted or didn't exist, False on error
        """
        self._ensure_initialized()
        if not self.blob_service_client or not self.use_cache:
            return False
        try:
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=cache_key
            )
            if blob_client.exists():
                blob_client.delete_blob()
            return True
        except Exception as e:
            logger.warning(f"Error deleting cache entry for key {cache_key}: {str(e)}")
            return False

    def cache_decorator(self, cache_key_func: Callable, ttl: Optional[int] = None):
        """
        Decorator to handle caching logic for any function.
        
        Args:
            cache_key_func: A function that generates a cache key based on function arguments
            ttl: Time-to-live in seconds. None means no expiration.
            
        Returns:
            A decorator function that wraps the target function with caching logic
            
        Example:
        ```python
        cache_manager = CacheManager(container_name="github-cache")
        
        def generate_request_cache_key(method, endpoint, params):
            return f"request:{method}:{endpoint}:{hashlib.md5(str(params).encode()).hexdigest()[:8]}"
            
        class GitHubAPI:
            @cache_manager.cache_decorator(cache_key_func=generate_request_cache_key, ttl=3600)
            def make_request(self, method: str, endpoint: str, params: Dict[str, Any]):
                # Logic for making API request
                pass
        ```
        """
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):

                # Generate cache key using only kwargs
                cache_key = cache_key_func(**kwargs)
                
                # Check cache
                cache_result = self.get(cache_key)
                if cache_result['status'] == 'valid':
                    return cache_result['data']
                
                # Cache miss or expired, call the function
                result = func(*args, **kwargs)
                
                # Save result to cache
                self.save(cache_key, result, ttl=ttl)

                return result
            return wrapper
        return decorator

    def cleanup_expired_cache(self, batch_size: int = 100, dry_run: bool = False) -> Dict[str, Any]:
        """
        Clean up expired cache entries from Azure Blob Storage.
        
        Args:
            batch_size: Number of blobs to process in each batch. Defaults to 100
            dry_run: If True, only identifies expired entries without deleting them
            
        Returns:
            Dictionary containing cleanup operation results
        """
        self._ensure_initialized()
        if not self.blob_service_client or not self.use_cache:
            logger.warning("Cache cleanup skipped: no blob service client or caching disabled")
            return {
                "status": "skipped",
                "reason": "Cache not configured",
                "expired_count": 0,
                "deleted_count": 0,
                "error_count": 0
            }
        
        try:
            # Get container client
            container_client = self.blob_service_client.get_container_client(self.container_name)
            
            # Check if container exists
            if not container_client.exists():
                logger.info(f"Cache container '{self.container_name}' does not exist")
                return {
                    "status": "completed",
                    "reason": "Container does not exist",
                    "expired_count": 0,
                    "deleted_count": 0,
                    "error_count": 0
                }
            
            # Track cleanup statistics
            total_blobs = 0
            expired_count = 0
            deleted_count = 0
            error_count = 0
            current_time = datetime.now(timezone.utc)
            
            # Process blobs in batches
            blob_batch = []
            
            for blob in container_client.list_blobs():
                total_blobs += 1
                blob_batch.append(blob)
                
                # Process batch when full
                if len(blob_batch) >= batch_size:
                    batch_result = self._process_cleanup_batch(blob_batch, current_time, dry_run)
                    expired_count += batch_result['expired']
                    deleted_count += batch_result['deleted']
                    error_count += batch_result['errors']
                    blob_batch = []
            
            # Process remaining blobs
            if blob_batch:
                batch_result = self._process_cleanup_batch(blob_batch, current_time, dry_run)
                expired_count += batch_result['expired']
                deleted_count += batch_result['deleted']
                error_count += batch_result['errors']
            
            logger.info(f"Cache cleanup completed: {expired_count} expired, {deleted_count} deleted")
            
            return {
                "status": "completed",
                "total_blobs": total_blobs,
                "expired_count": expired_count,
                "deleted_count": deleted_count,
                "error_count": error_count,
                "dry_run": dry_run,
                "cleanup_time": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error during cache cleanup: {str(e)}")
            return {
                "status": "failed",
                "reason": str(e),
                "expired_count": 0,
                "deleted_count": 0,
                "error_count": 1
            }

    def _process_cleanup_batch(self, blob_batch, current_time, dry_run):
        """Process a batch of blobs for cleanup."""
        self._ensure_initialized()
        expired = 0
        deleted = 0
        errors = 0
        
        for blob in blob_batch:
            try:
                blob_client = self.blob_service_client.get_blob_client(
                    container=self.container_name,
                    blob=blob.name
                )
                
                properties = blob_client.get_blob_properties()
                metadata = properties.metadata or {}
                
                # Skip non-expiring cache entries
                if metadata.get('no_expiry') == 'True':
                    continue
                
                # Check if expired
                if 'expires_at' in metadata:
                    try:
                        expires_at = datetime.fromisoformat(metadata['expires_at'])
                    except Exception:
                        expires_at = datetime.min.replace(tzinfo=timezone.utc)
                    if expires_at <= current_time:
                        expired += 1
                        if not dry_run:
                            blob_client.delete_blob()
                            deleted += 1
                else:
                    # No expiration metadata: fallback on age > 30 days
                    last_modified = properties.last_modified
                    age_seconds = (current_time - last_modified).total_seconds()
                    if age_seconds > 30 * 24 * 60 * 60:
                        expired += 1
                        if not dry_run:
                            blob_client.delete_blob()
                            deleted += 1
            except Exception as e:
                logger.warning(f"Error processing blob {blob.name} during cleanup: {str(e)}")
                errors += 1
        
        return {'expired': expired, 'deleted': deleted, 'errors': errors}

    def get_cache_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the cache.
        
        Returns:
            Dictionary containing cache statistics:
            - total_entries: Total number of cache entries
            - total_size_bytes: Total size of all cache entries in bytes
            - entries_by_type: Dictionary of entry counts by type (determined by prefix)
            - oldest_entry: ISO timestamp of oldest entry
            - newest_entry: ISO timestamp of newest entry
        """
        if not self.blob_service_client or not self.use_cache:
            return {
                "status": "disabled",
                "total_entries": 0,
                "total_size_bytes": 0
            }
        
        try:
            # Get container client
            container_client = self.blob_service_client.get_container_client(self.container_name)
            
            # Check if container exists
            if not container_client.exists():
                return {
                    "status": "empty",
                    "total_entries": 0,
                    "total_size_bytes": 0
                }
            
            # Track statistics
            total_entries = 0
            total_size_bytes = 0
            entries_by_type = {}
            oldest_timestamp = None
            newest_timestamp = None
            
            # Process all blobs
            for blob in container_client.list_blobs():
                total_entries += 1
                total_size_bytes += blob.size
                
                # Track by type (using prefix)
                prefix = blob.name.split('_')[0] if '_' in blob.name else 'unknown'
                entries_by_type[prefix] = entries_by_type.get(prefix, 0) + 1
                
                # Track timestamps
                if oldest_timestamp is None or blob.last_modified < oldest_timestamp:
                    oldest_timestamp = blob.last_modified
                
                if newest_timestamp is None or blob.last_modified > newest_timestamp:
                    newest_timestamp = blob.last_modified
            
            return {
                "status": "active",
                "total_entries": total_entries,
                "total_size_bytes": total_size_bytes,
                "entries_by_type": entries_by_type,
                "oldest_entry": oldest_timestamp.isoformat() if oldest_timestamp else None,
                "newest_entry": newest_timestamp.isoformat() if newest_timestamp else None
            }
        except Exception as e:
            logger.error(f"Error getting cache statistics: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "total_entries": 0,
                "total_size_bytes": 0
            }

    def get_container_client(self, container_name: str):
        self._ensure_initialized()
        if not self.blob_service_client:
            raise RuntimeError("Blob service not configured")
        return self.blob_service_client.get_container_client(container_name)

    def make_blob_sas_url(self, container_name: str, blob_name: str, minutes: int = 60) -> str:
        """
        Build a read-only URL for a blob.
        - If using an account key (conn string), generate key-based SAS.
        - If using Managed Identity, generate a user-delegation SAS.
        - If SAS cannot be created, return the base URL (works only for public containers).
        """
        self._ensure_initialized()
        if not self.blob_service_client:
            raise RuntimeError("Blob service not configured")

        account = self.blob_service_client.account_name
        base = f"https://{account}.blob.core.windows.net/{container_name}/{blob_name}"
        try:
            expiry = datetime.now(timezone.utc) + timedelta(minutes=minutes)

            # 1) Try account-key SAS (connection string auth)
            account_key = getattr(self.blob_service_client.credential, "account_key", None)
            if account_key:
                sas = generate_blob_sas(
                    account_name=account,
                    container_name=container_name,
                    blob_name=blob_name,
                    account_key=account_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=expiry,
                )
                return f"{base}?{sas}"

            # 2) Managed Identity: user-delegation SAS
            uds = self.blob_service_client.get_user_delegation_key(
                key_start_time=datetime.now(timezone.utc),
                key_expiry_time=expiry + timedelta(minutes=5)
            )
            sas = generate_blob_sas(
                account_name=account,
                container_name=container_name,
                blob_name=blob_name,
                user_delegation_key=uds,
                permission=BlobSasPermissions(read=True),
                expiry=expiry,
            )
            return f"{base}?{sas}"
        except Exception as e:
            logger.warning(f"Could not create SAS for {container_name}/{blob_name}: {e}")
            return base

# Create a global instance of CacheManager
cache_manager = CacheManager()
