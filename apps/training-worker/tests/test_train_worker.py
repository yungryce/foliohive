import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAIN_WORKER_SPEC = importlib.util.spec_from_file_location(
    "training_worker_module", ROOT / "train_worker.py"
)
train_worker_module = importlib.util.module_from_spec(TRAIN_WORKER_SPEC)
assert TRAIN_WORKER_SPEC and TRAIN_WORKER_SPEC.loader
TRAIN_WORKER_SPEC.loader.exec_module(train_worker_module)
TrainingWorker = train_worker_module.TrainingWorker


class FakeBlobClient:
    def __init__(self):
        self.uploads = []

    def upload_blob(self, data, overwrite=False):
        if hasattr(data, "read"):
            payload = data.read()
        else:
            payload = data
        self.uploads.append(payload)


class FakeContainer:
    def __init__(self):
        self.last_blob = None
        self.blob_client = FakeBlobClient()

    def get_blob_client(self, name):
        self.last_blob = name
        return self.blob_client


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
        # No-op for unit tests where we do not track Azure receipts.
        pass


def test_process_training_job_success(monkeypatch):
    created_models = []

    class StubSemanticModel:
        def __init__(self, *args, **kwargs):
            self.saved = False
            created_models.append(self)

        def train_from_repositories(self, repos_bundle, output_path, training_params):
            self.repos = repos_bundle
            self.output_path = output_path
            self.training_params = training_params
            return True

        def upload_to_blob(self, **kwargs):
            self.upload_args = kwargs

    monkeypatch.setattr(train_worker_module, "SemanticModel", StubSemanticModel)

    blob_service = FakeBlobService()
    worker = TrainingWorker(
        queue_client=FakeQueue(),
        blob_service=blob_service,
        storage_connection_string="UseDevelopmentStorage=true",
    )

    payload = {
        "username": "tester",
        "repos_bundle": [
            {"name": "a", "has_documentation": True},
            {"name": "b", "has_documentation": True},
            {"name": "c", "has_documentation": True},
        ],
        "training_params": {"epochs": 1},
        "experiment_name": "fast",
    }

    success = worker.process_training_job(payload)

    assert success is True
    assert blob_service.last_container == "github-cache"
    metadata_payload = json.loads(blob_service.container.blob_client.uploads[-1])
    assert metadata_payload["username"] == "tester"
    assert metadata_payload["repos_count"] == 3
    assert created_models and created_models[0].upload_args["blob_name"].startswith("model_")


def test_process_training_job_requires_documented_repos(monkeypatch):
    monkeypatch.setattr(train_worker_module, "SemanticModel", object)
    worker = TrainingWorker(
        queue_client=FakeQueue(),
        blob_service=FakeBlobService(),
        storage_connection_string="UseDevelopmentStorage=true",
    )

    payload = {
        "username": "tester",
        "repos_bundle": [{"name": "a", "has_documentation": True}],
    }

    assert worker.process_training_job(payload) is False
