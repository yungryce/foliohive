"""Standalone semantic model utilities for the training worker."""
from __future__ import annotations

import importlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TrainingParams:
    """Serializable training configuration."""

    batch_size: int = 8
    epochs: int = 2
    max_pairs: int = 150
    warmup_steps: int = 50
    use_mnrl: bool = True

    @classmethod
    def from_dict(cls, values: Optional[Dict[str, Any]]) -> "TrainingParams":
        merged = {
            "batch_size": 8,
            "epochs": 2,
            "max_pairs": 150,
            "warmup_steps": 50,
            "use_mnrl": True,
        }
        if values:
            merged.update({k: v for k, v in values.items() if k in merged})
        return cls(**merged)


class SemanticModel:
    """Train and persist semantic models independently from Azure Functions."""

    def __init__(
        self,
        base_model: str = "all-MiniLM-L6-v2",
        *,
        blob_connection_string: Optional[str] = None,
        blob_service: Optional[Any] = None,
    ) -> None:
        self.base_model_name = base_model
        self.model = None
        self._whiten_kernel = None
        self._whiten_bias = None

        if blob_service is not None:
            self.blob_service = blob_service
        elif blob_connection_string:
            self.blob_service = self._create_blob_client(blob_connection_string)
        else:
            self.blob_service = None

    # ----------------------------------------------------------------------------------
    # Training helpers
    # ----------------------------------------------------------------------------------
    def _ensure_base_model(self) -> bool:
        if self.model is not None:
            return True
        try:  # Lazy import to avoid hard dependency for tests
            module = importlib.import_module("sentence_transformers")
            sentence_transformer_cls = getattr(module, "SentenceTransformer")
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            logger.error("sentence-transformers not installed: %s", exc)
            return False

        try:
            logger.info("Loading base model: %s", self.base_model_name)
            self.model = sentence_transformer_cls(self.base_model_name)
            return True
        except Exception as exc:  # pragma: no cover - actual model load errors
            logger.error("Failed to load base model: %s", exc, exc_info=True)
            self.model = None
            return False

    def _generate_training_pairs(self, repo_bundle: Dict[str, Any]) -> List[Tuple[str, str, float]]:
        pairs: List[Tuple[str, str, float]] = []
        repo_context = repo_bundle.get("repoContext", {})
        identity = repo_context.get("project_identity", {})
        tech_stack = repo_context.get("tech_stack", {})
        readme = repo_bundle.get("readme") or ""
        skills_index = repo_bundle.get("skills_index") or ""

        if identity.get("name"):
            description = identity.get("description", "")
            question = f"What is {identity['name']}?"
            pairs.append((question, description, 1.0))

        if tech_stack.get("primary"):
            techs = ", ".join(tech_stack["primary"])
            pairs.append(("Which technologies are used?", techs, 0.9))

        if readme.strip():
            snippet = readme.strip().splitlines()
            snippet_text = "\n".join(snippet[:40])[:1000]
            pairs.append(("Summarize the README", snippet_text, 0.85))

        if skills_index.strip():
            pairs.append(("Which skills does the developer highlight?", skills_index, 0.8))

        return pairs

    # ----------------------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------------------
    def train_from_repositories(
        self,
        repos_bundle: List[Dict[str, Any]],
        *,
        output_path: str,
        training_params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        params = TrainingParams.from_dict(training_params)
        training_pairs: List[Tuple[str, str, float]] = []
        for repo in repos_bundle:
            training_pairs.extend(self._generate_training_pairs(repo))

        if not training_pairs:
            logger.warning("No training pairs generated from bundle")
            return False

        if len(training_pairs) > params.max_pairs:
            import random

            random.seed(42)
            training_pairs = random.sample(training_pairs, params.max_pairs)

        if not self._ensure_base_model():
            return False

        try:
            st_module = importlib.import_module("sentence_transformers")
            input_example_cls = getattr(st_module, "InputExample")
            losses = getattr(st_module, "losses")
            torch_data = importlib.import_module("torch.utils.data")
            dataloader_cls = getattr(torch_data, "DataLoader")
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            logger.error("ML dependencies missing: %s", exc)
            return False

        try:
            if params.use_mnrl:
                positives = [(q, c) for q, c, score in training_pairs if score >= 0.8]
                train_examples = [input_example_cls(texts=[q, c]) for q, c in positives]
                loss_obj = losses.MultipleNegativesRankingLoss(self.model)
            else:
                train_examples = [
                    input_example_cls(texts=[q, c], label=score)
                    for q, c, score in training_pairs
                ]
                loss_obj = losses.CosineSimilarityLoss(self.model)

            dataloader = dataloader_cls(
                train_examples,
                batch_size=params.batch_size,
                shuffle=True,
                num_workers=0,
            )
            self.model.fit(
                train_objectives=[(dataloader, loss_obj)],
                epochs=params.epochs,
                warmup_steps=params.warmup_steps,
                show_progress_bar=False,
            )

            output_dir = Path(output_path)
            output_dir.mkdir(parents=True, exist_ok=True)
            self.model.save(str(output_dir))
            logger.info("Model artifacts saved to %s", output_dir)
            return True
        except Exception as exc:  # pragma: no cover - real training failure
            logger.error("Training failed: %s", exc, exc_info=True)
            return False

    def upload_to_blob(
        self,
        local_model_path: str,
        blob_name: str,
        container_name: str = "github-cache",
    ) -> None:
        if not self.blob_service:
            raise ValueError("Blob service client is not configured")

        tmp_dir = tempfile.mkdtemp()
        zip_path = Path(tmp_dir) / blob_name
        try:
            self._zip_directory(Path(local_model_path), zip_path)
            container = self.blob_service.get_container_client(container_name)
            blob_client = container.get_blob_client(blob_name)
            with open(zip_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)
            logger.info("Uploaded model artifact to %s/%s", container_name, blob_name)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ----------------------------------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------------------------------
    @staticmethod
    def _zip_directory(source: Path, destination: Path) -> None:
        import zipfile

        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
            for file_path in source.rglob("*"):
                if file_path.is_file():
                    zipf.write(file_path, file_path.relative_to(source))

    # ----------------------------------------------------------------------------------
    # Serialization helpers
    # ----------------------------------------------------------------------------------
    @staticmethod
    def serialize_metadata(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, sort_keys=True)

    @staticmethod
    def _create_blob_client(connection_string: str) -> Any:
        try:
            module = importlib.import_module("azure.storage.blob")
            blob_service_client_cls = getattr(module, "BlobServiceClient")
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            raise ImportError(
                "azure-storage-blob must be installed to persist models"
            ) from exc
        return blob_service_client_cls.from_connection_string(connection_string)
