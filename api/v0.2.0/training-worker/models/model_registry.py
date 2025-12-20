"""Experiment registry for semantic model training."""
from __future__ import annotations

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "default": {
        "base_model": "all-MiniLM-L6-v2",
        "description": "Fast, lightweight baseline",
        "params": {
            "batch_size": 8,
            "epochs": 2,
            "max_pairs": 150,
            "use_mnrl": True,
        },
    },
    "large": {
        "base_model": "all-mpnet-base-v2",
        "description": "Larger embedding model for accuracy-focused jobs",
        "params": {
            "batch_size": 4,
            "epochs": 3,
            "max_pairs": 200,
            "use_mnrl": True,
        },
    },
    "fast": {
        "base_model": "all-MiniLM-L12-v2",
        "description": "Faster training configuration for quick experiments",
        "params": {
            "batch_size": 16,
            "epochs": 1,
            "max_pairs": 100,
            "use_mnrl": False,
        },
    },
    "experimental-bge": {
        "base_model": "BAAI/bge-small-en-v1.5",
        "description": "State-of-the-art embedding model, GPU recommended",
        "params": {
            "batch_size": 8,
            "epochs": 2,
            "max_pairs": 150,
            "use_mnrl": True,
        },
    },
}


def get_model_config(experiment_name: str) -> Dict[str, Any]:
    """Return configuration for the requested experiment."""
    config = MODEL_CONFIGS.get(experiment_name, MODEL_CONFIGS["default"])
    logger.info("Using model config '%s': %s", experiment_name, config["description"])
    return config
