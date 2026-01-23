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
    "RepoMetadataRow",
    "RepoLanguagesRow",
    "RepoFileTypesRow",
    "RepoGitHubMetadataRow",
    "RepoSyncStatusRow",
    "ModelMetadataRow",
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
    repo_metadata: str = "RepoMetadata"
    repo_languages: str = "RepoLanguages"
    repo_file_types: str = "RepoFileTypes"
    repo_github_metadata: str = "RepoGitHubMetadata"
    model_metadata: str = "ModelMetadata"
    repo_sync_status: str = "RepoSyncStatus"


@dataclass
class JobMetadataRow:
    """Job tracking - normalized schema.
    """

    username: str
    job_id: str
    status: str = "queued"
    bundle_fingerprint: Optional[str] = None
    force_refresh: bool = False
    merge_enqueued_at: Optional[str] = None
    last_requeue_at: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class SessionCandidateRow:
    """Lightweight mapping of a session to recently queried candidates."""

    session_id: str
    username: str
    latest_job_id: Optional[str] = None
    last_viewed_at: Optional[str] = None
    query_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoMetadataRow:
    """Lightweight per-repository metadata - normalized schema.
    """

    username: str
    repo_name: str
    fingerprint: Optional[str]
    content_blob: Optional[str] = None
    has_documentation: Optional[bool] = None
    readme_excerpt: Optional[str] = None
    created_at: Optional[str] = None
    last_synced_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoLanguagesRow:
    """Per-repo language statistics - normalized from RepoMetadata.languages."""

    username: str  # PartitionKey
    repo_language_key: str  # RowKey: "{repo_name}#{language}"
    repo_name: str
    language: str
    bytes: int
    percentage: Optional[float] = None


@dataclass
class RepoFileTypesRow:
    """Per-repo file type categorization - normalized from RepoMetadata.categorized_types."""

    username: str  # PartitionKey
    repo_type_key: str  # RowKey: "{repo_name}#{category}"
    repo_name: str
    category: str
    file_path: str
    file_type: str


@dataclass
class RepoGitHubMetadataRow:
    """GitHub-specific metadata - normalized from RepoMetadata.metadata."""

    username: str  # PartitionKey
    repo_name: str  # RowKey
    full_name: Optional[str] = None
    description: Optional[str] = None
    html_url: Optional[str] = None
    homepage: Optional[str] = None
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    watchers: int = 0
    default_branch: Optional[str] = None
    is_private: bool = False
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
    """Per-repository sync status for a given job."""

    job_id: str
    repo_name: str
    username: str
    status: str  # synced | failed | pending
    message_id: Optional[str] = None
    error: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ModelMetadataRow:
    """Metadata describing trained semantic models."""

    username: str
    fingerprint: Optional[str] = None
    experiment_name: str = "default"
    status: str = "pending"
    artifact_blob: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    training_params: Dict[str, Any] = field(default_factory=dict)
    repos_count: int = 0
    repo_names: List[str] = field(default_factory=list)
    trained_at: Optional[str] = None
    updated_at: Optional[str] = None
    model_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        if self.model_fingerprint and not self.fingerprint:
            self.fingerprint = self.model_fingerprint
        if self.fingerprint and not self.model_fingerprint:
            self.model_fingerprint = self.fingerprint
        if not self.fingerprint:
            raise ValueError("fingerprint or model_fingerprint must be provided")


_JSON_FIELDS_MODEL = {"metadata", "training_params", "repo_names"}
_AZURE_META_FIELDS = {"etag", "odata.etag", "odata.metadata"}
_REPO_STATUS_ALLOWED = {"synced", "failed", "pending"}


