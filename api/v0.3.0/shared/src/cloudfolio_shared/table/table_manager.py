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
    "CandidateSessionRow",
    "RepoMetadataRow",
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
    merge_enqueued_at: Optional[str] = None
    last_requeue_at: Optional[str] = None
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
            candidate_sessions=os.getenv("TABLE_CANDIDATE_SESSIONS", TableNames.candidate_sessions),
            repo_metadata=os.getenv("TABLE_REPO_METADATA", TableNames.repo_metadata),
            model_metadata=os.getenv("TABLE_MODEL_METADATA", TableNames.model_metadata),
        )

        # Diagnostics captured during initialization for later troubleshooting.
        # This helps when initialization happens early and logs are not emitted/visible.
        self._init_diagnostics: Dict[str, Any] = {}
        self._disabled_logged = False
        self._enabled_logged = False

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

        # Store presence flags (do NOT log secrets like connection strings).
        self._init_diagnostics.update(
            {
                "table_service_uri_present": bool(account_url),
                "table_storage_connection_string_present": bool(os.getenv("TABLE_STORAGE_CONNECTION_STRING")),
                "azurewebjobsstorage_present": bool(os.getenv("AzureWebJobsStorage")),
                "azurewebjobsstorage_table_uri_present": bool(os.getenv("AzureWebJobsStorage__tableServiceUri")),
            }
        )

        if account_url:
            try:
                credential = DefaultAzureCredential()
                self._init_diagnostics["table_auth_mode"] = "managed_identity"
                logger.info("[TABLE_DEBUG] Initialising TableServiceClient via managed identity")
                return TableServiceClient(endpoint=account_url, credential=credential)
            except (ClientAuthenticationError, HttpResponseError) as exc:
                self._init_diagnostics["table_init_error"] = f"{type(exc).__name__}: {exc}"
                logger.warning("[TABLE_DEBUG] Managed identity table auth failed: %s", exc, exc_info=True)
            except Exception as exc:  # pragma: no cover - fallback path
                self._init_diagnostics["table_init_error"] = f"{type(exc).__name__}: {exc}"
                logger.error("[TABLE_DEBUG] Unexpected table auth error: %s", exc, exc_info=True)

        if connection_string:
            try:
                self._init_diagnostics["table_auth_mode"] = "connection_string"
                logger.info("Initialising TableServiceClient via connection string")
                return TableServiceClient.from_connection_string(connection_string)
            except Exception as exc:  # pragma: no cover - fallback path
                self._init_diagnostics["table_init_error"] = f"{type(exc).__name__}: {exc}"
                logger.error("TableServiceClient connection error: %s", exc)

        if account_url or connection_string:
            # Config appears present but client could not be created.
            logger.warning(
                "[TABLE_DEBUG] Azure Table Service configured but could not create client (auth failure?)"
            )
        else:
            logger.warning("[TABLE_DEBUG] Azure Table Service not configured (missing URI/connection string)")
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
            # Emit a late diagnostic when tables are actually used.
            self.is_enabled()
            return None
        client = self._tables.get(table_name)
        if client is None:
            client = self._service_client.get_table_client(table_name)
            self._tables[table_name] = client
        return client

    def is_enabled(self) -> bool:
        enabled = self._service_client is not None
        if enabled and not self._enabled_logged:
            mode = self._init_diagnostics.get("table_auth_mode")
            logger.info("[TABLE_DEBUG] TableManager enabled (auth_mode=%s)", mode or "unknown")
            self._enabled_logged = True
        if not enabled and not self._disabled_logged:
            logger.warning(
                "[TABLE_DEBUG] TableManager disabled: uri_present=%s azure_uri_present=%s conn_present=%s last_error=%s",
                self._init_diagnostics.get("table_service_uri_present"),
                self._init_diagnostics.get("azurewebjobsstorage_table_uri_present"),
                self._init_diagnostics.get("azurewebjobsstorage_present"),
                self._init_diagnostics.get("table_init_error"),
            )
            self._disabled_logged = True
        return enabled

    # ------------------------------------------------------------------
    # Candidate sessions
    # ------------------------------------------------------------------
    def upsert_candidate_session(self, row: CandidateSessionRow) -> None:
        table = self._get_table_client(self.table_names.candidate_sessions)
        if not table:
            return
        logger.info(
            "[TABLE_UPSERT_SESSION] user=%s job=%s status=%s total=%s completed=%s expected=%d queued=%d synced=%d",
            row.username,
            row.job_id,
            row.status,
            row.total_repos,
            row.completed_repos,
            len(row.expected_repos or []),
            len(row.queued_repos or []),
            len(row.synced_repos or []),
        )
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
            "merge_enqueued_at": row.merge_enqueued_at or "",
            "last_requeue_at": row.last_requeue_at or "",
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
        logger.info(
            "[TABLE_UPDATE_SESSION] user=%s job=%s keys=%s",
            username,
            job_id,
            sorted(list(updates.keys())),
        )
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
        logger.info("[TABLE_GET_SESSION] user=%s job=%s found=true", username, job_id)
        return self._deserialize_candidate_session(entity)

    def list_candidate_sessions(self, username: str) -> List[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.candidate_sessions)
        if not table:
            return []
        # query = table.list_entities(f"PartitionKey eq '{username}'")
        query = table.list_entities(filter=f"PartitionKey eq '{username}'")
        sessions = [self._deserialize_candidate_session(e) for e in query]

        # Defense-in-depth: if an emulator/provider ignores filters, avoid leaking other users' sessions.
        filtered = [session for session in sessions if session.get("username") == username]
        if sessions and len(filtered) != len(sessions):
            logger.warning(
                "table-manager: list_candidate_sessions filter mismatch (requested=%s returned=%d kept=%d)",
                username,
                len(sessions),
                len(filtered),
            )
        return filtered

    def list_candidate_sessions_by_status(
        self,
        statuses: Iterable[str],
        *,
        updated_before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        table = self._get_table_client(self.table_names.candidate_sessions)
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
        payload["merge_enqueued_at"] = payload.get("merge_enqueued_at") or None
        payload["last_requeue_at"] = payload.get("last_requeue_at") or None
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
        entities = table.list_entities(filter=filter_str)
        rows = [self._deserialize_repo_entity(e) for e in entities]

        # Defense-in-depth: ensure results match the requested username/job_id even if filters are ignored.
        filtered = [row for row in rows if row.get("username") == username]
        if job_id:
            filtered = [row for row in filtered if row.get("job_id") == job_id]
        if rows and len(filtered) != len(rows):
            logger.warning(
                "table-manager: query_repo_metadata filter mismatch (requested=%s job_id=%s returned=%d kept=%d)",
                username,
                job_id or "<none>",
                len(rows),
                len(filtered),
            )
        return filtered

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
            entity[field_name] = _safe_json_dump_limited(
                getattr(row, field_name, {}),
                label=f"repo_metadata.{field_name}",
            )
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
