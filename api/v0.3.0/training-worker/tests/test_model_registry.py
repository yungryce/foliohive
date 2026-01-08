import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_REGISTRY_SPEC = importlib.util.spec_from_file_location(
    "training_worker_model_registry", ROOT / "models" / "model_registry.py"
)
model_registry = importlib.util.module_from_spec(MODEL_REGISTRY_SPEC)
assert MODEL_REGISTRY_SPEC and MODEL_REGISTRY_SPEC.loader
MODEL_REGISTRY_SPEC.loader.exec_module(model_registry)


def test_get_model_config_defaults():
    config = model_registry.get_model_config("unknown-experiment")
    assert config["base_model"] == model_registry.MODEL_CONFIGS["default"]["base_model"]


def test_get_model_config_specific():
    config = model_registry.get_model_config("fast")
    assert config["base_model"] == "all-MiniLM-L12-v2"
    assert config["params"]["use_mnrl"] is False