def _utcnow_iso() -> str: 
    return datetime.now(timezone.utc).isoformat()


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
            repo_metadata=os.getenv("TABLE_REPO_METADATA", TableNames.repo_metadata),
            model_metadata=os.getenv("TABLE_MODEL_METADATA", TableNames.model_metadata),
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
            self.table_names.repo_metadata,
            self.table_names.repo_languages,
            self.table_names.repo_file_types,
            self.table_names.repo_github_metadata,
            self.table_names.model_metadata,
            self.table_names.repo_sync_status,
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
    # Session candidates
    # ------------------------------------------------------------------

    def upsert_session_candidate(self, session_id: str, username: str, job_id: Optional[str]) -> None:
        table = self._get_table_client(self.table_names.session_candidates)
        if not table:
            return
        if not session_id or not username:
            return

        now = _utcnow_iso()
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
        table = self._get_table_client(self.table_names.session_candidates)
        if not table or not session_id:
            return []

        query = table.list_entities(filter=f"PartitionKey eq '{session_id}'")
        rows = [self._deserialize_session_candidate(e) for e in query]
        rows.sort(key=lambda row: row.get("last_viewed_at") or row.get("updated_at") or "", reverse=True)
        if limit and limit > 0:
            return rows[:limit]
        return rows

    def update_candidate_session(self, username: str, job_id: str, updates: Dict[str, Any]) -> None:
        """Update job metadata fields - no list fields in normalized schema."""
        table = self._get_table_client(self.table_names.job_metadata)
        if not table or not updates:
            return
        logger.info(
            "[TABLE_UPDATE_SESSION] user=%s job=%s keys=%s",
            username,
            job_id,
            sorted(list(updates.keys())),
        )
        entity: Dict[str, Any] = {"PartitionKey": username, "RowKey": job_id, "updated_at": _utcnow_iso()}
        for key, value in updates.items():
            entity[key] = value if value is not None else ""
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

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
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return
        logger.info(
            "[TABLE_UPSERT_JOB_METADATA] user=%s job=%s status=%s",
            row.username,
            row.job_id,
            row.status,
        )
        now = _utcnow_iso()
        entity = {
            "PartitionKey": row.username,
            "RowKey": row.job_id,
            "status": row.status,
            "bundle_fingerprint": row.bundle_fingerprint or "",
            "force_refresh": bool(row.force_refresh),
            "merge_enqueued_at": row.merge_enqueued_at or "",
            "last_requeue_at": row.last_requeue_at or "",
            "trace_id": row.trace_id or "",
            "request_id": row.request_id or "",
            "created_at": row.created_at or now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)


    def get_job_metadata(self, username: str, job_id: str) -> Optional[Dict[str, Any]]:
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
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return []
        query = table.list_entities(filter=f"PartitionKey eq '{username}'")
        jobs = [self._deserialize_job_metadata(e) for e in query]
        logger.info("[TABLE_LIST_JOBS_METADATA] user=%s found=%d", username, len(jobs))

        return jobs

    def list_jobs_metadata_by_status(
        self,
        statuses: Iterable[str],
        *,
        updated_before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.job_metadata)
        if not table:
            return []
        status_list = [status for status in (statuses or []) if status]
        if not status_list:
            return []
        status_filters = [f"status eq '{status}'" for status in status_list]
        filters = [f"({' or '.join(status_filters)})"]
        if updated_before:
            filters.append(f"updated_at lt '{updated_before}'")
        filter_str = " and ".join(filters)
        query = table.list_entities(filter=filter_str)
        return [self._deserialize_job_metadata(e) for e in query]

    def _deserialize_job_metadata(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.get("PartitionKey")
        payload["job_id"] = payload.get("RowKey")
        payload["bundle_fingerprint"] = payload.get("bundle_fingerprint") or None
        payload["merge_enqueued_at"] = payload.get("merge_enqueued_at") or None
        payload["last_requeue_at"] = payload.get("last_requeue_at") or None
        payload["trace_id"] = payload.get("trace_id") or None
        payload["request_id"] = payload.get("request_id") or None
        payload["force_refresh"] = bool(payload.get("force_refresh"))
        payload["created_at"] = payload.get("created_at") or None
        payload["updated_at"] = payload.get("updated_at") or None
        return payload


    # ------------------------------------------------------------------
    # Repo metadata
    # ------------------------------------------------------------------

    def upsert_repo_metadata(self, row: RepoMetadataRow) -> None:
        self.batch_upsert_repo_metadata([row])

    def batch_upsert_repo_metadata(self, rows: Sequence[RepoMetadataRow]) -> None:
        table = self._get_table_client(self.table_names.repo_metadata)
        if not table or not rows:
            return
        operations = []
        for row in rows:
            entity = self._serialize_repo_row(row)
            operations.append(("upsert", entity, {"mode": UpdateMode.MERGE}))
            if len(operations) == 100:
                table.submit_transaction(operations)
                operations = []
        if operations:
            table.submit_transaction(operations)

    def query_repo_metadata(
        self,
        username: str,
        *,
        repo_names: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Query repo metadata - job_id filter removed (use RepoSyncStatus)."""
        table = self._get_table_client(self.table_names.repo_metadata)
        if not table:
            return []
        filters = [f"PartitionKey eq '{username}'"]
        if repo_names:
            names = list(repo_names)
            if names:
                name_filters = [f"RowKey eq '{name}'" for name in names]
                filters.append(f"({' or '.join(name_filters)})")
        filter_str = " and ".join(filters)
        entities = table.list_entities(filter=filter_str)

        return [self._deserialize_repo_entity(e) for e in entities]


    def _serialize_repo_row(self, row: RepoMetadataRow) -> Dict[str, Any]:
        now = _utcnow_iso()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": row.repo_name,
            "fingerprint": row.fingerprint or "",
            "content_blob": row.content_blob or "",
            "has_documentation": bool(row.has_documentation),
            "readme_excerpt": (row.readme_excerpt or "")[:16384],
            "created_at": row.created_at or now,
            "last_synced_at": row.last_synced_at or now,
            "updated_at": row.updated_at or now,
        }
        return entity

    def _deserialize_repo_entity(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize Azure Table entity to clean dictionary - normalized schema."""
        payload = dict(entity)
        
        # Remove Azure metadata fields
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        
        # Map Azure keys to application fields
        payload["repo_name"] = payload.pop("RowKey", None)
        payload["username"] = payload.pop("PartitionKey", None)
        
        # Normalize optional fields
        payload["fingerprint"] = payload.get("fingerprint") or None
        payload["content_blob"] = payload.get("content_blob") or None
        payload["readme_excerpt"] = payload.get("readme_excerpt") or None
        payload["has_documentation"] = bool(payload.get("has_documentation"))
        payload["created_at"] = payload.get("created_at") or None
        payload["last_synced_at"] = payload.get("last_synced_at") or None
        payload["updated_at"] = payload.get("updated_at") or None
        return payload


    # ------------------------------------------------------------------
    # Repo sync status (per job, per repo)
    # ------------------------------------------------------------------
    def upsert_repo_status(self, row: RepoSyncStatusRow) -> None:
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table:
            return
        status = (row.status or "").strip().lower()
        if status not in _REPO_STATUS_ALLOWED:
            raise ValueError(f"Invalid repo sync status: {status}")

        now = _utcnow_iso()
        entity: Dict[str, Any] = {
            "PartitionKey": row.job_id,
            "RowKey": row.repo_name,
            "username": row.username,
            "status": status,
            "message_id": row.message_id or "",
            "error": row.error or "",
            "updated_at": row.updated_at or now,
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
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table:
            return []
        query = table.list_entities(filter=f"PartitionKey eq '{job_id}'")
        rows = [self._deserialize_repo_status(e) for e in query]

        # Defense-in-depth: if an emulator/provider ignores filters, avoid cross-job leakage.
        filtered = [row for row in rows if row.get("job_id") == job_id]
        if rows and len(filtered) != len(rows):
            logger.warning(
                "table-manager: list_repo_statuses filter mismatch (requested=%s returned=%d kept=%d)",
                job_id,
                len(rows),
                len(filtered),
            )
        return filtered

    def _deserialize_repo_status(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["job_id"] = payload.get("PartitionKey")
        payload["repo_name"] = payload.get("RowKey")
        payload["username"] = payload.get("username") or None
        payload["status"] = (payload.get("status") or "").lower() or None
        payload["message_id"] = payload.get("message_id") or None
        payload["error"] = payload.get("error") or None
        payload["updated_at"] = payload.get("updated_at") or None
        return payload

    # ------------------------------------------------------------------
    # Normalized repo tables: Languages, File Types, GitHub Metadata
    # ------------------------------------------------------------------
    def upsert_repo_languages(self, row: RepoLanguagesRow) -> None:
        """Store language statistics for a repository."""
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return
        now = _utcnow_iso()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": f"{row.repo_name}#{row.language}",
            "repo_name": row.repo_name,
            "language": row.language,
            "bytes_count": int(row.bytes_count or 0),
            "percentage": float(row.percentage or 0.0),
            "created_at": row.created_at or now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.REPLACE)

    def batch_upsert_repo_languages(self, rows: List[RepoLanguagesRow]) -> None:
        """Batch insert language statistics - replaces all existing for username+repo."""
        if not rows:
            return
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return
        
        # Group by username+repo to delete existing entries first
        by_repo: Dict[tuple[str, str], List[RepoLanguagesRow]] = {}
        for row in rows:
            key = (row.username, row.repo_name)
            by_repo.setdefault(key, []).append(row)
        
        for (username, repo_name), repo_rows in by_repo.items():
            # Delete existing entries for this repo
            filter_str = f"PartitionKey eq '{username}' and repo_name eq '{repo_name}'"
            existing = table.list_entities(filter=filter_str)
            for entity in existing:
                table.delete_entity(partition_key=entity["PartitionKey"], row_key=entity["RowKey"])
            
            # Insert new entries
            for row in repo_rows:
                self.upsert_repo_languages(row)
        
        logger.info("[TABLE_BATCH_UPSERT_LANGUAGES] rows=%d", len(rows))

    def query_repo_languages(self, username: str, repo_name: str) -> List[Dict[str, Any]]:
        """Query all language statistics for a repository."""
        table = self._get_table_client(self.table_names.repo_languages)
        if not table:
            return []
        filter_str = f"PartitionKey eq '{username}' and repo_name eq '{repo_name}'"
        entities = table.list_entities(filter=filter_str)
        return [self._deserialize_repo_languages(e) for e in entities]

    def _deserialize_repo_languages(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.pop("PartitionKey", None)
        # RowKey is composite, extract from individual fields
        payload.pop("RowKey", None)
        return payload

    def upsert_repo_file_types(self, row: RepoFileTypesRow) -> None:
        """Store file type categorization for a repository."""
        table = self._get_table_client(self.table_names.repo_file_types)
        if not table:
            return
        now = _utcnow_iso()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": f"{row.repo_name}#{row.category}",
            "repo_name": row.repo_name,
            "category": row.category,
            "types": _safe_json_dump_limited(row.types or [], label="file_types.types"),
            "created_at": row.created_at or now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.REPLACE)

    def batch_upsert_repo_file_types(self, rows: List[RepoFileTypesRow]) -> None:
        """Batch insert file types - replaces all existing for username+repo."""
        if not rows:
            return
        table = self._get_table_client(self.table_names.repo_file_types)
        if not table:
            return
        
        # Group by username+repo to delete existing entries first
        by_repo: Dict[tuple[str, str], List[RepoFileTypesRow]] = {}
        for row in rows:
            key = (row.username, row.repo_name)
            by_repo.setdefault(key, []).append(row)
        
        for (username, repo_name), repo_rows in by_repo.items():
            # Delete existing entries for this repo
            filter_str = f"PartitionKey eq '{username}' and repo_name eq '{repo_name}'"
            existing = table.list_entities(filter=filter_str)
            for entity in existing:
                table.delete_entity(partition_key=entity["PartitionKey"], row_key=entity["RowKey"])
            
            # Insert new entries
            for row in repo_rows:
                self.upsert_repo_file_types(row)
        
        logger.info("[TABLE_BATCH_UPSERT_FILE_TYPES] rows=%d", len(rows))

    def query_repo_file_types(self, username: str, repo_name: str) -> List[Dict[str, Any]]:
        """Query all file type categories for a repository."""
        table = self._get_table_client(self.table_names.repo_file_types)
        if not table:
            return []
        filter_str = f"PartitionKey eq '{username}' and repo_name eq '{repo_name}'"
        entities = table.list_entities(filter=filter_str)
        return [self._deserialize_repo_file_types(e) for e in entities]

    def _deserialize_repo_file_types(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.pop("PartitionKey", None)
        payload.pop("RowKey", None)
        try:
            payload["types"] = json.loads(payload.get("types") or "[]")
        except json.JSONDecodeError:
            payload["types"] = []
        return payload

    def upsert_repo_github_metadata(self, row: RepoGitHubMetadataRow) -> None:
        """Store GitHub API metadata for a repository."""
        table = self._get_table_client(self.table_names.repo_github_metadata)
        if not table:
            return
        now = _utcnow_iso()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": row.repo_name,
            "repo_name": row.repo_name,
            "description": (row.description or "")[:4096],
            "topics": _safe_json_dump_limited(row.topics or [], label="github_metadata.topics"),
            "homepage_url": (row.homepage_url or "")[:2048],
            "stars_count": int(row.stars_count or 0),
            "forks_count": int(row.forks_count or 0),
            "is_fork": bool(row.is_fork),
            "is_archived": bool(row.is_archived),
            "primary_language": (row.primary_language or "")[:128],
            "license_name": (row.license_name or "")[:256],
            "created_at": row.created_at or now,
            "updated_at": now,
        }
        table.upsert_entity(entity, mode=UpdateMode.REPLACE)
        logger.info("[TABLE_UPSERT_GITHUB_METADATA] user=%s repo=%s", row.username, row.repo_name)

    def get_repo_github_metadata(self, username: str, repo_name: str) -> Optional[Dict[str, Any]]:
        """Get GitHub metadata for a repository."""
        table = self._get_table_client(self.table_names.repo_github_metadata)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=username, row_key=repo_name)
        except ResourceNotFoundError:
            return None
        return self._deserialize_repo_github_metadata(entity)

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
        return payload

    # ------------------------------------------------------------------
    # Model metadata
    # ------------------------------------------------------------------
    def upsert_model_metadata(self, row: ModelMetadataRow) -> None:
        table = self._get_table_client(self.table_names.model_metadata)
        if not table:
            return
        now = _utcnow_iso()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": row.fingerprint or row.model_fingerprint,
            "experiment_name": row.experiment_name,
            "status": row.status,
            "artifact_blob": row.artifact_blob or "",
            "repos_count": int(row.repos_count or 0),
            "trained_at": row.trained_at or "",
            "updated_at": row.updated_at or now,
        }
        for field_name in _JSON_FIELDS_MODEL:
            entity[field_name] = _safe_json_dump(getattr(row, field_name, {}))
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def get_model_metadata(self, username: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.model_metadata)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=username, row_key=fingerprint)
        except ResourceNotFoundError:
            return None
        return self._deserialize_model_entity(entity)

    def list_model_metadata(self, username: str) -> List[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.model_metadata)
        if not table:
            return []
        entities = table.list_entities(filter=f"PartitionKey eq '{username}'")
        return [self._deserialize_model_entity(e) for e in entities]

    def _deserialize_model_entity(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        for field_name in _JSON_FIELDS_MODEL:
            try:
                payload[field_name] = json.loads(payload.get(field_name) or "{}")
            except json.JSONDecodeError:
                payload[field_name] = {}
        payload["artifact_blob"] = payload.get("artifact_blob") or None
        payload["repo_names"] = payload.get("repo_names") or []
        payload["repos_count"] = int(payload.get("repos_count", 0))
        payload["fingerprint"] = payload.get("RowKey")
        payload["model_fingerprint"] = payload.get("fingerprint")
        payload["username"] = payload.get("PartitionKey")
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
