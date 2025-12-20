import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_MODEL_SPEC = importlib.util.spec_from_file_location(
    "training_worker_semantic_model", ROOT / "models" / "semantic_model.py"
)
semantic_model_module = importlib.util.module_from_spec(SEMANTIC_MODEL_SPEC)
assert SEMANTIC_MODEL_SPEC and SEMANTIC_MODEL_SPEC.loader
SEMANTIC_MODEL_SPEC.loader.exec_module(semantic_model_module)
SemanticModel = semantic_model_module.SemanticModel


class FakeBlobClient:
    def __init__(self):
        self.uploaded_payloads = []

    def upload_blob(self, data, overwrite=False):
        self.uploaded_payloads.append(data.read())


class FakeContainer:
    def __init__(self):
        self.last_blob_name = None
        self.blob_client = FakeBlobClient()

    def get_blob_client(self, name):
        self.last_blob_name = name
        return self.blob_client


class FakeBlobService:
    def __init__(self):
        self.last_container = None
        self.container = FakeContainer()

    def get_container_client(self, container_name):
        self.last_container = container_name
        return self.container


def test_generate_training_pairs_extracts_signals():
    repo = {
        "repoContext": {
            "project_identity": {"name": "Demo", "description": "Sample repo"},
            "tech_stack": {"primary": ["Python", "Azure"]},
        },
        "readme": "# Heading\nDetails",
        "skills_index": "Python, Azure Functions",
    }
    model = SemanticModel(blob_service=FakeBlobService())

    pairs = model._generate_training_pairs(repo)

    prompts = [pair[0] for pair in pairs]
    assert any("What is Demo" in prompt for prompt in prompts)
    assert any("Which technologies" in prompt for prompt in prompts)
    assert any("Summarize the README" in prompt for prompt in prompts)


def test_upload_to_blob_zips_and_uploads(tmp_path):
    artifact_dir = tmp_path / "model"
    artifact_dir.mkdir()
    (artifact_dir / "config.json").write_text("{}")

    blob_service = FakeBlobService()
    model = SemanticModel(blob_service=blob_service)

    model.upload_to_blob(str(artifact_dir), "model_demo.zip")

    assert blob_service.last_container == "github-cache"
    assert blob_service.container.last_blob_name == "model_demo.zip"
    assert blob_service.container.blob_client.uploaded_payloads


def test_serialize_metadata_is_sorted():
    payload = {"b": 1, "a": 2}
    serialized = SemanticModel.serialize_metadata(payload)
    assert "\n" in serialized
    assert "a" in serialized.splitlines()[1]
