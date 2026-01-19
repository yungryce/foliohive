"""Merge worker blueprint.

Consumes messages from the ``merge-results`` queue, hydrates repository bundles
from cache, merges them with any existing cached bundle, and enqueues a
background training job once the consolidated bundle is saved.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import azure.functions as func

from cloudfolio_shared import cache_manager, FingerprintManager, queue_manager, table_manager

logger = logging.getLogger("cloudfolio.merge")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

BUNDLE_TTL_SECONDS = 3600
TRAINING_PARAMS = {"batch_size": 8, "epochs": 2}


def _deserialize_message(msg: func.QueueMessage) -> Dict[str, Any]:
    body_bytes = msg.get_body()
    body_str = body_bytes.decode("utf-8") if isinstance(body_bytes, (bytes, bytearray)) else str(body_bytes)
    if not body_str or not body_str.strip():
        logger.error("Received empty message body")
        raise ValueError("Queue message body is empty")

    payload = json.loads(body_str)
    job_id = payload.get("job_id", "unknown")
    logger.info("[RECV_DEBUG] job=%s", job_id)

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
    _ = job_id
    bundle_key = cache_manager.generate_cache_key(kind="bundle", username=username)
    result = cache_manager.get(bundle_key)
    if result.get("status") == "valid" and isinstance(result.get("data"), list):
        logger.info(
            "[MERGE_LOAD] user=%s job=%s source=bundle_cache repos=%d",
            username,
            job_id or "<unknown>",
            len(result.get("data") or []),
        )
        return list(result["data"])
    return []


def _load_repos_from_cache(username: str, repo_names: Iterable[str], job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _ = job_id
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


def _load_repos_from_table(username: str, repo_names: Iterable[str], job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    names = [name for name in repo_names if name]
    if not names:
        return []
    if not table_manager.is_enabled():
        return []

    rows = table_manager.query_repo_metadata(username, job_id=job_id, repo_names=names)
    documents: List[Dict[str, Any]] = []
    for row in rows:
        doc = row.get("document") if isinstance(row.get("document"), dict) else {}
        repo_name = row.get("repo_name") or row.get("RowKey") or doc.get("name")
        entry: Dict[str, Any] = dict(doc)
        if repo_name and "name" not in entry:
            entry["name"] = repo_name
        if "fingerprint" not in entry and row.get("fingerprint"):
            entry["fingerprint"] = row.get("fingerprint")

        if "metadata" not in entry and isinstance(row.get("metadata"), dict):
            entry["metadata"] = row.get("metadata")
        if "languages" not in entry and isinstance(row.get("languages"), dict):
            entry["languages"] = row.get("languages")
        if "categorized_types" not in entry and isinstance(row.get("categorized_types"), dict):
            entry["categorized_types"] = row.get("categorized_types")

        if "has_documentation" not in entry and row.get("has_documentation") is not None:
            entry["has_documentation"] = bool(row.get("has_documentation"))
        if "readme_excerpt" not in entry and row.get("readme_excerpt"):
            entry["readme_excerpt"] = row.get("readme_excerpt")
        if "content_blob" not in entry and row.get("content_blob"):
            entry["content_blob"] = row.get("content_blob")

        documents.append(entry)
    if documents:
        logger.info(
            "[MERGE_LOAD] user=%s job=%s source=repo_table repos=%d",
            username,
            job_id or "<unknown>",
            len(documents),
        )
        return documents
    return []


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


def _save_bundle(username: str, bundle: List[Dict[str, Any]], fingerprint: str) -> str:
    cache_key = cache_manager.generate_cache_key(kind="bundle", username=username)
    cache_manager.save(cache_key, bundle, ttl=None, fingerprint=fingerprint)
    logger.info("Saved merged bundle for %s (%s repos)", username, len(bundle))
    return cache_key


def _update_job_status(job_id: str, username: str, synced_repo_names: List[str], fingerprint: str) -> None:
    merged_count = len(synced_repo_names)
    now = datetime.now(timezone.utc).isoformat()

    if table_manager.is_enabled():
        existing = table_manager.get_candidate_session(username, job_id) or {}
        total_repos = existing.get("total_repos", merged_count)
        table_manager.update_candidate_session(
            username,
            job_id,
            {
                "completed_repos": merged_count,
                "total_repos": max(int(total_repos or 0), merged_count),
                "status": "completed",
                "bundle_fingerprint": fingerprint,
                "completed_at": now,
                "synced_repos": list(synced_repo_names),
            },
        )
        return


def _enqueue_training_job(
    *,
    username: str,
    job_id: str,
    bundle_cache_key: str,
    repo_names: List[str],
    bundle_fingerprint: str,
) -> None:
    if not bundle_cache_key:
        logger.info("Skip training enqueue for %s job=%s (missing bundle cache key)", username, job_id)
        return
    if not queue_manager.is_enabled():
        logger.warning("Queue manager disabled; training job skipped for %s job=%s", username, job_id)
        return
    queued = queue_manager.enqueue_training_job(
        username=username,
        bundle_cache_key=bundle_cache_key,
        training_params=TRAINING_PARAMS,
        job_id=job_id,
        repo_names=repo_names,
        bundle_fingerprint=bundle_fingerprint,
        experiment_name=os.getenv("TRAINING_EXPERIMENT", "default"),
    )
    if queued:
        logger.info("Training job enqueued for %s job=%s", username, job_id)
    else:
        logger.warning("Failed to enqueue training job for %s job=%s", username, job_id)


def _resolve_fresh_repos(payload: Dict[str, Any], username: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    fresh = payload.get("fresh_repos")
    if isinstance(fresh, list) and fresh:
        return [repo for repo in fresh if isinstance(repo, dict)]
    repo_names: Iterable[str] = payload.get("synced_repos") or payload.get("repo_names") or []
    if not repo_names:
        if job_id and table_manager.is_enabled():
            session = table_manager.get_candidate_session(username, job_id) or {}
            session_synced = session.get("synced_repos") or []
            if isinstance(session_synced, list) and session_synced:
                repo_names = session_synced
            else:
                status_rows = table_manager.list_repo_statuses(job_id)
                if status_rows:
                    repo_names = [row.get("repo_name") for row in status_rows if row.get("status") == "synced"]
    table_repos = _load_repos_from_table(username, repo_names, job_id)
    if table_repos:
        return table_repos
    return []


def _resolve_cached_bundle(payload: Dict[str, Any], username: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    cached = payload.get("cached_bundle")
    if isinstance(cached, list) and cached:
        return [repo for repo in cached if isinstance(repo, dict)]
    return []


def _process_merge_payload(payload: Dict[str, Any]) -> bool:
    username = payload.get("username")
    job_id = payload.get("job_id")
    if not username or not job_id:
        raise ValueError("username and job_id are required")

    repo_names_hint = payload.get("repo_names") or payload.get("synced_repos") or []
    logger.info(
        "[MERGE_START] job=%s user=%s repo_names_hint=%d has_fresh=%s has_cached=%s",
        job_id,
        username,
        len(repo_names_hint) if isinstance(repo_names_hint, list) else 0,
        bool(payload.get("fresh_repos")),
        bool(payload.get("cached_bundle")),
    )

    fresh_repos = _resolve_fresh_repos(payload, username, job_id)
    cached_bundle = _resolve_cached_bundle(payload, username, job_id)
    if not fresh_repos and not cached_bundle:
        logger.warning("Nothing to merge for job %s", job_id)
        return False

    merged_bundle = _merge_repos(fresh_repos, cached_bundle)
    logger.info(
        "[MERGE_COMBINED] job=%s user=%s fresh=%d cached=%d merged=%d",
        job_id,
        username,
        len(fresh_repos),
        len(cached_bundle),
        len(merged_bundle),
    )
    fingerprint = _calculate_bundle_fingerprint(merged_bundle)
    bundle_cache_key = _save_bundle(username, merged_bundle, fingerprint)
    synced_repo_names = [name for name in (_extract_repo_name(repo) for repo in merged_bundle) if name]
    logger.info(
        "[MERGE_SAVE] job=%s user=%s synced_repos=%d",
        job_id,
        username,
        len(synced_repo_names),
    )
    _update_job_status(job_id, username, synced_repo_names, fingerprint)
    _enqueue_training_job(
        username=username,
        job_id=job_id,
        bundle_cache_key=bundle_cache_key,
        repo_names=synced_repo_names,
        bundle_fingerprint=fingerprint,
    )
    return True


@bp.queue_trigger(arg_name="msg", queue_name="merge-results", connection="AzureWebJobsStorage")
def process_merge_job(msg: func.QueueMessage) -> None:
    try:
        payload = _deserialize_message(msg)
        logger.info(
            "[MERGE_MESSAGE] job=%s user=%s keys=%s",
            payload.get("job_id") or "<unknown>",
            payload.get("username") or "<unknown>",
            sorted(list(payload.keys())),
        )
        _process_merge_payload(payload)
    except Exception as exc:  # pragma: no cover
        logger.error("Merge worker failure: %s", exc, exc_info=True)
        raise
