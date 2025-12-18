"""Container-friendly training worker that processes model-training queue messages."""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from cloudfolio_shared import table_manager
    from cloudfolio_shared.table import ModelMetadataRow
except ImportError:  # pragma: no cover - graceful degradation for local dev without shared package
    table_manager = None
    ModelMetadataRow = None

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:  # pragma: no cover - runtime convenience
    sys.path.append(str(MODULE_DIR))

MODEL_REGISTRY_SPEC = importlib.util.spec_from_file_location(
    "training_worker_model_registry", MODULE_DIR / "models" / "model_registry.py"
)
SEMANTIC_MODEL_SPEC = importlib.util.spec_from_file_location(
    "training_worker_semantic_model", MODULE_DIR / "models" / "semantic_model.py"
)
_model_registry = importlib.util.module_from_spec(MODEL_REGISTRY_SPEC)
_semantic_model = importlib.util.module_from_spec(SEMANTIC_MODEL_SPEC)
assert MODEL_REGISTRY_SPEC and MODEL_REGISTRY_SPEC.loader
assert SEMANTIC_MODEL_SPEC and SEMANTIC_MODEL_SPEC.loader
MODEL_REGISTRY_SPEC.loader.exec_module(_model_registry)
SEMANTIC_MODEL_SPEC.loader.exec_module(_semantic_model)

get_model_config = _model_registry.get_model_config
SemanticModel = _semantic_model.SemanticModel

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

DEFAULT_QUEUE_NAME = "model-training"
DEFAULT_CONTAINER_NAME = "github-cache"


