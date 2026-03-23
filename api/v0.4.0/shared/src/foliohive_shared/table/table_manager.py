"""Azure Table Storage helper for foliohive metadata."""

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
    "RepoCacheSummaryRow",
    "RepoAPIUsageRow",
    "AIRequestUsageRow",
    "UserProfileRow",
    "table_manager",
    "get_table_manager",
]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = True

@dataclass
class TableNames:
    """Configured table names used by foliohive."""

    session_candidates: str = "SessionCandidates"
    job_metadata: str = "JobMetadata"
    repo_languages: str = "RepoLanguages"
    repo_github_metadata: str = "RepoGitHubMetadata"
    repo_sync_status: str = "RepoSyncStatus"
    repo_cache_summary: str = "RepoCacheSummary"
    repo_api_usage: str = "RepoAPIUsage"
    ai_request_usage: str = "AIRequestUsage"
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
    status: str = "queued" # queued | syncing | metadata_ready | caching_started | completed | failed
    last_requeue_at: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoLanguagesRow:
    """Per-repo language statistics - normalized from RepoMetadata.languages.
    
    Partitioned by {username}:{repo_name} to prevent cross-candidate collision when
    two candidates share a repo name (e.g. both have 'simple_shell').
    
    PartitionKey: {username}:{repo_name}
    RowKey: {Language} (unique language name for the repo)
    Fields: job_id, username, repo_name, language, bytes_count, percentage
    """

    username: str
    repo_name: str
    job_id: str
    language: str
    bytes_count: int
    percentage: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoGitHubMetadataRow:
    """GitHub-specific metadata - normalized from RepoMetadata.metadata.
    
    Includes fingerprint for content versioning and cache invalidation.
    Tied to a specific job_id for data provenance and freshness validation.
    
    PartitionKey: username (groups all repos for a user)
    RowKey: repo_name (unique repository name)
    Foreign Key: job_id → JobMetadataRow (only latest job per user is valid)
    """

    username: str  # PartitionKey
    repo_name: str  # RowKey
    job_id: str
    fingerprint: str  # Content fingerprint for versioning
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
    default_branch: Optional[str] = None  # Avoid redundant API calls in downstream workers
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_accessed_at: Optional[str] = None  # Track read operations for cleanup


@dataclass
class RepoSyncStatusRow:
    """Per-repository pipeline status for a given job.
    
    Tracks progress through: sync (metadata) → cache (files) → merge (bundle).
    Status transitions: pending → synced → summary_ready (or failed at any stage).

    PartitionKey: job_id (groups all repos for a job)
    RowKey: repo_name (unique repo within job)
    """

    job_id: str  # PartitionKey
    repo_name: str  # RowKey
    username: str
    status: str  # pending | synced | summary_ready | failed
    sync_message_id: Optional[str] = None  # Queue message ID for sync job
    cache_message_id: Optional[str] = None  # Queue message ID for cache job
    error: Optional[str] = None
    synced_at: Optional[str] = None  # When metadata sync completed
    cached_at: Optional[str] = None  # When file caching completed
    updated_at: Optional[str] = None


@dataclass
class RepoCacheSummaryRow:
    """Track cached repo micro-summaries with fingerprint validation.
    
    Stores metadata about generated micro-summaries for cache invalidation
    and existence verification before blob access.
    
    PartitionKey: repo_name (enables quick lookup of cache status for a repo across jobs)
    RowKey: fingerprint (unique content fingerprint for the repo version)
    """
    
    repo_name: str  # PartitionKey
    fingerprint: str  # RowKey
    job_id: str  # Foreign key to JobMetadataRow - syncing job that generated this cache
    cache_key: str  # Full blob storage key
    summary_blob_uri: Optional[str] = None  # Direct reference to blob
    cache_status: str = "valid"  # pending | valid | failed | stale | expired
    generated_at: Optional[str] = None
    accessed_at: Optional[str] = None  # Track read frequency
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
    purpose: str  # "freshness_check" | "metadata_sync" | "file_cache" | "user_profile" | "graphql_blob_batch" | etc.
    job_id: Optional[str] = None  # Associated job (None for freshness checks)
    repo_name: Optional[str] = None  # Specific repo (None for user-level operations)
    api_calls_rest: int = 0  # REST API calls made
    api_calls_graphql: int = 0  # GraphQL API calls made
    cache_hits: int = 0  # Number of cache hits (avoided API calls)
    rate_limit_remaining: Optional[int] = None  # Remaining rate limit after operation
    rate_limit_reset: Optional[str] = None  # When rate limit resets (ISO timestamp)
    error: Optional[str] = None  # Comma-separated error type summary (e.g. "timeout,rate_limited")
    error_details: Optional[str] = None  # JSON array of full error detail dicts
    created_at: Optional[str] = None  # When operation started


