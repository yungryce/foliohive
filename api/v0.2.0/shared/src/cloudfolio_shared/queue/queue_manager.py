import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient, QueueServiceClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = True

SYNC_QUEUE = "github-sync"
MERGE_QUEUE = "merge-results"
TRAINING_QUEUE = "model-training"
JOB_STATUS_QUEUE = "job-status-updates"


def _clean_queue_name(name: str) -> str:
    return name.strip().lower()
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
        json_size = len(json_str.encode('utf-8'))
        
        # Log message size (Azure Queue 64KB limit for message)
        repo_name = payload.get('repo_name', 'unknown')
        job_id = payload.get('job_id', 'unknown')
        logger.info(
            "Enqueued message to %s: json_size=%d bytes, repo_name=%s, job_id=%s",
            queue_alias,
            json_size,
            repo_name,
            job_id
        )
        
        # Debug: Log message structure for investigation
        logger.info("[SEND_DEBUG] repo=%s job=%s - Top-level keys: %s", 
                    repo_name, job_id, sorted(list(payload.keys())))
        logger.info("[SEND_DEBUG] repo=%s - schema=%s, username=%s, has_metadata=%s", 
                    repo_name, 
                    payload.get('schema_version', 'missing'),
                    payload.get('username', 'missing'),
                    'metadata' in payload and isinstance(payload['metadata'], dict))
        
        if "metadata" in payload and isinstance(payload["metadata"], dict):
            metadata = payload["metadata"]
            metadata_json_size = len(json.dumps(metadata).encode('utf-8'))
            logger.info("[SEND_DEBUG] repo=%s - Metadata has %d keys, size=%d bytes", 
                       repo_name, len(metadata), metadata_json_size)
            logger.info("[SEND_DEBUG] repo=%s - Metadata keys: %s", 
                       repo_name, sorted(list(metadata.keys())))
        
        client.send_message(json_str)
        logger.info("[SEND_DEBUG] repo=%s job=%s - Message sent successfully", repo_name, job_id)
        return True

    def enqueue_sync_job(self, job_id: str, username: str, repo_name: str, fingerprint: Optional[str] = None) -> bool:
        """Enqueue a sync job with minimal message payload.
        
        Args:
            job_id: Unique job identifier
            username: GitHub username
            repo_name: Repository name
            fingerprint: Optional metadata fingerprint for cache validation
            
        Returns:
            bool: True if message was enqueued successfully
        """
        if not repo_name:
            logger.warning("Cannot enqueue sync job without repo_name")
            return False
        
        # Construct minimal message with only essential identifiers
        message = {
            "schema_version": "2025-12-01",
            "job_id": job_id,
            "username": username,
            "repo_name": repo_name,
            "fingerprint": fingerprint,
            "queued_at": datetime.now(timezone.utc).isoformat(),
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

    def enqueue_training_job(
        self,
        *,
        username: str,
        bundle_cache_key: str,
        training_params: Optional[Dict] = None,
        job_id: Optional[str] = None,
        repo_names: Optional[List[str]] = None,
        bundle_fingerprint: Optional[str] = None,
        experiment_name: str = "default",
    ) -> bool:
        """Enqueue a training job without embedding large payloads.

        The training worker fetches the actual repository bundle from blob storage
        using the provided cache key.
        """
        message: Dict[str, Any] = {
            "schema_version": "2025-12-18",
            "job_id": job_id,
            "username": username,
            "experiment_name": experiment_name,
            "bundle_cache_key": bundle_cache_key,
            "bundle_fingerprint": bundle_fingerprint,
            "repo_names": repo_names or [],
            "training_params": training_params or {},
            "queued_at": datetime.now(timezone.utc).isoformat(),
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