class TrainingWorker:
    """Polling worker that trains semantic models based on queue messages."""

    def __init__(
        self,
        *,
        queue_client: Optional[Any] = None,
        blob_service: Optional[Any] = None,
        storage_connection_string: Optional[str] = None,
        queue_name: str = DEFAULT_QUEUE_NAME,
        container_name: str = DEFAULT_CONTAINER_NAME,
        training_mode: Optional[str] = None,
    ) -> None:
        self.connection_string = (
            storage_connection_string
            or os.getenv("AZURE_STORAGE_CONNECTION_STRING")
            or os.getenv("AzureWebJobsStorage")
        )
        self.queue_name = queue_name
        self.container_name = container_name
        self.training_mode = training_mode or os.getenv("TRAINING_MODE", "serverless")

        if queue_client is not None:
            self.queue_client = queue_client
        else:
            if not self.connection_string:
                raise ValueError("AZURE_STORAGE_CONNECTION_STRING is required")
            self.queue_client = self._create_queue_client(
                self.connection_string, self.queue_name
            )

        if blob_service is not None:
            self.blob_service = blob_service
        else:
            if not self.connection_string:
                raise ValueError("AZURE_STORAGE_CONNECTION_STRING is required")
            self.blob_service = self._create_blob_service(self.connection_string)

        logger.info(
            "Training worker initialised (queue=%s, mode=%s)",
            self.queue_name,
            self.training_mode,
        )

    # ----------------------------------------------------------------------------------
    # Core processing
    # ----------------------------------------------------------------------------------
    def process_training_job(self, message: Dict[str, Any]) -> bool:
        username = message.get("username")
        repos_bundle = message.get("repos_bundle")
        if not isinstance(repos_bundle, list):
            repos_bundle = None
        bundle_cache_key = message.get("bundle_cache_key")
        experiment_name = message.get("experiment_name", "default")
        custom_params = message.get("training_params") or {}

        if not username:
            raise ValueError("username missing from training job")

        if repos_bundle is None:
            if not isinstance(bundle_cache_key, str) or not bundle_cache_key.strip():
                raise ValueError("bundle_cache_key missing from training job")
            repos_bundle = self._load_bundle_from_blob(bundle_cache_key.strip())
            logger.info(
                "Loaded training bundle from blob (%s repos) user=%s job=%s",
                len(repos_bundle),
                username,
                message.get("job_id", "unknown"),
            )

        documented_repos = [repo for repo in repos_bundle if repo.get("has_documentation")]
        if len(documented_repos) < 3:
            logger.warning("Not enough documented repositories for %s", username)
            return False

        config = get_model_config(experiment_name)
        training_params = {**config["params"], **custom_params}
        fingerprint = self._generate_fingerprint(documented_repos)
        output_dir = f"/tmp/model_{fingerprint}"

        semantic_model = SemanticModel(
            base_model=config["base_model"],
            blob_connection_string=self.connection_string,
            blob_service=self.blob_service,
        )

        success = semantic_model.train_from_repositories(
            documented_repos,
            output_path=output_dir,
            training_params=training_params,
        )
        if not success:
            return False

        blob_name = f"model_{fingerprint}.zip"
        semantic_model.upload_to_blob(
            local_model_path=output_dir,
            blob_name=blob_name,
            container_name=self.container_name,
        )

        metadata = self._build_metadata(
            fingerprint=fingerprint,
            username=username,
            experiment_name=experiment_name,
            training_params=training_params,
            repos=documented_repos,
        )
        self._save_metadata_blob(metadata)
        self._save_metadata_table(username, fingerprint, experiment_name, metadata)
        logger.info("Training job completed for %s (%s)", username, fingerprint[:8])
        return True

    # ----------------------------------------------------------------------------------
    # Loop
    # ----------------------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - polling loop exercised indirectly
        logger.info("Polling queue '%s' (mode=%s)", self.queue_name, self.training_mode)
        while True:
            handled = self._process_messages_once()
            if self.training_mode == "serverless":
                if not handled:
                    logger.info("No messages available; exiting serverless container")
                return
            time.sleep(30)

    def _process_messages_once(self) -> bool:
        handled_message = False
        for message in self._receive_messages():
            handled_message = True
            try:
                payload = json.loads(message.content)
                logger.info(
                    "Processing training job for %s job=%s",
                    payload.get("username", "unknown"),
                    payload.get("job_id", "unknown"),
                )
                success = self.process_training_job(payload)
                if success:
                    self._delete_message(message)
                else:
                    logger.error("Training failed; message will become visible again")
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("Training job crashed: %s", exc, exc_info=True)
        return handled_message

    # ----------------------------------------------------------------------------------
    # Azure helpers
    # ----------------------------------------------------------------------------------
    def _receive_messages(self) -> Iterable[Any]:
        return self.queue_client.receive_messages(max_messages=1, visibility_timeout=600)

    def _delete_message(self, message: Any) -> None:
        if hasattr(message, "id") and hasattr(message, "pop_receipt"):
            self.queue_client.delete_message(message.id, message.pop_receipt)
        else:  # Allow fake messages in unit tests
            self.queue_client.delete_message(message)

    def _save_metadata_blob(self, metadata: Dict[str, Any]) -> None:
        """Save metadata to blob storage for historical audit and debugging."""
        container = self.blob_service.get_container_client(self.container_name)
        blob_name = f"model_metadata_{metadata['fingerprint']}.json"
        blob = container.get_blob_client(blob_name)
        blob.upload_blob(json.dumps(metadata, indent=2), overwrite=True)

    def _save_metadata_table(self, username: str, fingerprint: str, experiment_name: str, metadata: Dict[str, Any]) -> None:
        """Persist model metadata to Azure Tables via table_manager per plan-modelTraining.prompt.md."""
        if not table_manager or not ModelMetadataRow:
            logger.warning("table_manager unavailable; skipping table metadata write")
            return
        if not table_manager.is_enabled():
            logger.info("Table storage disabled; model metadata only in blobs")
            return
        
        try:
            row = ModelMetadataRow(
                username=username,
                model_fingerprint=fingerprint,
                experiment_name=experiment_name,
                trained_at=metadata.get('timestamp') or datetime.now(timezone.utc).isoformat(),
                repos_count=metadata.get('repos_count', 0),
                repo_names=metadata.get('repo_names', []),
                training_params=metadata.get('training_params', {}),
            )
            table_manager.upsert_model_metadata(row)
            logger.info("Model metadata written to table for %s (%s)", username, fingerprint[:8])
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.error("Failed to write model metadata to table: %s", exc, exc_info=True)

    # ----------------------------------------------------------------------------------
    # Utility helpers
    # ----------------------------------------------------------------------------------
    @staticmethod
    def _generate_fingerprint(bundle: Any) -> str:
        import hashlib

        payload = json.dumps(bundle, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_metadata(
        *,
        fingerprint: str,
        username: str,
        experiment_name: str,
        training_params: Dict[str, Any],
        repos: Any,
    ) -> Dict[str, Any]:
        return {
            "fingerprint": fingerprint,
            "username": username,
            "experiment_name": experiment_name,
            "training_params": training_params,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repos_count": len(repos),
            "repo_names": [repo.get("name", "unknown") for repo in repos],
        }

    @staticmethod
    def _create_queue_client(connection_string: str, queue_name: str) -> Any:
        try:
            module = importlib.import_module("azure.storage.queue")
            queue_client_cls = getattr(module, "QueueClient")
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            raise ImportError("azure-storage-queue must be installed") from exc
        return queue_client_cls.from_connection_string(
            conn_str=connection_string, queue_name=queue_name
        )

    @staticmethod
    def _create_blob_service(connection_string: str) -> Any:
        try:
            module = importlib.import_module("azure.storage.blob")
            blob_service_client_cls = getattr(module, "BlobServiceClient")
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            raise ImportError("azure-storage-blob must be installed") from exc
        return blob_service_client_cls.from_connection_string(connection_string)

    def _load_bundle_from_blob(self, cache_key: str) -> list[Dict[str, Any]]:
        container = self.blob_service.get_container_client(self.container_name)
        blob = container.get_blob_client(cache_key)
        raw = blob.download_blob().readall()
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError(f"Bundle blob did not contain a list: {cache_key}")
        return [repo for repo in payload if isinstance(repo, dict)]


def main() -> None:
    worker = TrainingWorker()
    worker.run()


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