@dataclass
class AIRequestUsageRow:
    """AI request usage tracking per operation.

    Tracks model selection, token budget/usage, truncation, and failures for
    observability and consistency analysis.

    PartitionKey: username (enables querying all AI operations for a user)
    RowKey: {purpose}
    """

    username: str  # PartitionKey
    operation_key: str  # RowKey
    purpose: str
    provider: str = "openai"
    model_name: Optional[str] = None
    model_tier: Optional[str] = None
    job_id: Optional[str] = None
    repo_name: Optional[str] = None
    budget_completion_tokens: int = 0
    prompt_tokens_estimated: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: Optional[str] = None
    was_truncated: bool = False
    status: str = "started"  # started | completed | failed
    error: Optional[str] = None
    error_details: Optional[str] = None  # JSON array of full error detail dicts
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class UserProfileRow:
    """Cached GitHub user profile (GET /users/{username}).

    PartitionKey: username
    RowKey: profile_key (constant "profile" for MVP)
    """

    username: str  # PartitionKey
    fingerprint: str  # Content fingerprint
    profile_key: str = "profile"  # RowKey
    job_id: Optional[str] = None  # FK to JobMetadataRow — latest sync job that refreshed this profile
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
    cached_at: Optional[str] = None
    updated_at: Optional[str] = None


_AZURE_META_FIELDS = {"etag", "odata.etag", "odata.metadata"}
_REPO_STATUS_ALLOWED = {"pending", "synced", "summary_ready", "failed"}

