import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient, QueueServiceClient

logger = logging.getLogger(__name__)

SYNC_QUEUE = "github-sync"
MERGE_QUEUE = "merge-results"
TRAINING_QUEUE = "model-training"
JOB_STATUS_QUEUE = "job-status-updates"


def _clean_queue_name(name: str) -> str:
    return name.strip().lower()


def _bundle_cache_key(username: str) -> str:
    username_str = str(username or "").strip()
    if not username_str:
        return ""
    return f"repos_bundle_context_{username_str}"


class QueueManager:
    """Wrapper around Azure Storage Queues used across workers."""

    def __init__(
        self,
        service_client: Optional[QueueServiceClient] = None,
        queue_names: Optional[Dict[str, str]] = None,
    ) -> None:
        self.queue_names = queue_names or {
            SYNC_QUEUE: SYNC_QUEUE,
            MERGE_QUEUE: MERGE_QUEUE,
            TRAINING_QUEUE: TRAINING_QUEUE,
            JOB_STATUS_QUEUE: JOB_STATUS_QUEUE,
        }
        self.service_client = service_client or self._create_service_client()
        self._queue_clients: Dict[str, QueueClient] = {}

        if self.service_client:
            self._ensure_queues_exist()
        else:
            logger.warning("QueueManager disabled (no QueueServiceClient available)")

    def _create_service_client(self) -> Optional[QueueServiceClient]:
        account_url = os.getenv('AzureWebJobsStorage__queueServiceUri')
        connection_string = os.getenv('AzureWebJobsStorage')

        if account_url:
            try:
                credential = DefaultAzureCredential()
                logger.info("Initializing QueueServiceClient via managed identity")
                return QueueServiceClient(account_url=account_url, credential=credential)
            except (ClientAuthenticationError, HttpResponseError) as exc:
                logger.warning("Managed identity queue auth failed: %s", exc)
            except Exception as exc:  # pragma: no cover - safety net
                logger.error("Unexpected queue auth error: %s", exc)

        if connection_string:
            try:
                logger.info("Initializing QueueServiceClient via connection string")
                return QueueServiceClient.from_connection_string(connection_string)
            except Exception as exc:  # pragma: no cover - safety net
                logger.error("QueueServiceClient connection string error: %s", exc)

        logger.warning("Azure Queue Service not configured (missing URI/connection string)")
        return None

    def _ensure_queues_exist(self) -> None:
        if not self.service_client:
            return
        for queue_alias, queue_name in self.queue_names.items():
            normalized = _clean_queue_name(queue_name)
            try:
                client = self.service_client.get_queue_client(normalized)
                client.create_queue()
                self._queue_clients[queue_alias] = client
                logger.info("Queue ensured: %s", normalized)
            except Exception as exc:
                if "QueueAlreadyExists" in str(exc):
                    self._queue_clients[queue_alias] = self.service_client.get_queue_client(normalized)
                else:
                    logger.warning("Unable to create queue %s: %s", normalized, exc)

    def _get_queue_client(self, queue_alias: str) -> Optional[QueueClient]:
        if not self.service_client:
            return None
        client = self._queue_clients.get(queue_alias)
        if client:
            return client
        queue_name = self.queue_names.get(queue_alias)
        if not queue_name:
            return None
        normalized = _clean_queue_name(queue_name)
        client = self.service_client.get_queue_client(normalized)
        self._queue_clients[queue_alias] = client
        return client

    def is_enabled(self) -> bool:
        return self.service_client is not None

    def send_message(self, queue_alias: str, payload: Dict) -> bool:
        client = self._get_queue_client(queue_alias)
        if not client:
            logger.warning("Queue client unavailable for %s", queue_alias)
            return False

        # client send message payload
        json_str = json.dumps(payload)
        encoded_message = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        client.send_message(encoded_message)
        logger.info("Sent message to queue %s", queue_alias)
        return True

    def enqueue_sync_job(self, job_id: str, username: str, repo_metadata: Dict, fingerprint: Optional[str] = None) -> bool:
        repo_name = repo_metadata.get('name') if isinstance(repo_metadata, dict) else None
        resolved_fingerprint = fingerprint or (repo_metadata.get('fingerprint') if isinstance(repo_metadata, dict) else None)
        message = {
            "schema_version": "2025-12-01",
            "job_id": job_id,
            "username": username,
            "repo_name": repo_name,
            "metadata": repo_metadata,
            "fingerprint": resolved_fingerprint,
            "bundle_cache_key": _bundle_cache_key(username),
            "blob_batch": {"status": "pending", "items": []},
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "repo": {
                "name": repo_name,
                "fingerprint": resolved_fingerprint,
            },
        }
        return self.send_message(SYNC_QUEUE, message)

    def enqueue_merge_job(self, job_id: str, username: str, synced_repos: List[str]) -> bool:
        message = {
            "job_id": job_id,
            "username": username,
            "synced_repos": synced_repos,
            "trigger_source": "sync_complete"
        }
        return self.send_message(MERGE_QUEUE, message)

    def enqueue_training_job(self, username: str, repos_bundle: List[Dict], training_params: Optional[Dict] = None) -> bool:
        message = {
            "username": username,
            "repos_bundle": repos_bundle,
            "training_params": training_params or {}
        }
        return self.send_message(TRAINING_QUEUE, message)

    def enqueue_status_update(self, job_id: str, status: str, details: Optional[Dict] = None) -> bool:
        message = {
            "job_id": job_id,
            "status": status,
            "details": details or {}
        }
        return self.send_message(JOB_STATUS_QUEUE, message)


queue_manager = QueueManager()
