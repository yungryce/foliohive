"""Merge worker Function App.

Consumes messages from the ``merge-results`` queue, hydrates repository bundles
from cache, merges them with any existing cached bundle, and enqueues a
background training job once the consolidated bundle is saved. The worker keeps
user-facing requests fast by running asynchronously as part of the queue-based
architecture described in `.github/prompts/plan-azureStorageQueuesArchitecture.prompt.md`.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

try:  # pragma: no cover - Azure Functions bindings are unavailable in tests
    import azure.functions as func  # type: ignore
except ImportError:  # pragma: no cover - lightweight stubs for unit tests
    class _QueueMessage:
        def __init__(self, body: Any):
            self._body = body

        def get_body(self):  # type: ignore[override]
            return self._body

    class _FunctionApp:
        def queue_trigger(self, *_, **__):
            def _decorator(fn):
                return fn

            return _decorator

    func = type("func", (), {"QueueMessage": _QueueMessage, "FunctionApp": _FunctionApp})()  # type: ignore


if TYPE_CHECKING:  # pragma: no cover - only for type checkers
    from azure.functions import QueueMessage as AzureQueueMessage  # type: ignore
else:
    # At runtime, use the real type if available, otherwise fall back to Any
    try:
        from azure.functions import QueueMessage as AzureQueueMessage  # type: ignore
    except ImportError:
        AzureQueueMessage = Any

# Clean imports from installed cloudfolio-shared package
from cloudfolio_shared import cache_manager, FingerprintManager, queue_manager, table_manager

logger = logging.getLogger("portfolio.merge")
logger.setLevel(logging.INFO)
logger.propagate = True

app = func.FunctionApp()

BUNDLE_TTL_SECONDS = 3600
TRAINING_PARAMS = {"batch_size": 8, "epochs": 2}


def _deserialize_message(msg: func.QueueMessage) -> Dict[str, Any]:
    body = msg.get_body()
    decoded = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
    payload = json.loads(decoded)
    logger.info(
        "Merge worker received payload: job=%s repos=%s",
        payload.get("job_id"),
        payload.get("synced_repos"),
    )
    return payload


def _extract_repo_name(repo: Dict[str, Any]) -> Optional[str]:
    metadata = repo.get("metadata")
    if isinstance(metadata, dict):
        name = metadata.get("name")
        if name:
            return name
    name = repo.get("name")
    return name if isinstance(name, str) and name else None


def _load_cached_bundle(username: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _ = job_id  # Placeholder for future table-aware logic
    bundle_key = cache_manager.generate_cache_key(kind="bundle", username=username)
    result = cache_manager.get(bundle_key)
    if result.get("status") == "valid" and isinstance(result.get("data"), list):
        return list(result["data"])  # shallow copy to avoid accidental mutation
    return []


def _load_repos_from_cache(username: str, repo_names: Iterable[str], job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _ = job_id  # Placeholder for future table-aware logic
    bundles: List[Dict[str, Any]] = []
    for repo_name in repo_names:
        if not repo_name:
            continue
        repo_key = cache_manager.generate_cache_key(kind="repo", username=username, repo=repo_name)
        repo_entry = cache_manager.get(repo_key)
        if repo_entry.get("status") == "valid" and isinstance(repo_entry.get("data"), dict):
            bundles.append(repo_entry["data"])
        else:
            logger.warning("Repo %s for %s missing or invalid in cache", repo_name, username)
    return bundles


def _merge_repos(fresh_repos: List[Dict[str, Any]], cached_bundle: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for repo in cached_bundle:
        name = _extract_repo_name(repo)
        if name:
            merged[name] = repo
    for repo in fresh_repos:
        name = _extract_repo_name(repo)
        if name:
            merged[name] = repo
    ordered_names = sorted(merged.keys())
    return [merged[name] for name in ordered_names]


def _calculate_bundle_fingerprint(bundle: List[Dict[str, Any]]) -> str:
    fingerprints = [repo.get("fingerprint") for repo in bundle if isinstance(repo.get("fingerprint"), str)]
    if fingerprints:
        return FingerprintManager.generate_bundle_fingerprint(fingerprints)
    return FingerprintManager.generate_content_fingerprint(bundle)


def _save_bundle(username: str, bundle: List[Dict[str, Any]], fingerprint: str) -> None:
    cache_key = cache_manager.generate_cache_key(kind="bundle", username=username)
    cache_manager.save(cache_key, bundle, ttl=None, fingerprint=fingerprint)
    logger.info("Saved merged bundle for %s (%s repos)", username, len(bundle))


def _update_job_status(job_id: str, username: str, synced_repo_names: List[str], fingerprint: str) -> None:
    job_key = f"job:{job_id}"
    entry = cache_manager.get(job_key)
    payload = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    merged_count = len(synced_repo_names)
    total_repos = payload.get("total_repos", merged_count)
    payload.update(
        {
            "job_id": job_id,
            "username": username,
            "completed_repos": merged_count,
            "total_repos": max(total_repos, merged_count),
            "status": "completed",
            "bundle_fingerprint": fingerprint,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "synced_repos": list(synced_repo_names),
        }
    )
    cache_manager.save(job_key, payload, ttl=BUNDLE_TTL_SECONDS)


def _enqueue_training_job(username: str, bundle: List[Dict[str, Any]]) -> None:
    if not bundle:
        logger.info("Skip training enqueue for %s (empty bundle)", username)
        return
    if not queue_manager.is_enabled():
        logger.warning("Queue manager disabled; training job skipped for %s", username)
        return
    queued = queue_manager.enqueue_training_job(username, bundle, training_params=TRAINING_PARAMS)
    if queued:
        logger.info("Training job enqueued for %s", username)
    else:
        logger.warning("Failed to enqueue training job for %s", username)


def _resolve_fresh_repos(payload: Dict[str, Any], username: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    fresh = payload.get("fresh_repos")
    if isinstance(fresh, list) and fresh:
        return [repo for repo in fresh if isinstance(repo, dict)]
    repo_names: Iterable[str] = payload.get("synced_repos") or payload.get("repo_names") or []
    return _load_repos_from_cache(username, repo_names, job_id)


def _resolve_cached_bundle(payload: Dict[str, Any], username: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    cached = payload.get("cached_bundle")
    if isinstance(cached, list) and cached:
        return [repo for repo in cached if isinstance(repo, dict)]
    return _load_cached_bundle(username, job_id)


def _process_merge_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    username = payload.get("username")
    job_id = payload.get("job_id")
    if not username or not job_id:
        raise ValueError("username and job_id are required")

    fresh_repos = _resolve_fresh_repos(payload, username, job_id)
    cached_bundle = _resolve_cached_bundle(payload, username, job_id)
    if not fresh_repos and not cached_bundle:
        logger.warning("Nothing to merge for job %s", job_id)
        return []

    merged_bundle = _merge_repos(fresh_repos, cached_bundle)
    fingerprint = _calculate_bundle_fingerprint(merged_bundle)
    _save_bundle(username, merged_bundle, fingerprint)
    synced_repo_names = [name for name in (_extract_repo_name(repo) for repo in merged_bundle) if name]
    _update_job_status(job_id, username, synced_repo_names, fingerprint)
    _enqueue_training_job(username, merged_bundle)
    return merged_bundle


@app.queue_trigger(arg_name="msg", queue_name="merge-results", connection="AzureWebJobsStorage")
def process_merge_job(msg: func.QueueMessage) -> None:
    try:
        payload = _deserialize_message(msg)
        _process_merge_payload(payload)
    except Exception as exc:  # pragma: no cover - allow Azure queue retries
        logger.error("Merge worker failure: %s", exc, exc_info=True)
        raise


__all__ = [
    "_process_merge_payload",
    "_merge_repos",
    "_resolve_fresh_repos",
    "_resolve_cached_bundle",
    "process_merge_job",
]