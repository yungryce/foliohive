"""Azure Table Storage helper for Cloudfolio metadata."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

__all__ = [
    "TableManager",
    "TableNames",
    "CandidateSessionRow",
    "RepoMetadataRow",
    "RepoSyncStatusRow",
    "ModelMetadataRow",
    "table_manager",
]

logger = logging.getLogger(__name__)


@dataclass
class TableNames:
    """Configured table names used by Cloudfolio."""

    candidate_sessions: str = "CandidateSessions"
    repo_metadata: str = "RepoMetadata"
    model_metadata: str = "ModelMetadata"
    repo_sync_status: str = "RepoSyncStatus"


@dataclass
class CandidateSessionRow:
    """Representation of a candidate session/job row."""

    username: str
    job_id: str
    status: str = "queued"
    total_repos: int = 0
    completed_repos: int = 0
    expected_repos: List[str] = field(default_factory=list)
    queued_repos: List[str] = field(default_factory=list)
    synced_repos: List[str] = field(default_factory=list)
    failed_repos: List[str] = field(default_factory=list)
    bundle_fingerprint: Optional[str] = None
    force_refresh: bool = False
    model_status: Optional[str] = None
    model_fingerprint: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoMetadataRow:
    """Per-repository metadata used by merge + AI flows."""

    username: str
    repo_name: str
    fingerprint: Optional[str]
    job_id: Optional[str] = None
    document: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    content_blob: Optional[str] = None
    languages: Dict[str, Any] = field(default_factory=dict)
    categorized_types: Dict[str, Any] = field(default_factory=dict)
    has_documentation: Optional[bool] = None
    readme_excerpt: Optional[str] = None
    created_at: Optional[str] = None
    last_synced_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RepoSyncStatusRow:
    """Per-repository sync status for a given job."""

    job_id: str
    repo_name: str
    username: str
    status: str  # synced | failed | pending
    message_uuid: Optional[str] = None
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


_JSON_LIST_FIELDS = {"expected_repos", "queued_repos", "synced_repos", "failed_repos"}
_JSON_FIELDS_REPO = {"document", "metadata", "languages", "categorized_types"}
_JSON_FIELDS_MODEL = {"metadata", "training_params", "repo_names"}
_AZURE_META_FIELDS = {"etag", "odata.etag", "odata.metadata"}
_REPO_STATUS_ALLOWED = {"synced", "failed", "pending"}


def _utcnow_iso() -> str: 
    return datetime.now(timezone.utc).isoformat()


def _safe_json_dump(value: Any) -> str:
    return json.dumps({} if value is None else value, separators=(",", ":"))


class TableManager:
    """High-level helper that wraps Azure Data Tables usage."""

    def __init__(
        self,
        *,
        table_service_client: Optional[TableServiceClient] = None,
        table_names: Optional[TableNames] = None,
    ) -> None:
        self.table_names = table_names or TableNames(
            candidate_sessions=os.getenv("TABLE_CANDIDATE_SESSIONS", TableNames.candidate_sessions),
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
        account_url = (
            os.getenv("TABLE_SERVICE_URI")
            or os.getenv("AzureWebJobsStorage__tableServiceUri")
        )
        connection_string = (
            os.getenv("TABLE_STORAGE_CONNECTION_STRING")
            or os.getenv("AzureWebJobsStorage")
        )

        if account_url:
            try:
                credential = DefaultAzureCredential()
                logger.info("Initialising TableServiceClient via managed identity")
                return TableServiceClient(account_url=account_url, credential=credential)
            except Exception as exc:  # pragma: no cover - fallback path
                logger.warning("Managed identity table auth failed: %s", exc)

        if connection_string:
            try:
                logger.info("Initialising TableServiceClient via connection string")
                return TableServiceClient.from_connection_string(connection_string)
            except Exception as exc:  # pragma: no cover - fallback path
                logger.error("TableServiceClient connection error: %s", exc)

        logger.warning("Azure Table Service not configured")
        return None

    def _ensure_tables_exist(self) -> None:
        if not self._service_client:
            return
        for name in (
            self.table_names.candidate_sessions,
            self.table_names.repo_metadata,
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

    def is_enabled(self) -> bool:
        return self._service_client is not None

    # ------------------------------------------------------------------
    # Candidate sessions
    # ------------------------------------------------------------------
    def upsert_candidate_session(self, row: CandidateSessionRow) -> None:
        table = self._get_table_client(self.table_names.candidate_sessions)
        if not table:
            return
        now = _utcnow_iso()
        entity = {
            "PartitionKey": row.username,
            "RowKey": row.job_id,
            "status": row.status,
            "total_repos": int(row.total_repos),
            "completed_repos": int(row.completed_repos),
            "bundle_fingerprint": row.bundle_fingerprint or "",
            "force_refresh": bool(row.force_refresh),
            "model_status": row.model_status or "",
            "model_fingerprint": row.model_fingerprint or "",
            "created_at": row.created_at or now,
            "updated_at": now,
        }
        for field_name in _JSON_LIST_FIELDS:
            entity[field_name] = json.dumps(getattr(row, field_name, []) or [], separators=(",", ":"))
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def update_candidate_session(self, username: str, job_id: str, updates: Dict[str, Any]) -> None:
        table = self._get_table_client(self.table_names.candidate_sessions)
        if not table or not updates:
            return
        entity: Dict[str, Any] = {"PartitionKey": username, "RowKey": job_id, "updated_at": _utcnow_iso()}
        for key, value in updates.items():
            if key in _JSON_LIST_FIELDS:
                entity[key] = json.dumps(value or [], separators=(",", ":"))
            else:
                entity[key] = value if value is not None else ""
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def get_candidate_session(self, username: str, job_id: str) -> Optional[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.candidate_sessions)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=username, row_key=job_id)
        except ResourceNotFoundError:
            return None
        return self._deserialize_candidate_session(entity)

    def list_candidate_sessions(self, username: str) -> List[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.candidate_sessions)
        if not table:
            return []
        # query = table.list_entities(f"PartitionKey eq '{username}'")
        query = table.list_entities(filter=f"PartitionKey eq '{username}'")
        return [self._deserialize_candidate_session(e) for e in query]

    def _deserialize_candidate_session(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["username"] = payload.get("PartitionKey")
        payload["job_id"] = payload.get("RowKey")
        for field_name in _JSON_LIST_FIELDS:
            try:
                payload[field_name] = json.loads(payload.get(field_name) or "[]")
            except json.JSONDecodeError:
                payload[field_name] = []
        payload["bundle_fingerprint"] = payload.get("bundle_fingerprint") or None
        payload["model_fingerprint"] = payload.get("model_fingerprint") or None
        payload["model_status"] = payload.get("model_status") or None
        payload["force_refresh"] = bool(payload.get("force_refresh"))
        payload["total_repos"] = int(payload.get("total_repos", 0))
        payload["completed_repos"] = int(payload.get("completed_repos", 0))
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
        job_id: Optional[str] = None,
        repo_names: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.repo_metadata)
        if not table:
            return []
        filters = [f"PartitionKey eq '{username}'"]
        if job_id:
            filters.append(f"job_id eq '{job_id}'")
        if repo_names:
            names = list(repo_names)
            if names:
                name_filters = [f"RowKey eq '{name}'" for name in names]
                filters.append(f"({' or '.join(name_filters)})")
        filter_str = " and ".join(filters)
        entities = table.list_entities(filter_str)
        return [self._deserialize_repo_entity(e) for e in entities]

    def _serialize_repo_row(self, row: RepoMetadataRow) -> Dict[str, Any]:
        now = _utcnow_iso()
        entity: Dict[str, Any] = {
            "PartitionKey": row.username,
            "RowKey": row.repo_name,
            "fingerprint": row.fingerprint or "",
            "job_id": row.job_id or "",
            "content_blob": row.content_blob or "",
            "has_documentation": bool(row.has_documentation),
            "readme_excerpt": (row.readme_excerpt or "")[:16384],
            "created_at": row.created_at or now,
            "last_synced_at": row.last_synced_at or now,
            "updated_at": row.updated_at or now,
        }
        for field_name in _JSON_FIELDS_REPO:
            entity[field_name] = _safe_json_dump(getattr(row, field_name, {}))
        return entity

    def _deserialize_repo_entity(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        for field_name in _JSON_FIELDS_REPO:
            try:
                payload[field_name] = json.loads(payload.get(field_name) or "{}")
            except json.JSONDecodeError:
                payload[field_name] = {} if field_name != "document" else None
        payload["fingerprint"] = payload.get("fingerprint") or None
        payload["job_id"] = payload.get("job_id") or None
        payload["content_blob"] = payload.get("content_blob") or None
        payload["has_documentation"] = bool(payload.get("has_documentation"))
        payload["repo_name"] = payload.get("RowKey")
        payload["username"] = payload.get("PartitionKey")
        payload["created_at"] = payload.get("created_at") or None
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
            "message_uuid": row.message_uuid or "",
            "error": row.error or "",
            "updated_at": row.updated_at or now,
        }
        table.upsert_entity(entity, mode=UpdateMode.MERGE)

    def get_repo_status(self, job_id: str, repo_name: str) -> Optional[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table:
            return None
        try:
            entity = table.get_entity(partition_key=job_id, row_key=repo_name)
        except ResourceNotFoundError:
            return None
        return self._deserialize_repo_status(entity)

    def list_repo_statuses(self, job_id: str) -> List[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.repo_sync_status)
        if not table:
            return []
        query = table.list_entities(filter=f"PartitionKey eq '{job_id}'")
        return [self._deserialize_repo_status(e) for e in query]

    def _deserialize_repo_status(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entity)
        for meta_key in _AZURE_META_FIELDS:
            payload.pop(meta_key, None)
        payload["job_id"] = payload.get("PartitionKey")
        payload["repo_name"] = payload.get("RowKey")
        payload["username"] = payload.get("username") or None
        payload["status"] = (payload.get("status") or "").lower() or None
        payload["message_uuid"] = payload.get("message_uuid") or None
        payload["error"] = payload.get("error") or None
        payload["updated_at"] = payload.get("updated_at") or None
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
        entities = table.list_entities(f"PartitionKey eq '{username}'")
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
