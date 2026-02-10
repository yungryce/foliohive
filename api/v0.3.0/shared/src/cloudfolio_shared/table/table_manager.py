"""Azure Table Storage helper for Cloudfolio metadata."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.data.tables import TableClient, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

__all__ = [
    "TableManager",
    "TableNames",
    "JobMetadataRow",
    "SessionCandidateRow",
    "RepoLanguagesRow",
    "RepoGitHubMetadataRow",
    "RepoSyncStatusRow",
    "RepoDiscoveredPathsRow",
    "RepoAPIUsageRow",
    "UserProfileRow",
    "table_manager",
]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = True

@dataclass
class TableNames:
    """Configured table names used by Cloudfolio."""

    job_metadata: str = "JobMetadata"
    session_candidates: str = "SessionCandidates"
    repo_languages: str = "RepoLanguages"
    repo_github_metadata: str = "RepoGitHubMetadata"
    repo_sync_status: str = "RepoSyncStatus"
    repo_discovered_paths: str = "RepoDiscoveredPaths"
    repo_api_usage: str = "RepoAPIUsage"
    user_profile: str = "UserProfile"

@dataclass
class SessionCandidateRow:
    """Lightweight mapping of a session to recently queried candidates.
    
    PartitionKey: session_id
    RowKey: username
    """

    session_id: str  # PartitionKey
    username: str  # RowKey
    latest_job_id: Optional[str] = None
    last_viewed_at: Optional[str] = None
    query_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass
class JobMetadataRow:
    """Job tracking - normalized schema.
    
    PartitionKey: username (enables per-user queries)
    RowKey: job_id (unique job identifier)
    """

    username: str  # PartitionKey
    job_id: str  # RowKey
    status: str = "queued" # "queued" | "syncing" | "metadata_ready" | "completed" | "failed"
    bundle_fingerprint: Optional[str] = None
    force_refresh: bool = False
    last_requeue_at: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoLanguagesRow:
    """Per-repo language statistics - normalized from RepoMetadata.languages.
    
    PartitionKey: job_id (lifecycle of operation)
    RowKey: repo_language_key format "{repo_name}|{language}"
    """

    job_id: str  # PartitionKey
    repo_language_key: str  # RowKey
    repo_name: str
    language: str
    bytes_count: int
    percentage: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoGitHubMetadataRow:
    """GitHub-specific metadata - normalized from RepoMetadata.metadata.
    
    Includes fingerprint for content versioning and cache invalidation.
    
    PartitionKey: username (groups all repos for a user)
    RowKey: repo_name (unique repository name)
    """

    username: str  # PartitionKey
    repo_name: str  # RowKey
    fingerprint: Optional[str] = None  # Content fingerprint for versioning
    description: Optional[str] = None
    topics: Optional[List[str]] = None
    html_url: Optional[str] = None
    homepage_url: Optional[str] = None
    stars_count: int = 0
    forks_count: int = 0
    watchers: int = 0
    open_issues: int = 0
    primary_language: Optional[str] = None
    is_fork: bool = False
    is_archived: bool = False
    license_name: Optional[str] = None
    github_created_at: Optional[str] = None
    github_updated_at: Optional[str] = None
    github_pushed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoSyncStatusRow:
    """Per-repository pipeline status for a given job.
    
    Tracks progress through: sync (metadata) → cache (files) → merge (bundle).
    Status transitions: pending → synced → cached → merged (or failed at any stage).
    
    PartitionKey: job_id (groups all repos for a job)
    RowKey: repo_name (unique repo within job)
    """

    job_id: str  # PartitionKey
    repo_name: str  # RowKey
    username: str
    status: str  # pending | synced | cached | failed
    sync_message_id: Optional[str] = None  # Queue message ID for sync job
    cache_message_id: Optional[str] = None  # Queue message ID for cache job
    error: Optional[str] = None
    synced_at: Optional[str] = None  # When metadata sync completed
    cached_at: Optional[str] = None  # When file caching completed
    updated_at: Optional[str] = None


@dataclass
class RepoDiscoveredPathsRow:
    """Discovered file paths for a repository during cache phase.
    
    Stores discovered file paths (readme and config files) so they can be
    retrieved by api_gateway._get_repo_files_for_summary() and passed to
    repo_cache_retrieval.get_repo_files() for proper file categorization.
    
    PartitionKey: job_id (groups all repos for a job)
    RowKey: repo_name (unique repo within job)
    """

    job_id: str  # PartitionKey
    repo_name: str  # RowKey
    username: str
    discovered_paths: List[str] = field(default_factory=list)  # All discovered file paths
    readme_paths: List[str] = field(default_factory=list)  # Subset: readme files
    config_paths: List[str] = field(default_factory=list)  # Subset: config files
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoAPIUsageRow:
    """GitHub API usage tracking per operation.
    
    Tracks API calls, rate limits, and cache hits for observability and cost analysis.
    
    PartitionKey: username (enables querying all operations for a user)
    RowKey: {operation}#{timestamp}#{repo_name} (composite for uniqueness)
    """
    
    username: str  # PartitionKey
    operation_key: str  # RowKey: e.g., "freshness#2026-01-25T10:30:00#repo1"
    operation: str  # "freshness_check" | "metadata_sync" | "file_cache"
    job_id: Optional[str] = None  # Associated job (None for freshness checks)
    repo_name: Optional[str] = None  # Specific repo (None for user-level operations)
    api_calls_rest: int = 0  # REST API calls made
    api_calls_graphql: int = 0  # GraphQL API calls made
    cache_hits: int = 0  # Number of cache hits (avoided API calls)
    rate_limit_remaining: Optional[int] = None  # Remaining rate limit after operation
    rate_limit_reset: Optional[str] = None  # When rate limit resets (ISO timestamp)
    error: Optional[str] = None  # Error message if operation failed
    created_at: Optional[str] = None  # When operation started


@dataclass
class UserProfileRow:
    """Cached GitHub user profile (GET /users/{username}).

    PartitionKey: username
    RowKey: profile_key (constant "profile" for MVP)
    """

    username: str  # PartitionKey
    profile_key: str = "profile"  # RowKey
    github_id: Optional[int] = None
    name: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    blog: Optional[str] = None
    email: Optional[str] = None
    twitter_username: Optional[str] = None
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None
    public_repos: int = 0
    public_gists: int = 0
    followers: int = 0
    following: int = 0
    github_created_at: Optional[str] = None
    github_updated_at: Optional[str] = None
    fingerprint: Optional[str] = None
    cached_at: Optional[str] = None
    updated_at: Optional[str] = None


_AZURE_META_FIELDS = {"etag", "odata.etag", "odata.metadata"}
_REPO_STATUS_ALLOWED = {"pending", "synced", "cached", "failed"}


def _utcnow_iso() -> str: 
    return datetime.now(timezone.utc).isoformat()


def _azure_safe_timestamp(iso_timestamp: Optional[str] = None) -> str:
    r"""Return Azure Table-safe timestamp (no :, /, \, #, ? characters).
    
    Args:
        iso_timestamp: ISO format timestamp string. If None, uses current UTC time.
    
    Returns:
        Sanitized timestamp with : replaced by - and + replaced by _
    """
    if iso_timestamp is None:
        iso_timestamp = _utcnow_iso()
    return iso_timestamp.replace(":", "-").replace("+", "_")


def _restore_iso_timestamp(safe_timestamp: Optional[str]) -> Optional[str]:
    """Restore ISO format from Azure Table-safe timestamp.
    
    Converts sanitized timestamp back to valid ISO 8601 format for JSON serialization.
    
    Args:
        safe_timestamp: Sanitized timestamp with - for : and _ for +
    
    Returns:
        ISO format timestamp string, or None if input is None/empty
    """
    if not safe_timestamp:
        return None
    
    # First handle timezone indicator: _ → +
    restored = safe_timestamp.replace("_", "+")
    
    # Split into date and time portions at "T"
    if "T" in restored:
        date_part, time_part = restored.split("T", 1)
        # Date part already has correct hyphens (2026-01-26), leave it unchanged
        # Time part needs hyphens converted to colons (03-47-23Z → 03:47:23Z)
        time_part = time_part.replace("-", ":")
        restored = f"{date_part}T{time_part}"
    
    return restored


def _safe_json_dump(value: Any) -> str:
    return json.dumps({} if value is None else value, separators=(",", ":"))


def _safe_json_dump_limited(value: Any, *, max_chars: int = 32000, label: str = "") -> str:
    """Serialize JSON with a conservative size limit.

    Azure Table Storage caps string properties at 64KB (UTF-16), which is roughly
    32K characters. When exceeded, we drop the value to keep the write durable.
    """

    payload = {} if value is None else value
    text = json.dumps(payload, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    logger.warning(
        "table-manager: dropping oversized JSON field %s (%d chars > %d)",
        label or "<unknown>",
        len(text),
        max_chars,
    )
    return "{}"


class TableManager:
    """High-level helper that wraps Azure Data Tables usage."""

    def __init__(
        self,
        *,
        table_service_client: Optional[TableServiceClient] = None,
        table_names: Optional[TableNames] = None,
    ) -> None:
        self.table_names = table_names or TableNames(
            job_metadata=os.getenv("TABLE_JOB_METADATA", TableNames.job_metadata),
            session_candidates=os.getenv("TABLE_SESSION_CANDIDATES", TableNames.session_candidates),
        )

        self._service_client = table_service_client or self._create_service_client()
        self._tables: Dict[str, TableClient] = {}
        if self._service_client:
            self._ensure_tables_exist()
        else:
            logger.warning("TableManager disabled (no TableServiceClient available)")

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------
    def _create_service_client(self) -> Optional[TableServiceClient]:
        account_url = os.getenv("TABLE_SERVICE_URI") or os.getenv("AzureWebJobsStorage__tableServiceUri")
        connection_string = os.getenv("TABLE_STORAGE_CONNECTION_STRING") or os.getenv("AzureWebJobsStorage")

        if account_url:
            try:
                credential = DefaultAzureCredential()
                return TableServiceClient(endpoint=account_url, credential=credential)
            except (ClientAuthenticationError, HttpResponseError) as exc:
                logger.error("Managed identity table auth failed: %s", exc)
            except Exception as exc:
                logger.error("Unexpected table auth error: %s", exc)

        if connection_string:
            try:
                return TableServiceClient.from_connection_string(connection_string)
            except Exception as exc:
                logger.error("TableServiceClient connection error: %s", exc)

        return None

    def _ensure_tables_exist(self) -> None:
        if not self._service_client:
            return
        for name in (
            self.table_names.job_metadata,
            self.table_names.session_candidates,
            self.table_names.repo_languages,
            self.table_names.repo_github_metadata,
            self.table_names.repo_sync_status,
            self.table_names.repo_discovered_paths,
            self.table_names.repo_api_usage,
            self.table_names.user_profile,
        ):
            try:
                client = self._service_client.get_table_client(name)
                client.create_table()
                logger.info("Ensured table %s", name)
            except ResourceExistsError:
                pass
            except Exception as exc:  # pragma: no cover - safety net
                logger.warning("Unable to create table %s: %s", name, exc)

    def _get_table_client(self, table_name: str) -> Optional[TableClient]:
        if not self._service_client:
            return None
        client = self._tables.get(table_name)
        if client is None:
            client = self._service_client.get_table_client(table_name)
            self._tables[table_name] = client
        return client


    # ------------------------------------------------------------------
    # Session candidates schema parsing
    # ------------------------------------------------------------------

    def upsert_session_candidate(self, session_id: str, username: str, job_id: Optional[str]) -> None:
        """Upsert session candidate with FK validation.
        
        PartitionKey: session_id
        RowKey: username
        Validates: job_id exists in JobMetadata if provided
        Increments: query_count on each upsert
        """
        table = self._get_table_client(self.table_names.session_candidates)
        if not table:
            return
        if not session_id or not username:
            return

        # Validate FK: if job_id provided, verify it exists in JobMetadata
        if job_id:
            job = self.get_job_metadata(username, job_id)
            if not job:
                raise ValueError(f"Invalid job_id '{job_id}' for user '{username}' - job not found in JobMetadata")
            logger.info(
                "[TABLE_VALIDATE_SESSION_FK] session=%s user=%s job=%s - FK validation passed",
                session_id,
                username,
                job_id,
            )

        now = _azure_safe_timestamp()
        existing_count = 0
        created_at = now
        try:
            existing = table.get_entity(partition_key=session_id, row_key=username)
            existing_count = int(existing.get("query_count", 0) or 0)
            created_at = existing.get("created_at") or created_at
        except ResourceNotFoundError:
            pass

        entity = {
            "PartitionKey": session_id,
            "RowKey": username,
            "latest_job_id": job_id or "",
            "last_viewed_at": now,
            "query_count": existing_count + 1,
            "created_at": created_at,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)
        logger.info(
            "[TABLE_UPSERT_SESSION_CANDIDATE] session=%s user=%s job=%s query_count=%d",
            session_id,
            username,
            job_id or "<none>",
            existing_count + 1,
        )

    def list_session_candidates(self, session_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        """List candidate history for a session.
        
        PartitionKey: session_id
        Returns: List of deserialized candidates sorted by last_viewed_at (most recent first)
        """
        table = self._get_table_client(self.table_names.session_candidates)
        if not table or not session_id:
            return []

        query = list(table.list_entities(filter=f"PartitionKey eq '{session_id}'"))
        rows = [self._deserialize_session_candidate(e) for e in query]
        rows.sort(key=lambda row: row.get("last_viewed_at") or row.get("updated_at") or "", reverse=True)
        if limit and limit > 0:
            return rows[:limit]
        return rows

    def _deserialize_session_candidate(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["latest_job_id"] = payload.get("latest_job_id") or None
        payload["last_viewed_at"] = payload.get("last_viewed_at") or None
        payload["query_count"] = int(payload.get("query_count", 0) or 0)
        payload["created_at"] = payload.get("created_at") or None
        payload["updated_at"] = payload.get("updated_at") or None

        # Map Azure table keys to application fields
        payload["username"] = payload.pop("RowKey", None)
        payload["session_id"] = payload.pop("PartitionKey", None)
        return payload


    # ------------------------------------------------------------------
    # Job metadata
    # ------------------------------------------------------------------
    def upsert_job_metadata(self, row: JobMetadataRow) -> None:
        """Insert or update job metadata.
        
        PartitionKey: username
        RowKey: job_id
        Required fields: username, job_id
        """
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return
        logger.info(
            "[TABLE_UPSERT_JOB_METADATA] user=%s job=%s status=%s",
            row.username,
            row.job_id,
            row.status,
        )
        now = _azure_safe_timestamp()
        entity = {
            "PartitionKey": row.username,
            "RowKey": row.job_id,
            "status": row.status,
            "bundle_fingerprint": row.bundle_fingerprint or "",
            "force_refresh": bool(row.force_refresh),
            "last_requeue_at": _azure_safe_timestamp(row.last_requeue_at) if row.last_requeue_at else "",
            "trace_id": row.trace_id or "",
            "request_id": row.request_id or "",
            "created_at": _azure_safe_timestamp(row.created_at) if row.created_at else now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)


    def update_job_metadata(self, username: str, job_id: str, updates: Dict[str, Any]) -> None:
        """Update job metadata fields with partial updates (MERGE mode).
        
        Use for incremental status transitions and timestamp updates without
        requiring full JobMetadataRow construction. Validates job exists first.
        
        Args:
            username: Job owner
            job_id: Job identifier
            updates: Dict of field names to values (e.g., {"status": "completed"})
        
        Raises:
            ValueError: If job doesn't exist
        """
        table = self._get_table_client(self.table_names.job_metadata)
        if not table or not updates:
            return
        
        # Validate job exists before updating
        existing = self.get_job_metadata(username, job_id)
        if not existing:
            raise ValueError(f"Cannot update non-existent job: username={username}, job_id={job_id}")
        
        logger.info(
            "[TABLE_UPDATE_JOB_METADATA] user=%s job=%s keys=%s",
            username,
            job_id,
            sorted(list(updates.keys())),
        )
        
        entity: Dict[str, Any] = {
            "PartitionKey": username,
            "RowKey": job_id,
            "updated_at": _azure_safe_timestamp()
        }
        for key, value in updates.items():
            if key.endswith("_at") and value:
                entity[key] = _azure_safe_timestamp(value)
            else:
                entity[key] = value if value is not None else ""
        
        table.upsert_entity(entity, mode=UpdateMode.MERGE)


    def get_job_metadata(self, username: str, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve job metadata by username and job_id.
        
        PartitionKey: username
        RowKey: job_id
        Returns: Deserialized job dict or None if not found
        """
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=username, row_key=job_id)
        except ResourceNotFoundError:
            return None
        logger.info("[TABLE_GET_JOB_METADATA] user=%s job=%s found=true", username, job_id)
        return self._deserialize_job_metadata(entity)

    def list_jobs_metadata(self, username: str) -> List[Dict[str, Any]]:
        """List all jobs for a user.
        
        PartitionKey: username
        Returns: List of deserialized job dicts
        """
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return []
        query = list(table.list_entities(filter=f"PartitionKey eq '{username}'"))
        jobs = [self._deserialize_job_metadata(e) for e in query]
        logger.info("[TABLE_LIST_JOBS_METADATA] user=%s found=%d", username, len(jobs))

        return jobs

    def list_jobs_metadata_by_status(
        self,
        statuses: Iterable[str],
        *,
        updated_before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List jobs across all users filtered by status and optionally by updated_at.
        
        Warning: Table scan operation - no PartitionKey filtering
        Returns: List of deserialized job dicts matching criteria
        """
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return []
        status_list = [status for status in (statuses or []) if status]
        if not status_list:
            return []
        status_filters = [f"status eq '{status}'" for status in status_list]
        filters = [f"({' or '.join(status_filters)})"]
        if updated_before:
            filters.append(f"updated_at lt '{_azure_safe_timestamp(updated_before)}'")
        filter_str = " and ".join(filters)
        query = list(table.list_entities(filter=filter_str))
        return [self._deserialize_job_metadata(e) for e in query]

    def _deserialize_job_metadata(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.get("PartitionKey")
        payload["job_id"] = payload.get("RowKey")
        payload["bundle_fingerprint"] = payload.get("bundle_fingerprint") or None
        payload["trace_id"] = payload.get("trace_id") or None
        payload["request_id"] = payload.get("request_id") or None
        payload["force_refresh"] = bool(payload.get("force_refresh"))
        for key in list(payload.keys()):
            if key.endswith("_at"):
                payload[key] = _restore_iso_timestamp(payload.get(key))
        return payload


    # ------------------------------------------------------------------
    # Repo GitHub metadata
    # ------------------------------------------------------------------

    def upsert_repo_github_metadata(self, row: RepoGitHubMetadataRow) -> None:
        """Store GitHub API metadata for a repository."""
        table = self._get_table_client(self.table_names.repo_github_metadata)
        if not table:
            return
        now = _azure_safe_timestamp()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": row.repo_name,
            "fingerprint": row.fingerprint,
            "description": (row.description or "")[:4096],
            "topics": _safe_json_dump_limited(row.topics or [], label="github_metadata.topics"),
            "html_url": (row.html_url or "")[:2048],
            "homepage_url": (row.homepage_url or "")[:2048],
            "stars_count": int(row.stars_count or 0),
            "forks_count": int(row.forks_count or 0),
            "watchers": int(row.watchers or 0),
            "open_issues": int(row.open_issues or 0),
            "is_fork": bool(row.is_fork),
            "is_archived": bool(row.is_archived),
            "primary_language": (row.primary_language or "")[:128],
            "license_name": (row.license_name or "")[:256],
            "github_created_at": _azure_safe_timestamp(row.github_created_at) if row.github_created_at else None,
            "github_updated_at": _azure_safe_timestamp(row.github_updated_at) if row.github_updated_at else None,
            "github_pushed_at": _azure_safe_timestamp(row.github_pushed_at) if row.github_pushed_at else None,
            "created_at": _azure_safe_timestamp(row.created_at) if row.created_at else now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.REPLACE)
        logger.info("[TABLE_UPSERT_GITHUB_METADATA] user=%s repo=%s", row.username, row.repo_name)

    def get_repo_github_metadata(self, username: str, repo_name: str) -> Optional[Dict[str, Any]]:
        """Get GitHub metadata for a repository.
        
        PartitionKey: username
        RowKey: repo_name
        Returns: Deserialized metadata dict or None if not found
        """
        table = self._get_table_client(self.table_names.repo_github_metadata)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=username, row_key=repo_name)
        except ResourceNotFoundError:
            return None
        return self._deserialize_repo_github_metadata(entity)

    def query_repo_github_metadata(self, username: str) -> List[Dict[str, Any]]:
        """Query all GitHub metadata for a user's repositories.
        
        PartitionKey: username
        Returns: List of deserialized metadata dicts for all user repositories
        """
        table = self._get_table_client(self.table_names.repo_github_metadata)
        if not table:
            return []
        filter_str = f"PartitionKey eq '{username}'"
        entities = list(table.list_entities(filter=filter_str))
        return [self._deserialize_repo_github_metadata(e) for e in entities]

    def _deserialize_repo_github_metadata(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.pop("PartitionKey", None)
        payload["repo_name"] = payload.pop("RowKey", None)
        try:
            payload["topics"] = json.loads(payload.get("topics") or "[]")
        except json.JSONDecodeError:
            payload["topics"] = []
        
        # Ensure numeric fields are properly typed
        payload["stars_count"] = int(payload.get("stars_count", 0))
        payload["forks_count"] = int(payload.get("forks_count", 0))
        
        # Restore ISO format timestamps from Azure-safe format
        for ts_field in ["github_created_at", "github_updated_at", "github_pushed_at", "created_at", "updated_at"]:
            if ts_field in payload:
                payload[ts_field] = _restore_iso_timestamp(payload[ts_field])
        
        return payload


    # ------------------------------------------------------------------
    # Repo sync status (per job, per repo)
    # ------------------------------------------------------------------
    def upsert_repo_status(self, row: RepoSyncStatusRow) -> None:
        """Insert or update repository sync status.
        
        PartitionKey: job_id
        RowKey: repo_name
        Required fields: job_id, repo_name, username, status
        Validates: status must be in ['pending', 'synced', 'cached', 'failed']
        """
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table:
            return
        status = (row.status or "").strip().lower()
        if status not in _REPO_STATUS_ALLOWED:
            raise ValueError(f"Invalid repo sync status: {status}")

        now = _azure_safe_timestamp()
        entity: Dict[str, Any] = {
            "PartitionKey": row.job_id,
            "RowKey": row.repo_name,
            "username": row.username,
            "status": status,
            "sync_message_id": row.sync_message_id or "",
            "cache_message_id": row.cache_message_id or "",
            "error": row.error or "",
            "synced_at": _azure_safe_timestamp(row.synced_at) if row.synced_at else "",
            "cached_at": _azure_safe_timestamp(row.cached_at) if row.cached_at else "",
            "updated_at": _azure_safe_timestamp(row.updated_at) if row.updated_at else now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)
        logger.info(
            "[TABLE_UPSERT_REPO_STATUS] job=%s user=%s repo=%s status=%s",
            row.job_id,
            row.username,
            row.repo_name,
            status,
        )

    def get_repo_status(self, job_id: str, repo_name: str) -> Optional[Dict[str, Any]]:
        """Get sync status for a specific repository in a job.
        
        PartitionKey: job_id
        RowKey: repo_name
        Returns: Deserialized status dict or None if not found
        """
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=job_id, row_key=repo_name)
        except ResourceNotFoundError:
            return None
        logger.info("[TABLE_GET_REPO_STATUS] job=%s repo=%s found=true", job_id, repo_name)
        return self._deserialize_repo_status(entity)

    def list_repo_statuses(self, job_id: str) -> List[Dict[str, Any]]:
        """List all repository sync statuses for a job.
        
        PartitionKey: job_id
        Returns: List of deserialized status dicts for all repositories in the job
        """
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table:
            return []
        query = table.list_entities(filter=f"PartitionKey eq '{job_id}'")
        results = list(query)
        logger.info("[TABLE_LIST_REPO_STATUSES] job=%s found=%d", job_id, len(results))
        return [self._deserialize_repo_status(e) for e in results]

    def update_repo_status(self, job_id: str, repo_name: str, updates: Dict[str, Any]) -> None:
        """Update repo sync status fields with partial updates (MERGE mode).
        
        Use for incremental status transitions and timestamp updates without
        requiring full RepoSyncStatusRow construction. Validates status exists first.
        
        Args:
            job_id: Job identifier (PartitionKey)
            repo_name: Repository name (RowKey)
            updates: Dict of field names to values (e.g., {"status": "cached", "cached_at": "..."})
        
        Raises:
            ValueError: If repo status doesn't exist or invalid status provided
        """
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table or not updates:
            return
        
        # Validate status exists before updating
        existing = self.get_repo_status(job_id, repo_name)
        if not existing:
            raise ValueError(f"Cannot update non-existent repo status: job_id={job_id}, repo_name={repo_name}")
        
        # Validate status value if provided
        if "status" in updates:
            status = (updates["status"] or "").strip().lower()
            if status not in _REPO_STATUS_ALLOWED:
                raise ValueError(f"Invalid repo sync status: {status}")
            updates["status"] = status
        
        logger.info(
            "[TABLE_UPDATE_REPO_STATUS] job=%s repo=%s keys=%s",
            job_id,
            repo_name,
            sorted(list(updates.keys())),
        )
        
        entity: Dict[str, Any] = {
            "PartitionKey": job_id,
            "RowKey": repo_name,
            "updated_at": _azure_safe_timestamp()
        }
        timestamp_fields = {"synced_at", "cached_at", "updated_at"}
        for key, value in updates.items():
            if key in timestamp_fields and value:
                entity[key] = _azure_safe_timestamp(value)
            else:
                entity[key] = value if value is not None else ""
        
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def _deserialize_repo_status(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["job_id"] = payload.get("PartitionKey", None)
        payload["repo_name"] = payload.get("RowKey", None)
        payload["username"] = payload.get("username") or None
        payload["status"] = (payload.get("status") or "").lower() or None
        payload["sync_message_id"] = payload.get("sync_message_id") or None
        payload["cache_message_id"] = payload.get("cache_message_id") or None
        payload["synced_at"] = _restore_iso_timestamp(payload.get("synced_at"))
        payload["cached_at"] = _restore_iso_timestamp(payload.get("cached_at"))
        payload["error"] = payload.get("error") or None
        payload["updated_at"] = _restore_iso_timestamp(payload.get("updated_at"))
        return payload

    # ------------------------------------------------------------------
    # Repo discovered paths
    # ------------------------------------------------------------------
    def upsert_repo_discovered_paths(self, row: RepoDiscoveredPathsRow) -> None:
        """Persist discovered file paths for a repository.
        
        Args:
            row: RepoDiscoveredPathsRow with job_id, repo_name, username, discovered_paths
        """
        table = self._get_table_client(self.table_names.repo_discovered_paths)
        if not table:
            logger.warning("Table client unavailable for repo discovered paths")
            return
        
        now = _azure_safe_timestamp()
        entity = {
            "PartitionKey": row.job_id,
            "RowKey": row.repo_name,
            "username": row.username,
            "discovered_paths": _safe_json_dump(row.discovered_paths),
            "readme_paths": _safe_json_dump(row.readme_paths),
            "config_paths": _safe_json_dump(row.config_paths),
            "created_at": row.created_at or now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)
        logger.info(
            "[TABLE_UPSERT_DISCOVERED_PATHS] job=%s repo=%s path_count=%d",
            row.job_id, row.repo_name, len(row.discovered_paths)
        )

    def get_repo_discovered_paths(self, job_id: str, repo_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve discovered file paths for a repository.
        
        Args:
            job_id: Job ID
            repo_name: Repository name
            
        Returns:
            Dict with discovered_paths, readme_paths, config_paths or None if not found
        """
        table = self._get_table_client(self.table_names.repo_discovered_paths)
        if not table:
            return None
        
        try:
            entity = table.get_entity(job_id, repo_name)
            return self._deserialize_repo_discovered_paths(entity)
        except ResourceNotFoundError:
            return None

    def _deserialize_repo_discovered_paths(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        
        payload["job_id"] = payload.get("PartitionKey", None)
        payload["repo_name"] = payload.get("RowKey", None)
        payload["username"] = payload.get("username") or None
        
        # Parse JSON arrays
        try:
            payload["discovered_paths"] = json.loads(payload.get("discovered_paths", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            payload["discovered_paths"] = []
        
        try:
            payload["readme_paths"] = json.loads(payload.get("readme_paths", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            payload["readme_paths"] = []
        
        try:
            payload["config_paths"] = json.loads(payload.get("config_paths", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            payload["config_paths"] = []
        
        payload["created_at"] = _restore_iso_timestamp(payload.get("created_at"))
        payload["updated_at"] = _restore_iso_timestamp(payload.get("updated_at"))
        return payload

    # ------------------------------------------------------------------
    # Repo languages
    # ------------------------------------------------------------------
    def upsert_repo_languages(self, row: RepoLanguagesRow) -> None:
        """Store language statistics for a repository.
        
        PartitionKey: job_id
        RowKey: "{repo_name}|{language}"
        Required fields: job_id, repo_name, language, bytes_count
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return
        now = _azure_safe_timestamp()
        entity: Dict[str, Any] = {
            "PartitionKey": row.job_id,
            "RowKey": f"{row.repo_name}|{row.language}",
            "repo_name": row.repo_name,
            "language": row.language,
            "bytes_count": int(row.bytes_count or 0),
            "percentage": float(row.percentage or 0.0),
            "created_at": _azure_safe_timestamp(row.created_at) if row.created_at else now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def batch_upsert_repo_languages(self, rows: List[RepoLanguagesRow]) -> None:
        """Upsert language statistics directly.
        
        Simplified to iterate and upsert directly. Since job_id is partition key,
        Azure Table throughput scaling handles concurrent inserts well.
        """
        if not rows:
            return
        
        # Optimization: Reuse client if already initialized
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return
            
        success_count = 0
        for row in rows:
            try:
                self.upsert_repo_languages(row)
                success_count += 1
            except Exception as exc:
                logger.warning(
                    "[TABLE_UPSERT_LANGUAGE_FAILED] job=%s repo=%s lang=%s error=%s",
                    row.job_id, row.repo_name, row.language, exc
                )
        
        logger.info("[TABLE_UPSERT_LANGUAGES] total=%d succeeded=%d", len(rows), success_count)

    def delete_repo_languages(self, job_id: str, repo_name: str) -> None:
        """Delete language entries for a repository within a specific job.
        
        Filters by PartitionKey (job_id) and repo_name field.
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return
        
        filter_str = f"PartitionKey eq '{job_id}' and repo_name eq '{repo_name}'"
        existing = list(table.list_entities(filter=filter_str))
        
        if existing:
            # Simple individual deletion
            for e in existing:
                try:
                    table.delete_entity(partition_key=e["PartitionKey"], row_key=e["RowKey"])
                except Exception as exc:
                    logger.warning(
                        "[TABLE_DELETE_LANGUAGE_FAILED] job=%s repo=%s error=%s",
                        job_id, repo_name, exc
                    )
            logger.info("[TABLE_DELETE_REPO_LANGUAGES] job=%s repo=%s count=%d", job_id, repo_name, len(existing))

    def query_repo_languages(self, job_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Query all language statistics for a specific job.
        
        Returns a dictionary mapping repo_name to list of language dicts.
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return {}
        
        filter_str = f"PartitionKey eq '{job_id}'"
        entities = list(table.list_entities(filter=filter_str))
        
        by_repo: Dict[str, List[Dict[str, Any]]] = {}
        for entity in entities:
            deserialized = self._deserialize_repo_languages(entity)
            repo_name = deserialized.get("repo_name")
            if repo_name:
                by_repo.setdefault(repo_name, []).append(deserialized)
        
        return by_repo

    def cleanup_old_repo_languages(self, older_than_iso: str) -> int:
        """Cleanup RepoLanguages entries older than a specific timestamp.
        
        Warning: This performs a table scan filtering on Timestamp/created_at.
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return 0
            
        safe_ts = _azure_safe_timestamp(older_than_iso)
        filter_str = f"created_at lt '{safe_ts}'"
        
        count = 0
        try:
            # Query only keys to delete - convert to list first to avoid iteration during modification
            entities = list(table.list_entities(filter=filter_str, select=["PartitionKey", "RowKey"]))
            
            for e in entities:
                try:
                    table.delete_entity(partition_key=e["PartitionKey"], row_key=e["RowKey"])
                    count += 1
                except Exception as del_exc:
                    logger.warning("Failed to delete stale language row: %s", del_exc)
                
        except Exception as exc:
            logger.error("Failed to cleanup old repo languages: %s", exc)
            
        return count

    def _deserialize_repo_languages(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["job_id"] = payload.pop("PartitionKey", None)
        payload["repo_language_key"] = payload.pop("RowKey", None)
        payload["created_at"] = _restore_iso_timestamp(payload.get("created_at"))
        payload["updated_at"] = _restore_iso_timestamp(payload.get("updated_at"))
        return payload

    # ------------------------------------------------------------------
    # Repo API usage tracking
    # ------------------------------------------------------------------
    
    def upsert_api_usage(self, row: RepoAPIUsageRow) -> None:
        """Record GitHub API usage for an operation.
        
        Tracks API calls, cache hits, and rate limits for cost analysis and observability.
        """
        table = self._get_table_client(self.table_names.repo_api_usage)
        if not table:
            logger.warning("[TABLE_UPSERT_API_USAGE] No table client, skipping")
            return
        
        now = _azure_safe_timestamp()
        entity = {
            "PartitionKey": row.username,
            "RowKey": row.operation_key,
            "operation": row.operation,
            "job_id": row.job_id,
            "repo_name": row.repo_name,
            "api_calls_rest": row.api_calls_rest,
            "api_calls_graphql": row.api_calls_graphql,
            "cache_hits": row.cache_hits,
            "rate_limit_remaining": row.rate_limit_remaining,
            "rate_limit_reset": _azure_safe_timestamp(row.rate_limit_reset) if row.rate_limit_reset else "",
            "error": row.error,
            "created_at": _azure_safe_timestamp(row.created_at) if row.created_at else now,
        }
        
        logger.debug(
            "[TABLE_UPSERT_API_USAGE_ENTITY] PartitionKey=%s RowKey=%s operation=%s job_id=%s repo_name=%s",
            entity.get("PartitionKey"),
            entity.get("RowKey"),
            entity.get("operation"),
            entity.get("job_id"),
            entity.get("repo_name"),
        )
        logger.debug(
            "[TABLE_UPSERT_API_USAGE_ENTITY_FULL] %s",
            entity,
        )
        
        table.upsert_entity(entity, mode=UpdateMode.REPLACE)
        logger.info(
            "[TABLE_UPSERT_API_USAGE] user=%s operation=%s job=%s repo=%s rest=%d graphql=%d cache_hits=%d",
            row.username,
            row.operation,
            row.job_id or "<none>",
            row.repo_name or "<all>",
            row.api_calls_rest,
            row.api_calls_graphql,
            row.cache_hits,
        )
    
    def list_api_usage(
        self,
        username: str,
        *,
        job_id: Optional[str] = None,
        operation: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List API usage records for a username.
        
        Args:
            username: User to query
            job_id: Optional filter by job
            operation: Optional filter by operation type
            limit: Maximum records to return
        
        Returns:
            List of API usage records sorted by created_at descending
        """
        table = self._get_table_client(self.table_names.repo_api_usage)
        if not table:
            return []
        
        query = f"PartitionKey eq '{username}'"
        if job_id:
            query += f" and job_id eq '{job_id}'"
        if operation:
            query += f" and operation eq '{operation}'"
        
        try:
            entities = list(table.query_entities(query, results_per_page=limit))
            results = [self._deserialize_api_usage(e) for e in entities]
            # Sort by created_at descending
            results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return results[:limit]
        except Exception as exc:
            logger.error("[TABLE_LIST_API_USAGE] Query failed: %s", exc, exc_info=True)
            return []
    
    def _deserialize_api_usage(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize API usage entity."""
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.pop("PartitionKey", None)
        payload["operation_key"] = payload.pop("RowKey", None)
        payload["created_at"] = _restore_iso_timestamp(payload.get("created_at"))
        payload["rate_limit_reset"] = _restore_iso_timestamp(payload.get("rate_limit_reset"))
        return payload

    # ------------------------------------------------------------------
    # User profile
    # ------------------------------------------------------------------
    def upsert_user_profile(self, row: UserProfileRow) -> None:
        """Insert or update cached GitHub user profile.
        
        PartitionKey: username
        RowKey: profile_key (default: 'profile')
        Required fields: username
        Timestamps: github_created_at, github_updated_at, cached_at (restored from Azure-safe format)
        """
        table = self._get_table_client(self.table_names.user_profile)
        if not table:
            return
        now = _azure_safe_timestamp()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": row.profile_key or "profile",
            "github_id": int(row.github_id) if row.github_id is not None else None,
            "name": (row.name or "")[:256],
            "bio": (row.bio or "")[:4096],
            "company": (row.company or "")[:256],
            "location": (row.location or "")[:256],
            "blog": (row.blog or "")[:2048],
            "email": (row.email or "")[:256],
            "twitter_username": (row.twitter_username or "")[:64],
            "avatar_url": (row.avatar_url or "")[:2048],
            "html_url": (row.html_url or "")[:2048],
            "public_repos": int(row.public_repos or 0),
            "public_gists": int(row.public_gists or 0),
            "followers": int(row.followers or 0),
            "following": int(row.following or 0),
            "github_created_at": _azure_safe_timestamp(row.github_created_at) if row.github_created_at else "",
            "github_updated_at": _azure_safe_timestamp(row.github_updated_at) if row.github_updated_at else "",
            "fingerprint": row.fingerprint or "",
            "cached_at": _azure_safe_timestamp(row.cached_at) if row.cached_at else now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)
        logger.info("[TABLE_UPSERT_USER_PROFILE] user=%s", row.username)

    def get_user_profile(self, username: str) -> Optional[Dict[str, Any]]:
        """Retrieve user profile by username.
        
        PartitionKey: username
        RowKey: "profile" (fixed)
        Returns: Deserialized profile dict or None if not found
        """
        table = self._get_table_client(self.table_names.user_profile)
        if not table or not username:
            return None
        try:
            entity = table.get_entity(partition_key=username, row_key="profile")
        except ResourceNotFoundError:
            return None
        return self._deserialize_user_profile(entity)

    def _deserialize_user_profile(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.pop("PartitionKey", None)
        payload["profile_key"] = payload.pop("RowKey", None)
        payload["github_id"] = int(payload.get("github_id")) if payload.get("github_id") not in (None, "") else None
        for int_field in ("public_repos", "public_gists", "followers", "following"):
            payload[int_field] = int(payload.get(int_field) or 0)
        for ts_field in ("github_created_at", "github_updated_at", "cached_at", "updated_at"):
            if ts_field in payload:
                payload[ts_field] = _restore_iso_timestamp(payload.get(ts_field))
        payload["fingerprint"] = payload.get("fingerprint") or None
        for field_name in (
            "name",
            "bio",
            "company",
            "location",
            "blog",
            "email",
            "twitter_username",
            "avatar_url",
            "html_url",
        ):
            payload[field_name] = payload.get(field_name) or None
        return payload


# Global singleton matching cache_manager style
_table_manager_singleton: Optional[TableManager] = None


def get_table_manager() -> TableManager:
    """Return the lazily constructed TableManager singleton."""

    global _table_manager_singleton
    if _table_manager_singleton is None:
        _table_manager_singleton = TableManager()
    return _table_manager_singleton


# Eager singleton instance for convenience imports
table_manager = get_table_manager()