# Job status state machine ordering (prevents status regressions across async workers)
# Transitions: queued → syncing → metadata_ready → caching_started → completed
# (failed can appear at any stage)
_JOB_STATUS_ORDER = {
    "queued": 0,
    "syncing": 1,
    "metadata_ready": 2,
    "caching_started": 3,
    "completed": 4,
    "failed": float('inf'),  # Failed can override any state
}


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
        self.table_names = table_names or TableNames()

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
            self.table_names.repo_cache_summary,
            self.table_names.repo_sync_status,
            self.table_names.repo_api_usage,
            self.table_names.ai_request_usage,
            self.table_names.user_profile,
        ):
            try:
                client = self._service_client.get_table_client(name)
                client.create_table()
            except ResourceExistsError:
                pass
            except Exception as exc:  # pragma: no cover - safety net
                logger.error("Unable to create table %s at startup: %s", name, exc)


    def _get_table_client(self, table_name: str) -> Optional[TableClient]:
        """Return a TableClient, creating the table on first access if needed.

        Acts as a self-healing fallback for cases where _ensure_tables_exist()
        failed silently at startup (e.g. Managed Identity role propagation delay).
        """
        if not self._service_client:
            return None
        client = self._tables.get(table_name)
        if client is None:
            client = self._service_client.get_table_client(table_name)
            try:
                client.create_table()
            except ResourceExistsError:
                pass
            except Exception as exc:
                logger.warning("Unable to create table %s on first access: %s", table_name, exc)
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
        # Guard against Azurite PartitionKey filter unreliability
        rows = [r for r in rows if r.get("session_id") == session_id]
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

        now = _azure_safe_timestamp()
        entity = {
            "PartitionKey": row.username,
            "RowKey": row.job_id,
            "status": row.status,
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

    def update_job_metadata_conditional(self, username: str, job_id: str, updates: Dict[str, Any]) -> bool:
        """Update job metadata with state machine validation.
        
        Prevents status regressions across async workers by only allowing
        transitions to equal or higher states in the job workflow.
        
        State ordering: queued → syncing → metadata_ready → caching_started → completed
        Failed state can override any state at any time.
        
        Args:
            username: Job owner
            job_id: Job identifier
            updates: Dict of field names to values (must include "status" key)
        
        Returns:
            bool: True if update succeeded, False if blocked by state ordering
        
        Raises:
            ValueError: If job doesn't exist or status not in allowed set
        """
        if "status" not in updates:
            # No status update, proceed with regular update
            self.update_job_metadata(username, job_id, updates)
            return True
        
        new_status = updates["status"]
        if new_status not in _JOB_STATUS_ORDER:
            raise ValueError(f"Invalid job status: {new_status}. Allowed: {set(_JOB_STATUS_ORDER.keys())}")
        
        existing = self.get_job_metadata(username, job_id)
        if not existing:
            raise ValueError(f"Cannot update non-existent job: username={username}, job_id={job_id}")
        
        current_status = existing.get("status", "queued")
        current_order = _JOB_STATUS_ORDER.get(current_status, -1)
        new_order = _JOB_STATUS_ORDER[new_status]
        
        # Allow transition if: new status is higher/equal in order, or current/new is failed
        can_transition = (new_order >= current_order) or (new_status == "failed") or (current_status == "failed")
        
        if can_transition:
            self.update_job_metadata(username, job_id, updates)
            return True
        else:
            return False

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
        # Guard against Azurite PartitionKey filter unreliability
        return [j for j in jobs if j.get("username") == username]

    def _deserialize_job_metadata(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.get("PartitionKey")
        payload["job_id"] = payload.get("RowKey")
        payload["trace_id"] = payload.get("trace_id") or None
        payload["request_id"] = payload.get("request_id") or None
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
            "job_id": row.job_id,
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
            "last_accessed_at": _azure_safe_timestamp(row.last_accessed_at) if row.last_accessed_at else now,
        }
        table.upsert_entity(entity, mode=UpdateMode.REPLACE)


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
        rows = [self._deserialize_repo_github_metadata(e) for e in entities]
        # Guard against Azurite PartitionKey filter unreliability
        return [r for r in rows if r.get("username") == username]


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
        for ts_field in ["github_created_at", "github_updated_at", "github_pushed_at", "created_at", "updated_at", "last_accessed_at"]:
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
        rows = [self._deserialize_repo_status(e) for e in results]
        # Guard against Azurite PartitionKey filter unreliability
        return [r for r in rows if r.get("job_id") == job_id]

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
    # Repo Cache Summary
    # ------------------------------------------------------------------
    def upsert_cache_summary(self, row: RepoCacheSummaryRow) -> None:
        """Upsert cache summary entry with flexible state management.
        
        Handles both initial registration (status=pending) before generation
        and state transitions (status=valid) after successful generation.
        Uses MERGE mode for partial updates to avoid overwriting existing fields.
        
        Args:
            row: RepoCacheSummaryRow with repo_name, fingerprint, job_id, cache_key,
                 cache_status (default: 'pending'), and optional timestamp fields
        """
        table = self._get_table_client(self.table_names.repo_cache_summary)
        if not table:
            return
        
        now = _azure_safe_timestamp()
        entity: Dict[str, Any] = {
            "PartitionKey": row.repo_name,
            "RowKey": row.fingerprint,
            "job_id": row.job_id,
            "cache_key": row.cache_key,
            "cache_status": row.cache_status,
            "updated_at": now,
        }
        
        if row.summary_blob_uri:
            entity["summary_blob_uri"] = row.summary_blob_uri
        if row.generated_at:
            entity["generated_at"] = _azure_safe_timestamp(row.generated_at)
        if row.accessed_at:
            entity["accessed_at"] = _azure_safe_timestamp(row.accessed_at)
        if row.created_at:
            entity["created_at"] = _azure_safe_timestamp(row.created_at)
        
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def update_cache_summary_status(self, repo_name: str, fingerprint: str, cache_status: str) -> None:
        """Update cache summary status without overwriting other persisted fields."""
        table = self._get_table_client(self.table_names.repo_cache_summary)
        if not table:
            return

        entity: Dict[str, Any] = {
            "PartitionKey": repo_name,
            "RowKey": fingerprint,
            "cache_status": cache_status,
            "updated_at": _azure_safe_timestamp(),
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)


    def get_cache_summary(self, repo_name: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        """Get cache summary metadata for a repo fingerprint.
        
        Args:
            repo_name: Repository name (PartitionKey)
            fingerprint: Content fingerprint (RowKey)
            
        Returns:
            Deserialized cache summary dict if found, None otherwise
        """
        table = self._get_table_client(self.table_names.repo_cache_summary)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=repo_name, row_key=fingerprint)
            entity["PartitionKey"] = repo_name
            entity["RowKey"] = fingerprint
            return self._deserialize_cache_summary(entity)
        except ResourceNotFoundError:
            return None

    def _deserialize_cache_summary(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["repo_name"] = payload.get("PartitionKey", None)
        payload["fingerprint"]= payload.get("RowKey", None)
        payload["job_id"] = payload.get("job_id")
        payload["cache_key"] = payload.get("cache_key")
        payload["cache_status"] = payload.get("cache_status")
        payload["generated_at"] = _restore_iso_timestamp(payload.get("generated_at"))
        payload["accessed_at"] = _restore_iso_timestamp(payload.get("accessed_at"))
        payload["created_at"] = _restore_iso_timestamp(payload.get("created_at"))
        payload["updated_at"] = _restore_iso_timestamp(payload.get("updated_at"))
        return payload


    # ------------------------------------------------------------------
    # Repo languages
    # ------------------------------------------------------------------
    def upsert_repo_languages(self, row: RepoLanguagesRow) -> None:
        """Store language statistics for a repository.
        
        PartitionKey: {username}:{repo_name} — scoped per-candidate to prevent collision.
        RowKey: language
        Latest job's data overwrites previous job's data for same language in same repo.
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return
        now = _azure_safe_timestamp()
        entity: Dict[str, Any] = {
            "PartitionKey": f"{row.username}:{row.repo_name}",
            "RowKey": row.language,
            "username": row.username,
            "job_id": row.job_id,
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
        

    def delete_repo_languages(self, job_id: str, username: str, repo_name: str) -> None:
        """Delete all language entries for a repository.
        
        Deletes all languages for a repo regardless of job_id.
        Used before re-syncing a repo to clear old language data.
        
        PartitionKey: {username}:{repo_name}
        RowKey: language (any)
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return

        safe_pk = f"{username}:{repo_name}".replace("'", "''")
        filter_str = f"PartitionKey eq '{safe_pk}'"
        existing = list(table.list_entities(filter=filter_str, select=["PartitionKey", "RowKey"]))
        
        if existing:
            for e in existing:
                try:
                    table.delete_entity(partition_key=e["PartitionKey"], row_key=e["RowKey"])
                except Exception as exc:
                    logger.warning(
                        "[TABLE_DELETE_LANGUAGE_FAILED] username=%s repo=%s language=%s error=%s",
                        username, repo_name, e.get("RowKey"), exc
                    )

    def query_all_repo_languages(self, job_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Query all language statistics for all repos in a job.
        
        Full table scan filtering by job_id column.
        Since schema stores latest job only, this returns currently active languages.
        
        Args:
            job_id: Job ID to filter by (column-based filter)
        
        Returns:
            Dictionary mapping repo_name -> list of language dicts for that repo
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return {}

        by_repo: Dict[str, List[Dict[str, Any]]] = {}
        
        # Full table scan filtering by job_id column
        filter_str = f"job_id eq '{job_id}'"
        entities = list(table.list_entities(filter=filter_str))
        
        # Group results by repo_name; guard against Azurite column filter unreliability
        for entity in entities:
            deserialized = self._deserialize_repo_languages(entity)
            if deserialized.get("job_id") != job_id:
                continue
            repo_name = deserialized.get("repo_name")
            if repo_name:
                if repo_name not in by_repo:
                    by_repo[repo_name] = []
                by_repo[repo_name].append(deserialized)
        
        return by_repo

    def query_all_repo_languages_by_username(self, username: str) -> Dict[str, List[Dict[str, Any]]]:
        """Query all language statistics for a candidate across all jobs.

        PartitionKey prefix scan for all repos belonging to this username.
        Returns the latest language data per repo regardless of which job wrote it.
        Use this instead of query_all_repo_languages when building portfolio views
        that must include unchanged repos from previous refresh jobs.

        Args:
            username: Candidate username

        Returns:
            Dictionary mapping repo_name -> list of language dicts for that repo
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return {}

        by_repo: Dict[str, List[Dict[str, Any]]] = {}

        # PartitionKey prefix scan: all keys starting with "{username}:"
        # ";" (ASCII 59) is one code point above ":" (ASCII 58), covering all "{username}:*"
        filter_str = f"PartitionKey ge '{username}:' and PartitionKey lt '{username};'"
        entities = list(table.list_entities(filter=filter_str))

        for entity in entities:
            deserialized = self._deserialize_repo_languages(entity)
            # Guard against Azurite PartitionKey filter unreliability
            if deserialized.get("username") != username:
                continue
            repo_name = deserialized.get("repo_name")
            if repo_name:
                if repo_name not in by_repo:
                    by_repo[repo_name] = []
                by_repo[repo_name].append(deserialized)

        return by_repo

    def get_repo_languages(self, username: str, repo_name: str) -> List[Dict[str, Any]]:
        """Query language statistics for a specific repo.

        Returns all languages for a candidate's repo.
        
        PartitionKey: {username}:{repo_name}
        RowKey: language (any)
        
        Args:
            username: Candidate username (part of PartitionKey)
            repo_name: Repository name (part of PartitionKey)
            
        Returns:
            List of language dicts for the repo, empty list if none found
        """
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return []

        safe_pk = f"{username}:{repo_name}"
        filter_str = f"PartitionKey eq '{safe_pk}'"
        entities = list(table.list_entities(filter=filter_str))

        languages = []
        for entity in entities:
            deserialized = self._deserialize_repo_languages(entity)
            # Guard against Azurite PartitionKey filter unreliability
            if deserialized.get("username") == username and deserialized.get("repo_name") == repo_name:
                languages.append(deserialized)
                logger.info(
                    "[LANGUAGE_ENTRY] username=%s repo=%s language=%s bytes=%d percentage=%.2f",
                    username, repo_name, deserialized.get("language"), deserialized.get("bytes_count"), deserialized.get("percentage")
                )
        
        return languages


    def _deserialize_repo_languages(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize RepoLanguagesRow from Azure Table entity.
        
        PartitionKey = {username}:{repo_name}
        RowKey = language
        job_id = regular column (latest job that processed this language)
        """
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)

        payload["language"] = payload.pop("RowKey", None)
        pk = payload.pop("PartitionKey", "") or ""
        if ":" in pk:
            username, repo_name = pk.split(":", 1)
            payload["username"] = username
            payload["repo_name"] = repo_name
        else:
            # Legacy row written before PartitionKey schema change
            payload["username"] = payload.get("username") or None
            payload["repo_name"] = pk or None
        payload["job_id"] = payload.get("job_id") or None
        payload["bytes_count"] = int(payload.get("bytes_count", 0))
        payload["percentage"] = float(payload.get("percentage", 0.0))
        payload["created_at"] = _restore_iso_timestamp(payload.get("created_at"))
        payload["updated_at"] = _restore_iso_timestamp(payload.get("updated_at"))
        return payload


    # -------------------------------------------------------------------
    # Cleanup operations
    # -------------------------------------------------------------------

    def find_stale_candidates(self, older_than_iso: str) -> List[str]:
        """Find candidate usernames whose most recent job is older than the cutoff.

        Scans all JobMetadata rows, groups by username, and returns those whose
        latest updated_at is before older_than_iso — meaning no refresh has been
        triggered for them within the retention period.

        Args:
            older_than_iso: ISO timestamp cutoff

        Returns:
            List of stale usernames
        """
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return []

        safe_ts = _azure_safe_timestamp(older_than_iso)
        try:
            entities = list(table.list_entities(select=["PartitionKey", "updated_at", "created_at"]))
        except Exception as exc:
            logger.error("[FIND_STALE_CANDIDATES] Scan failed: %s", exc)
            return []

        latest_by_user: Dict[str, str] = {}
        for e in entities:
            username = e.get("PartitionKey")
            ts = e.get("updated_at") or e.get("created_at") or ""
            if username and ts > latest_by_user.get(username, ""):
                latest_by_user[username] = ts

        return [u for u, latest in latest_by_user.items() if latest < safe_ts]

    def cleanup_candidate_data(self, username: str) -> int:
        """Delete all table data for a candidate username.

        Deletes per-candidate rows from:
        - RepoGitHubMetadata  (PartitionKey = username)
        - RepoLanguages       (PartitionKey prefix = {username}:)
        - UserProfile         (PartitionKey = username, RowKey = "profile")
        - RepoSyncStatus      (cascade via job_ids from JobMetadata)
        - JobMetadata         (PartitionKey = username)

        Does NOT touch RepoCacheSummary (globally shared by fingerprint) or
        SessionCandidates (browsing history; stale entries auto-filter on FK
        validation at read time).

        Caller should delete username-scoped blobs before or after calling this.

        Returns:
            Number of rows deleted
        """
        count = 0

        # 1. RepoGitHubMetadata
        github_table = self._get_table_client(self.table_names.repo_github_metadata)
        if github_table:
            try:
                entities = list(github_table.list_entities(
                    filter=f"PartitionKey eq '{username}'",
                    select=["PartitionKey", "RowKey"],
                ))
                for e in entities:
                    try:
                        github_table.delete_entity(e["PartitionKey"], e["RowKey"])
                        count += 1
                    except Exception as del_exc:
                        logger.warning(
                            "[CLEANUP_CANDIDATE] github_metadata delete failed user=%s repo=%s: %s",
                            username, e.get("RowKey"), del_exc,
                        )
            except Exception as exc:
                logger.error("[CLEANUP_CANDIDATE] github_metadata scan failed user=%s: %s", username, exc)

        # 2. RepoLanguages (PartitionKey prefix scan)
        lang_table = self._get_table_client(self.table_names.repo_languages)
        if lang_table:
            try:
                filter_str = f"PartitionKey ge '{username}:' and PartitionKey lt '{username};'"
                entities = list(lang_table.list_entities(filter=filter_str, select=["PartitionKey", "RowKey"]))
                for e in entities:
                    pk = e.get("PartitionKey", "")
                    # Guard against Azurite filter unreliability
                    if not pk.startswith(f"{username}:"):
                        continue
                    try:
                        lang_table.delete_entity(e["PartitionKey"], e["RowKey"])
                        count += 1
                    except Exception as del_exc:
                        logger.warning(
                            "[CLEANUP_CANDIDATE] languages delete failed user=%s pk=%s: %s",
                            username, pk, del_exc,
                        )
            except Exception as exc:
                logger.error("[CLEANUP_CANDIDATE] languages scan failed user=%s: %s", username, exc)

        # 3. UserProfile
        profile_table = self._get_table_client(self.table_names.user_profile)
        if profile_table:
            try:
                profile_table.delete_entity(username, "profile")
                count += 1
            except ResourceNotFoundError:
                pass
            except Exception as exc:
                logger.warning("[CLEANUP_CANDIDATE] profile delete failed user=%s: %s", username, exc)

        # 4. RepoSyncStatus (cascade via job_ids) + JobMetadata
        job_table = self._get_table_client(self.table_names.job_metadata)
        status_table = self._get_table_client(self.table_names.repo_sync_status)
        if job_table:
            try:
                jobs = list(job_table.list_entities(
                    filter=f"PartitionKey eq '{username}'",
                    select=["PartitionKey", "RowKey"],
                ))
                for job_e in jobs:
                    job_id = job_e.get("RowKey")
                    if job_id and status_table:
                        try:
                            status_entities = list(status_table.list_entities(
                                filter=f"PartitionKey eq '{job_id}'",
                                select=["PartitionKey", "RowKey"],
                            ))
                            for s in status_entities:
                                try:
                                    status_table.delete_entity(s["PartitionKey"], s["RowKey"])
                                    count += 1
                                except Exception:
                                    pass
                        except Exception as exc:
                            logger.warning("[CLEANUP_CANDIDATE] status scan failed job=%s: %s", job_id, exc)
                    try:
                        job_table.delete_entity(username, job_id)
                        count += 1
                    except Exception as del_exc:
                        logger.warning(
                            "[CLEANUP_CANDIDATE] job delete failed user=%s job=%s: %s",
                            username, job_id, del_exc,
                        )
            except Exception as exc:
                logger.error("[CLEANUP_CANDIDATE] jobs scan failed user=%s: %s", username, exc)

        return count

    def cleanup_stale_cache_summaries(self, older_than_iso: str) -> List[str]:
        """Delete RepoCacheSummary rows not updated within the retention period.

        Returns the cache_keys of successfully deleted rows so the caller can also
        delete the corresponding blobs. Blobs are globally shared by (repo_name,
        fingerprint) — ownership lives here, not in candidate cleanup.

        Args:
            older_than_iso: ISO timestamp cutoff

        Returns:
            List of cache_keys for rows that were successfully deleted
        """
        table = self._get_table_client(self.table_names.repo_cache_summary)
        if not table:
            return []

        safe_ts = _azure_safe_timestamp(older_than_iso)
        deleted_keys: List[str] = []
        try:
            entities = list(table.list_entities(
                filter=f"updated_at lt '{safe_ts}'",
                select=["PartitionKey", "RowKey", "cache_key"],
            ))
            for e in entities:
                try:
                    table.delete_entity(e["PartitionKey"], e["RowKey"])
                    cache_key = e.get("cache_key")
                    if cache_key:
                        deleted_keys.append(cache_key)
                except Exception as del_exc:
                    logger.warning(
                        "[CLEANUP_CACHE_SUMMARY] delete failed repo=%s fp=%s: %s",
                        e.get("PartitionKey"), e.get("RowKey"), del_exc,
                    )
        except Exception as exc:
            logger.error("[CLEANUP_CACHE_SUMMARY] scan failed: %s", exc)

        return deleted_keys

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
            "purpose": row.purpose,
            "job_id": row.job_id,
            "repo_name": row.repo_name,
            "api_calls_rest": row.api_calls_rest,
            "api_calls_graphql": row.api_calls_graphql,
            "cache_hits": row.cache_hits,
            "rate_limit_remaining": row.rate_limit_remaining,
            "rate_limit_reset": _azure_safe_timestamp(row.rate_limit_reset) if row.rate_limit_reset else "",
            "error": row.error,
            "error_details": row.error_details,
            
            "created_at": _azure_safe_timestamp(row.created_at) if row.created_at else now,
        }
        
        table.upsert_entity(entity, mode=UpdateMode.REPLACE)
    
    def list_api_usage(
        self,
        username: str,
        *,
        job_id: Optional[str] = None,
        purpose: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List API usage records for a username.
        
        Args:
            username: User to query
            job_id: Optional filter by job
            purpose: Optional filter by purpose
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
        if purpose:
            query += f" and purpose eq '{purpose}'"
        
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
    # AI request usage tracking
    # ------------------------------------------------------------------

    def upsert_ai_request_usage(self, row: AIRequestUsageRow) -> None:
        """Record AI request usage for an operation."""
        table = self._get_table_client(self.table_names.ai_request_usage)
        if not table:
            logger.warning("[TABLE_UPSERT_AI_REQUEST_USAGE] No table client, skipping")
            return

        now = _azure_safe_timestamp()
        entity = {
            "PartitionKey": row.username,
            "RowKey": row.operation_key,
            "purpose": row.purpose,
            "provider": row.provider,
            "model_name": row.model_name,
            "model_tier": row.model_tier,
            "job_id": row.job_id,
            "repo_name": row.repo_name,
            "budget_completion_tokens": int(row.budget_completion_tokens or 0),
            "prompt_tokens_estimated": int(row.prompt_tokens_estimated or 0),
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "finish_reason": row.finish_reason,
            "was_truncated": bool(row.was_truncated),
            "status": row.status,
            "error": row.error,
            "error_details": row.error_details,
            "created_at": _azure_safe_timestamp(row.created_at) if row.created_at else now,
            "updated_at": _azure_safe_timestamp(row.updated_at) if row.updated_at else now,
        }

        table.upsert_entity(entity, mode=UpdateMode.REPLACE)


    def list_ai_request_usage(
        self,
        username: str,
        *,
        job_id: Optional[str] = None,
        purpose: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List AI request usage records for a username."""
        table = self._get_table_client(self.table_names.ai_request_usage)
        if not table:
            return []

        query = f"PartitionKey eq '{username}'"
        if job_id:
            query += f" and job_id eq '{job_id}'"
        if purpose:
            query += f" and purpose eq '{purpose}'"

        try:
            entities = list(table.query_entities(query, results_per_page=limit))
            results = [self._deserialize_ai_request_usage(e) for e in entities]
            results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return results[:limit]
        except Exception as exc:
            logger.error("[TABLE_LIST_AI_REQUEST_USAGE] Query failed: %s", exc, exc_info=True)
            return []

    def _deserialize_ai_request_usage(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize AI request usage entity."""
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.pop("PartitionKey", None)
        payload["operation_key"] = payload.pop("RowKey", None)
        payload["created_at"] = _restore_iso_timestamp(payload.get("created_at"))
        payload["updated_at"] = _restore_iso_timestamp(payload.get("updated_at"))
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
            "job_id": row.job_id or "",
            "cached_at": _azure_safe_timestamp(row.cached_at) if row.cached_at else now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

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
        payload["job_id"] = payload.get("job_id") or None
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
