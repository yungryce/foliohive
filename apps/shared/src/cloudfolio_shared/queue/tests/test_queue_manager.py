import base64
import json
from datetime import datetime

import pytest  # type: ignore

from cloudfolio_shared.queue.queue_manager import (
    JOB_STATUS_QUEUE,
    MERGE_QUEUE,
    SYNC_QUEUE,
    TRAINING_QUEUE,
    QueueManager,
)


class StubQueueClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.created = False
        self.messages = []

    def create_queue(self) -> None:
        self.created = True

    def send_message(self, body: str) -> None:
        self.messages.append(body)


class StubQueueServiceClient:
    def __init__(self) -> None:
        self.clients = {}

    def get_queue_client(self, name: str) -> StubQueueClient:
        normalized = name.strip().lower()
        client = self.clients.get(normalized)
        if not client:
            client = StubQueueClient(normalized)
            self.clients[normalized] = client
        return client


def _decode_message(raw: str) -> dict:
    decoded = base64.b64decode(raw.encode("utf-8")).decode("utf-8")
    return json.loads(decoded)


def test_queue_manager_initializes_queues() -> None:
    service = StubQueueServiceClient()
    manager = QueueManager(service_client=service)

    assert manager.is_enabled() is True
    expected = {SYNC_QUEUE, MERGE_QUEUE, TRAINING_QUEUE, JOB_STATUS_QUEUE}
    assert expected.issubset(service.clients.keys())
    assert all(service.clients[name].created for name in expected)


def test_send_message_returns_false_when_disabled(monkeypatch) -> None:
    manager = QueueManager(service_client=None)
    result = manager.send_message(SYNC_QUEUE, {"hello": "world"})

    assert result is False


def test_enqueue_sync_job_serializes_message(monkeypatch) -> None:
    service = StubQueueServiceClient()
    manager = QueueManager(service_client=service)

    repo_metadata = {"name": "repo-one", "fingerprint": "fp1"}
    job_id = "job-123"
    username = "tester"
    before = datetime.now().timestamp()

    success = manager.enqueue_sync_job(job_id, username, repo_metadata)
    assert success is True

    raw = service.clients[SYNC_QUEUE].messages.pop()
    payload = _decode_message(raw)

    assert payload["job_id"] == job_id
    assert payload["username"] == username
    assert payload["repo_name"] == "repo-one"
    assert payload["fingerprint"] == "fp1"
    assert payload["bundle_cache_key"].endswith(username)
    assert payload["repo"]["name"] == "repo-one"
    queued_at = datetime.fromisoformat(payload["queued_at"].replace("Z", "+00:00"))
    tolerance = max(abs(before) * 1e-3, 1e-3)
    assert queued_at.timestamp() >= before - tolerance


def test_enqueue_training_job_defaults_params(monkeypatch) -> None:
    service = StubQueueServiceClient()
    manager = QueueManager(service_client=service)

    success = manager.enqueue_training_job("tester", [{"name": "repo"}])
    assert success is True

    payload = _decode_message(service.clients[TRAINING_QUEUE].messages.pop())
    assert payload["username"] == "tester"
    assert payload["training_params"] == {}
    assert payload["repos_bundle"][0]["name"] == "repo"


def test_unknown_queue_alias_returns_false(monkeypatch) -> None:
    service = StubQueueServiceClient()
    manager = QueueManager(service_client=service, queue_names={})

    result = manager.send_message("nonexistent", {})
    assert result is False
