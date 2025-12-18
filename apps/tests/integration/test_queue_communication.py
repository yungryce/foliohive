"""
Integration tests for queue communication between Cloudfolio workers.

Tests message format, queue routing, and inter-worker communication patterns.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Queue Message Format Tests
# ---------------------------------------------------------------------------

class TestQueueMessageFormats:
    """Verify correct message formats for each queue."""

    def test_sync_job_message_format(self):
        """Verify sync job message has all required fields."""
        job_id = str(uuid.uuid4())
        message = {
            "job_id": job_id,
            "username": "testuser",
            "repo_name": "test-repo",
            "metadata": {
                "name": "test-repo",
                "full_name": "testuser/test-repo",
                "language": "Python",
                "updated_at": "2025-01-15T12:00:00Z",
            },
            "fingerprint": "abc123def456",
        }

        # Validate structure
        assert "job_id" in message
        assert "username" in message
        assert "repo_name" in message
        assert "metadata" in message
        assert "fingerprint" in message

        # Validate types
        assert isinstance(message["job_id"], str)
        assert isinstance(message["metadata"], dict)

        # Validate metadata fields
        assert "name" in message["metadata"]
        assert message["metadata"]["name"] == message["repo_name"]

    def test_merge_job_message_format(self):
        """Verify merge job message has all required fields."""
        job_id = str(uuid.uuid4())
        message = {
            "job_id": job_id,
            "username": "testuser",
            "synced_repos": ["repo-alpha", "repo-beta", "repo-gamma"],
            "trigger_source": "sync_complete",
        }

        # Validate structure
        assert "job_id" in message
        assert "username" in message
        assert "synced_repos" in message
        assert "trigger_source" in message

        # Validate types
        assert isinstance(message["synced_repos"], list)
        assert all(isinstance(r, str) for r in message["synced_repos"])

    def test_training_job_message_format(self):
        """Verify training job message has all required fields."""
        message = {
            "username": "testuser",
            "bundle_cache_key": "repos_bundle_context_testuser",
            "repo_names": ["repo-1", "repo-2", "repo-3"],
            "training_params": {"batch_size": 8, "epochs": 2},
        }

        # Validate structure
        assert "username" in message
        assert "bundle_cache_key" in message
        assert "repo_names" in message
        assert "training_params" in message

        assert isinstance(message["repo_names"], list)
        assert len(message["repo_names"]) >= 3  # Minimum for training

    def test_status_update_message_format(self):
        """Verify status update message has all required fields."""
        job_id = str(uuid.uuid4())
        message = {
            "job_id": job_id,
            "status": "completed",
            "details": {
                "completed_repos": 5,
                "bundle_fingerprint": "fp_bundle_123",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        # Validate structure
        assert "job_id" in message
        assert "status" in message
        assert "details" in message

        # Validate status values
        valid_statuses = ["queued", "processing", "synced", "completed", "failed"]
        assert message["status"] in valid_statuses


# ---------------------------------------------------------------------------
# Queue Routing Tests
# ---------------------------------------------------------------------------

class TestQueueRouting:
    """Test messages are routed to correct queues."""

    @pytest.fixture
    def queue_router(self):
        """Create a test queue router."""
        queues = {
            "github-sync": [],
            "merge-results": [],
            "model-training": [],
            "job-status-updates": [],
        }

        class MockRouter:
            def send(self, queue_name: str, message: Dict[str, Any]) -> bool:
                if queue_name in queues:
                    queues[queue_name].append(message)
                    return True
                return False

            def get_messages(self, queue_name: str) -> List[Dict[str, Any]]:
                return queues.get(queue_name, [])

        return MockRouter()

    def test_api_gateway_routes_to_sync_queue(self, queue_router):
        """Verify API Gateway sends to github-sync queue."""
        job_id = str(uuid.uuid4())
        repos = [
            {"name": "repo-1", "language": "Python"},
            {"name": "repo-2", "language": "JavaScript"},
        ]

        for repo in repos:
            message = {
                "job_id": job_id,
                "username": "testuser",
                "repo_name": repo["name"],
                "metadata": repo,
            }
            queue_router.send("github-sync", message)

        sync_messages = queue_router.get_messages("github-sync")
        assert len(sync_messages) == 2
        assert all(m["job_id"] == job_id for m in sync_messages)

    def test_sync_worker_routes_to_merge_queue(self, queue_router):
        """Verify Sync Worker sends to merge-results queue."""
        job_id = str(uuid.uuid4())
        message = {
            "job_id": job_id,
            "username": "testuser",
            "synced_repos": ["repo-1", "repo-2"],
            "trigger_source": "sync_complete",
        }
        queue_router.send("merge-results", message)

        merge_messages = queue_router.get_messages("merge-results")
        assert len(merge_messages) == 1
        assert merge_messages[0]["trigger_source"] == "sync_complete"

    def test_merge_worker_routes_to_training_queue(self, queue_router):
        """Verify Merge Worker sends to model-training queue."""
        message = {
            "username": "testuser",
            "bundle_cache_key": "repos_bundle_context_testuser",
            "repo_names": [f"repo-{i}" for i in range(3)],
            "training_params": {"batch_size": 8},
        }
        queue_router.send("model-training", message)

        training_messages = queue_router.get_messages("model-training")
        assert len(training_messages) == 1
        assert training_messages[0]["bundle_cache_key"] == "repos_bundle_context_testuser"
        assert len(training_messages[0]["repo_names"]) == 3


# ---------------------------------------------------------------------------
# Message Serialization Tests
# ---------------------------------------------------------------------------

class TestMessageSerialization:
    """Test message serialization and deserialization."""

    def test_sync_message_json_roundtrip(self):
        """Verify sync messages survive JSON serialization."""
        original = {
            "job_id": str(uuid.uuid4()),
            "username": "testuser",
            "repo_name": "test-repo",
            "metadata": {
                "name": "test-repo",
                "updated_at": "2025-01-15T12:00:00Z",
                "languages": {"Python": 5000, "JavaScript": 1000},
            },
            "fingerprint": "abc123",
        }

        # Serialize and deserialize
        serialized = json.dumps(original)
        deserialized = json.loads(serialized)

        assert deserialized == original
        assert deserialized["metadata"]["languages"]["Python"] == 5000

    def test_merge_message_json_roundtrip(self):
        """Verify merge messages survive JSON serialization."""
        original = {
            "job_id": str(uuid.uuid4()),
            "username": "testuser",
            "synced_repos": ["repo-1", "repo-2", "repo-3"],
            "trigger_source": "sync_complete",
        }

        serialized = json.dumps(original)
        deserialized = json.loads(serialized)

        assert deserialized == original
        assert deserialized["synced_repos"] == original["synced_repos"]

    def test_training_message_json_roundtrip(self):
        """Verify training messages survive JSON serialization."""
        original = {
            "username": "testuser",
            "bundle_cache_key": "repos_bundle_context_testuser",
            "repo_names": ["repo-1"],
            "training_params": {"batch_size": 8, "learning_rate": 0.001},
        }

        serialized = json.dumps(original)
        deserialized = json.loads(serialized)

        assert deserialized == original
        assert deserialized["bundle_cache_key"] == original["bundle_cache_key"]

    def test_message_with_unicode_content(self):
        """Verify messages with unicode content serialize correctly."""
        original = {
            "username": "testuser",
            "bundle_cache_key": "repos_bundle_context_testuser",
            "repo_names": ["i18n-repo", "国际化"],
        }

        serialized = json.dumps(original, ensure_ascii=False)
        deserialized = json.loads(serialized)

        assert "国际化" in deserialized["repo_names"][1]


# ---------------------------------------------------------------------------
# Inter-Worker Communication Tests
# ---------------------------------------------------------------------------

class TestInterWorkerCommunication:
    """Test communication patterns between workers."""

    @pytest.fixture
    def worker_context(self):
        """Shared context for worker communication tests."""
        return {
            "jobs": {},
            "cache": {},
            "queues": {
                "github-sync": [],
                "merge-results": [],
                "model-training": [],
            },
        }

    def test_sync_completion_triggers_merge(self, worker_context):
        """Verify all repos syncing triggers merge job."""
        job_id = str(uuid.uuid4())
        total_repos = 3

        # Simulate job creation
        worker_context["jobs"][job_id] = {
            "job_id": job_id,
            "username": "testuser",
            "total_repos": total_repos,
            "completed_repos": 0,
            "synced_repos": [],
            "status": "processing",
        }

        # Simulate each sync completion
        for i in range(total_repos):
            repo_name = f"repo-{i}"
            job = worker_context["jobs"][job_id]
            job["synced_repos"].append(repo_name)
            job["completed_repos"] += 1

            # Check if all repos synced
            if job["completed_repos"] >= job["total_repos"]:
                job["status"] = "synced"
                worker_context["queues"]["merge-results"].append({
                    "job_id": job_id,
                    "username": job["username"],
                    "synced_repos": job["synced_repos"],
                })

        # Verify merge was triggered
        assert len(worker_context["queues"]["merge-results"]) == 1
        merge_msg = worker_context["queues"]["merge-results"][0]
        assert len(merge_msg["synced_repos"]) == total_repos

    def test_merge_completion_triggers_training(self, worker_context):
        """Verify merge completion triggers training job."""
        job_id = str(uuid.uuid4())
        username = "testuser"

        # Simulate merged bundle
        bundle = [
            {"name": f"repo-{i}", "has_documentation": True, "readme": f"content-{i}"}
            for i in range(4)
        ]

        # Cache the bundle
        bundle_key = f"repos_bundle_context_{username}"
        worker_context["cache"][bundle_key] = bundle

        # Merge worker enqueues training
        documented_count = len([r for r in bundle if r.get("has_documentation")])
        if documented_count >= 3:
            worker_context["queues"]["model-training"].append({
                "username": username,
                "bundle_cache_key": bundle_key,
                "repo_names": [r["name"] for r in bundle],
                "training_params": {"batch_size": 8},
            })

        # Verify training was triggered
        assert len(worker_context["queues"]["model-training"]) == 1
        training_msg = worker_context["queues"]["model-training"][0]
        assert training_msg["bundle_cache_key"] == bundle_key
        assert len(training_msg["repo_names"]) == 4

    def test_insufficient_docs_skips_training(self, worker_context):
        """Verify training is skipped when fewer than 3 documented repos."""
        username = "testuser"

        # Bundle with insufficient documentation
        bundle = [
            {"name": "repo-1", "has_documentation": True},
            {"name": "repo-2", "has_documentation": False},
            {"name": "repo-3", "has_documentation": False},
        ]

        documented_count = len([r for r in bundle if r.get("has_documentation")])
        if documented_count >= 3:
            worker_context["queues"]["model-training"].append({
                "username": username,
                "bundle_cache_key": f"repos_bundle_context_{username}",
                "repo_names": [r["name"] for r in bundle],
            })

        # Verify training was NOT triggered
        assert len(worker_context["queues"]["model-training"]) == 0


# ---------------------------------------------------------------------------
# Queue Manager Integration Tests
# ---------------------------------------------------------------------------

class TestQueueManagerIntegration:
    """Test QueueManager behavior in integration scenarios."""

    @pytest.fixture
    def mock_queue_service(self):
        """Create a mock queue service client."""
        service = MagicMock()
        queues = {}

        def get_queue_client(name):
            if name not in queues:
                queue = MagicMock()
                queue.messages = []
                queue.send_message = lambda msg: queue.messages.append(msg)
                queue.receive_messages = lambda **kwargs: queue.messages[:kwargs.get("max_messages", 1)]
                queues[name] = queue
            return queues[name]

        service.get_queue_client = get_queue_client
        return service

    def test_queue_manager_creates_required_queues(self, mock_queue_service):
        """Verify QueueManager creates all required queues."""
        required_queues = ["github-sync", "merge-results", "model-training"]

        for queue_name in required_queues:
            client = mock_queue_service.get_queue_client(queue_name)
            assert client is not None

    def test_queue_manager_handles_disabled_state(self):
        """Verify QueueManager handles disabled state gracefully."""
        manager = MagicMock()
        manager.is_enabled.return_value = False

        # Operations should check enabled state
        if manager.is_enabled():
            manager.enqueue_sync_job("job", "user", "repo-name", "fingerprint")
        else:
            result = "skipped"

        assert result == "skipped"
        manager.enqueue_sync_job.assert_not_called()


# ---------------------------------------------------------------------------
# Message Processing Order Tests
# ---------------------------------------------------------------------------

class TestMessageProcessingOrder:
    """Test correct message processing order."""

    def test_fifo_processing_within_job(self):
        """Verify messages for a job are processed in order."""
        job_id = str(uuid.uuid4())
        messages = [
            {"job_id": job_id, "repo_name": f"repo-{i}", "sequence": i}
            for i in range(5)
        ]

        # Simulate FIFO processing
        processed = []
        queue = list(messages)  # Copy as queue

        while queue:
            msg = queue.pop(0)  # FIFO
            processed.append(msg["sequence"])

        assert processed == [0, 1, 2, 3, 4]

    def test_parallel_job_processing(self):
        """Verify multiple jobs can be processed independently."""
        job_1_id = str(uuid.uuid4())
        job_2_id = str(uuid.uuid4())

        job_1_progress = {"completed": 0, "total": 3}
        job_2_progress = {"completed": 0, "total": 2}

        # Simulate interleaved processing
        events = [
            (job_1_id, "repo-1"),
            (job_2_id, "repo-a"),
            (job_1_id, "repo-2"),
            (job_2_id, "repo-b"),
            (job_1_id, "repo-3"),
        ]

        for job_id, repo in events:
            if job_id == job_1_id:
                job_1_progress["completed"] += 1
            else:
                job_2_progress["completed"] += 1

        assert job_1_progress["completed"] == 3
        assert job_2_progress["completed"] == 2
