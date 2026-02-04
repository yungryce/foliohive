import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient, QueueServiceClient

from cloudfolio_shared.cache.cache_manager import cache_manager

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = True

SYNC_QUEUE = "github-sync"
CACHE_QUEUE = "github-cache"
JOB_STATUS_QUEUE = "job-status-updates"


def _clean_queue_name(name: str) -> str:
    return name.strip().lower()

def _extract_send_message_id(send_result: Any) -> Optional[str]:
    if send_result is None:
        return None
    for attr in ("id", "message_id"):
        value = getattr(send_result, attr, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(send_result, dict):
        for key in ("id", "message_id"):
            value = send_result.get(key)
            if isinstance(value, str) and value:
                return value
    return None
class QueueManager:
    """Wrapper around Azure Storage Queues used across workers."""

    def __init__(
        self,
        service_client: Optional[QueueServiceClient] = None,
        queue_names: Optional[Dict[str, str]] = None,
    ) -> None:
        self.queue_names = queue_names or {
            SYNC_QUEUE: SYNC_QUEUE,
            CACHE_QUEUE: CACHE_QUEUE,
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

    def send_message(self, queue_alias: str, payload: Dict[str, Any]) -> Optional[str]:
        client = self._get_queue_client(queue_alias)
        if not client:
            logger.warning("Queue client unavailable for %s", queue_alias)
            return None

        # client send message payload
        json_str = json.dumps(payload)
        json_size = len(json_str.encode('utf-8'))
        
        # Log message size (Azure Queue 64KB limit for message)
        repo_name = payload.get("repo_name")
        if not repo_name:
            repo_names = payload.get("repo_names")
            if isinstance(repo_names, list) and repo_names:
                repo_name = repo_names[0]
        repo_name = repo_name or "n/a"
        job_id = payload.get("job_id") or "n/a"
        logger.info(
            "Enqueued message to %s: json_size=%d bytes, repo_name=%s, job_id=%s",
            queue_alias,
            json_size,
            repo_name,
            job_id
        )
        
        send_result = client.send_message(json_str)
        message_id = _extract_send_message_id(send_result)

        trace_id = payload.get("trace_id")
        session_id = payload.get("session_id")
        logger.info(
            "[QUEUE_ENQUEUE] queue=%s trace_id=%s message_id=%s job_id=%s repo=%s size_bytes=%d session_id=%s",
            queue_alias,
            trace_id or "<none>",
            message_id or "<unknown>",
            job_id,
            repo_name,
            json_size,
            session_id or "<none>",
        )

        logger.info("[SEND_DEBUG] repo=%s job=%s message_id=%s - Message sent successfully", repo_name, job_id, message_id)
        return message_id

    def enqueue_sync_job(
        self,
        job_id: str,
        username: str,
        repo_name: str,
        fingerprint: Optional[str] = None,
        *,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
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
            "trace_id": trace_id,
            "request_id": request_id,
            "session_id": session_id,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

        return bool(self.send_message(SYNC_QUEUE, message))

    def enqueue_cache_job(
        self,
        username: str,
        job_id: str,
        repo_name: str,
        fingerprint: Optional[str] = None,
        *,
        trace_id: Optional[str] = None,
    ) -> bool:
        """Enqueue a cache job to fetch and cache file contents asynchronously.
        
        Args:
            username: GitHub username
            job_id: Unique job identifier
            repo_name: Repository name
            fingerprint: Optional metadata fingerprint for cache validation
            trace_id: Optional trace ID for logging correlation
            
        Returns:
            bool: True if message was enqueued successfully
        """
        if not repo_name:
            logger.warning("Cannot enqueue cache job without repo_name")
            return False
        
        message = {
            "schema_version": "2025-12-01",
            "job_id": job_id,
            "username": username,
            "repo_name": repo_name,
            "fingerprint": fingerprint,
            "trace_id": trace_id,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        
        return bool(self.send_message(CACHE_QUEUE, message))

    # def enqueue_merge_job(
    #     self,
    #     job_id: str,
    #     username: str,
    #     synced_repos: List[str],
    #     *,
    #     trace_id: Optional[str] = None,
    #     request_id: Optional[str] = None,
    #     session_id: Optional[str] = None,
    # ) -> bool:
    #     message = {
    #         "job_id": job_id,
    #         "username": username,
    #         "synced_repos": synced_repos,
    #         "trigger_source": "sync_complete",
    #         "trace_id": trace_id,
    #         "request_id": request_id,
    #         "session_id": session_id,
    #         "queued_at": datetime.now(timezone.utc).isoformat(),
    #     }
    #     return bool(self.send_message(MERGE_QUEUE, message))

queue_manager = QueueManager()
