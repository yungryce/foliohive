"""Edge case tests for training worker per plan-modelTraining.prompt.md."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


class FakeBlobClient:
    def __init__(self):
        self.uploads = []
        self._downloads = {}
        self._name = ""

    def upload_blob(self, data, overwrite=False):
        if hasattr(data, "read"):
            payload = data.read()
        else:
            payload = data
        self.uploads.append(payload)

    def download_blob(self):
        class _Downloader:
            def __init__(self, raw):
                self._raw = raw

            def readall(self):
                return self._raw

        raw = self._downloads.get(self._name, b"[]")
        return _Downloader(raw)


class FakeContainer:
    def __init__(self):
        self.last_blob = None
        self.blob_client = FakeBlobClient()

    def get_blob_client(self, name):
        self.last_blob = name
        self.blob_client._name = name
        return self.blob_client

    def seed_download(self, name: str, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.blob_client._downloads[name] = raw


class FakeBlobService:
    def __init__(self):
        self.last_container = None
        self.container = FakeContainer()

    def get_container_client(self, name):
        self.last_container = name
        return self.container


class FakeQueue:
    def receive_messages(self, *_, **__):
        return []

    def delete_message(self, *_):
        # No-op for unit tests; Azure queue receipts not tracked
        pass


@pytest.fixture
def mock_table_manager():
    """Mock table_manager for testing ModelMetadata persistence."""
    manager = MagicMock()
    manager.is_enabled.return_value = True
    manager.upsert_model_metadata = MagicMock()
    return manager


class TestTrainingWorkerEdgeCases:
    """Test edge cases per plan-modelTraining.prompt.md."""

    def test_insufficient_documented_repos_skips_training(self, monkeypatch):
        """Verify training skipped when < 3 documented repos per plan."""
        import importlib.util
        from pathlib import Path
        
        MODULE_DIR = Path(__file__).resolve().parents[1]
        TRAIN_WORKER_SPEC = importlib.util.spec_from_file_location(
            "training_worker_module", MODULE_DIR / "train_worker.py"
        )
        train_worker_module = importlib.util.module_from_spec(TRAIN_WORKER_SPEC)
        assert TRAIN_WORKER_SPEC and TRAIN_WORKER_SPEC.loader
        TRAIN_WORKER_SPEC.loader.exec_module(train_worker_module)
        TrainingWorker = train_worker_module.TrainingWorker
        
        worker = TrainingWorker(
            queue_client=FakeQueue(),
            blob_service=FakeBlobService(),
            storage_connection_string="UseDevelopmentStorage=true",
        )

        bundle_key = "repos_bundle_context_tester"
        worker.blob_service.container.seed_download(
            bundle_key,
            [
                {"name": "repo-1", "has_documentation": True},
                {"name": "repo-2", "has_documentation": False},
            ],
        )
        
        payload = {"username": "tester", "bundle_cache_key": bundle_key}
        
        result = worker.process_training_job(payload)
        assert result is False  # Should abort training

    def test_training_failure_returns_false(self, monkeypatch):
        """Verify training failure propagates without crashing."""
        import importlib.util
        from pathlib import Path
        
        MODULE_DIR = Path(__file__).resolve().parents[1]
        TRAIN_WORKER_SPEC = importlib.util.spec_from_file_location(
            "training_worker_module", MODULE_DIR / "train_worker.py"
        )
        train_worker_module = importlib.util.module_from_spec(TRAIN_WORKER_SPEC)
        assert TRAIN_WORKER_SPEC and TRAIN_WORKER_SPEC.loader
        TRAIN_WORKER_SPEC.loader.exec_module(train_worker_module)
        TrainingWorker = train_worker_module.TrainingWorker
        
        class FailingSemanticModel:
            def __init__(self, *args, **kwargs):
                pass
            
            def train_from_repositories(self, repos_bundle, output_path, training_params):
                return False  # Simulate training failure
        
        monkeypatch.setattr(train_worker_module, "SemanticModel", FailingSemanticModel)
        
        worker = TrainingWorker(
            queue_client=FakeQueue(),
            blob_service=FakeBlobService(),
            storage_connection_string="UseDevelopmentStorage=true",
        )

        bundle_key = "repos_bundle_context_tester"
        worker.blob_service.container.seed_download(
            bundle_key,
            [
                {"name": "a", "has_documentation": True},
                {"name": "b", "has_documentation": True},
                {"name": "c", "has_documentation": True},
            ],
        )
        
        payload = {"username": "tester", "bundle_cache_key": bundle_key}
        
        result = worker.process_training_job(payload)
        assert result is False

    def test_table_manager_disabled_graceful_degradation(self, monkeypatch, mock_table_manager):
        """Verify worker continues when table_manager disabled per architectural plan."""
        import importlib.util
        from pathlib import Path
        
        MODULE_DIR = Path(__file__).resolve().parents[1]
        TRAIN_WORKER_SPEC = importlib.util.spec_from_file_location(
            "training_worker_module", MODULE_DIR / "train_worker.py"
        )
        train_worker_module = importlib.util.module_from_spec(TRAIN_WORKER_SPEC)
        assert TRAIN_WORKER_SPEC and TRAIN_WORKER_SPEC.loader
        TRAIN_WORKER_SPEC.loader.exec_module(train_worker_module)
        TrainingWorker = train_worker_module.TrainingWorker
        
        # Simulate table_manager disabled
        mock_table_manager.is_enabled.return_value = False
        
        class StubSemanticModel:
            def __init__(self, *args, **kwargs):
                pass
            
            def train_from_repositories(self, repos_bundle, output_path, training_params):
                return True
            
            def upload_to_blob(self, **kwargs):
                pass
        
        monkeypatch.setattr(train_worker_module, "SemanticModel", StubSemanticModel)
        monkeypatch.setattr(train_worker_module, "table_manager", mock_table_manager)
        
        worker = TrainingWorker(
            queue_client=FakeQueue(),
            blob_service=FakeBlobService(),
            storage_connection_string="UseDevelopmentStorage=true",
        )

        bundle_key = "repos_bundle_context_tester"
        worker.blob_service.container.seed_download(
            bundle_key,
            [
                {"name": "a", "has_documentation": True},
                {"name": "b", "has_documentation": True},
                {"name": "c", "has_documentation": True},
            ],
        )
        
        payload = {"username": "tester", "bundle_cache_key": bundle_key}
        
        result = worker.process_training_job(payload)
        
        # Should succeed despite table_manager disabled
        assert result is True
        # Should not have called upsert_model_metadata
        assert not mock_table_manager.upsert_model_metadata.called

    def test_model_metadata_persisted_to_table(self, monkeypatch, mock_table_manager):
        """Verify ModelMetadata row created per plan-modelTraining.prompt.md."""
        import importlib.util
        from pathlib import Path
        
        MODULE_DIR = Path(__file__).resolve().parents[1]
        TRAIN_WORKER_SPEC = importlib.util.spec_from_file_location(
            "training_worker_module", MODULE_DIR / "train_worker.py"
        )
        train_worker_module = importlib.util.module_from_spec(TRAIN_WORKER_SPEC)
        assert TRAIN_WORKER_SPEC and TRAIN_WORKER_SPEC.loader
        TRAIN_WORKER_SPEC.loader.exec_module(train_worker_module)
        TrainingWorker = train_worker_module.TrainingWorker
        
        class StubSemanticModel:
            def __init__(self, *args, **kwargs):
                pass
            
            def train_from_repositories(self, repos_bundle, output_path, training_params):
                return True
            
            def upload_to_blob(self, **kwargs):
                pass
        
        monkeypatch.setattr(train_worker_module, "SemanticModel", StubSemanticModel)
        monkeypatch.setattr(train_worker_module, "table_manager", mock_table_manager)
        
        # Mock ModelMetadataRow
        class FakeModelMetadataRow:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        
        monkeypatch.setattr(train_worker_module, "ModelMetadataRow", FakeModelMetadataRow)
        
        worker = TrainingWorker(
            queue_client=FakeQueue(),
            blob_service=FakeBlobService(),
            storage_connection_string="UseDevelopmentStorage=true",
        )

        bundle_key = "repos_bundle_context_tester"
        worker.blob_service.container.seed_download(
            bundle_key,
            [
                {"name": "a", "has_documentation": True},
                {"name": "b", "has_documentation": True},
                {"name": "c", "has_documentation": True},
            ],
        )
        
        payload = {
            "username": "tester",
            "bundle_cache_key": bundle_key,
            "experiment_name": "fast",
            "training_params": {"epochs": 1},
        }
        
        result = worker.process_training_job(payload)
        
        assert result is True
        # Verify table write was called
        assert mock_table_manager.upsert_model_metadata.called
        call_args = mock_table_manager.upsert_model_metadata.call_args[0][0]
        assert call_args.username == "tester"
        assert call_args.experiment_name == "fast"
        assert call_args.repos_count == 3

    def test_missing_username_raises_error(self, monkeypatch):
        """Verify missing username in payload raises ValueError."""
        import importlib.util
        from pathlib import Path
        
        MODULE_DIR = Path(__file__).resolve().parents[1]
        TRAIN_WORKER_SPEC = importlib.util.spec_from_file_location(
            "training_worker_module", MODULE_DIR / "train_worker.py"
        )
        train_worker_module = importlib.util.module_from_spec(TRAIN_WORKER_SPEC)
        assert TRAIN_WORKER_SPEC and TRAIN_WORKER_SPEC.loader
        TRAIN_WORKER_SPEC.loader.exec_module(train_worker_module)
        TrainingWorker = train_worker_module.TrainingWorker
        
        worker = TrainingWorker(
            queue_client=FakeQueue(),
            blob_service=FakeBlobService(),
            storage_connection_string="UseDevelopmentStorage=true",
        )
        
        payload = {
            "bundle_cache_key": "repos_bundle_context_tester",
        }
        
        with pytest.raises(ValueError, match="username missing"):
            worker.process_training_job(payload)

    def test_blob_404_handling(self, monkeypatch):
        """Verify worker tolerates missing ephemeral blobs per architectural plan."""
        # Per plan-modelTraining.prompt.md section 3.3:
        # "blobs are deleted automatically after lifecycle TTL, so the worker must
        # tolerate 404 and request a re-sync via table_manager note if missing."
        # 
        # Current implementation reads from bundle_cache_key, but does not yet
        # implement retry + re-sync signalling on missing blobs.
        pass
