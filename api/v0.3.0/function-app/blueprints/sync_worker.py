"""Sync worker blueprint.

Consumes messages from the ``github-sync`` queue, fetches repository context
using shared managers, and persists results to the cache. Once all repositories
for a job are processed the worker enqueues a merge job.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import azure.functions as func

from cloudfolio_shared import (
    cache_manager,
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    queue_manager,
    table_manager,
)
from cloudfolio_shared.table import RepoMetadataRow, RepoSyncStatusRow

logger = logging.getLogger("cloudfolio.sync_worker")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

JOB_METADATA_TTL_SECONDS = 4 * 3600
READ_ME_EXCERPT_MAX_CHARS = 4096
STANDARD_CONFIG_FETCH_LIMIT = 20
STANDARD_CONFIG_MAX_CHARS = 4000


def _job_cache_key(job_id: str) -> str:
    return f"job:{job_id}"


def _get_repo_manager(username: str) -> GitHubRepoManager:
    if not username:
        raise ValueError("Username required")
    token = os.getenv("GITHUB_TOKEN")
    api = GitHubAPI(token=token, username=username)
    return GitHubRepoManager(api, username=username)


def _deserialize_message(msg: func.QueueMessage) -> Dict[str, Any]:
    body_bytes = msg.get_body()
    body_str = body_bytes.decode("utf-8") if isinstance(body_bytes, (bytes, bytearray)) else str(body_bytes)
    if not body_str or not body_str.strip():
        logger.error("Received empty message body")
        raise ValueError("Queue message body is empty")

    payload = json.loads(body_str)

    repo_name = payload.get("repo_name", "unknown")
    job_id = payload.get("job_id", "unknown")
    logger.info(
        "[RECV_DEBUG] repo=%s job=%s - Top-level keys: %s",
        repo_name,
        job_id,
        sorted(list(payload.keys())),
    )

    return payload


def _fetch_repo_bundle(job_id: str, username: str, repo_name: str, fingerprint: Optional[str]) -> bool:
    logger.info(
        "[SYNC_FETCH_START] repo=%s job=%s - Beginning fetch (metadata+languages+README+file_types)",
        repo_name,
        job_id,
    )
    repo_manager = _get_repo_manager(username)
    if not repo_name:
        raise ValueError("Repository name missing in message")

    try:
        repo_metadata = repo_manager.get_repo_metadata(username=username, repo=repo_name, include_languages=True)
    except Exception as exc:
        logger.error("Failed to fetch metadata for %s/%s: %s", username, repo_name, exc)
        raise

    resolved_fingerprint = fingerprint or FingerprintManager.generate_metadata_fingerprint(repo_metadata)

    mode = "rest"
    if os.getenv("ENABLE_CONFIG_DISCOVERY_GRAPHQL", "false").lower() == "true":
        mode = "graphql"

    usage_payload: Optional[Dict[str, Any]] = None

    discovery = repo_manager.discover_repo_files(
        username=username,
        repo=repo_name,
        mode=mode,
        limit=STANDARD_CONFIG_FETCH_LIMIT,
        max_chars=STANDARD_CONFIG_MAX_CHARS,
        readme_max_chars=READ_ME_EXCERPT_MAX_CHARS,
    )
    config_files = discovery.get("config_files", {})
    readme_files = discovery.get("readme_files", {})
    readme_content = discovery.get("primary_readme", "")
    config_usage = discovery.get("api_usage")
    if config_usage:
        if hasattr(config_usage, "to_dict"):
            usage_payload = config_usage.to_dict()
        elif isinstance(config_usage, dict):
            usage_payload = config_usage
        else:
            usage_payload = {"raw": config_usage}
    else:
        usage_payload = None

    logger.info(
        "[CONFIG_USAGE] repo=%s job=%s - Config discovery API usage: %s",
        repo_name,
        job_id,
        usage_payload if usage_payload is not None else "none",
    )

    languages_data = repo_metadata.get("languages", {})
    logger.info(
        "[SYNC_API_CALLS] repo=%s job=%s - Using GitHub languages API: %d languages detected",
        repo_name,
        job_id,
        len(languages_data),
    )

    file_types = {}
    categorized_types = {}

    result: Dict[str, Any] = {
        "name": repo_name,
        "metadata": repo_metadata,
        "readme": readme_content,
        "readme_files": readme_files,
        "config_files": config_files,
        "file_types": file_types,
        "categorized_types": categorized_types,
        "fingerprint": resolved_fingerprint,
        "languages": repo_metadata.get("languages", {}),
        "has_documentation": bool(readme_content),
        "api_usage": {"config_discovery": usage_payload} if usage_payload is not None else {},
    }

    logger.info(
        "[SYNC_FETCH_COMPLETE] repo=%s job=%s - Fetch complete: languages=%d file_types=%d config_files=%d",
        repo_name,
        job_id,
        len(languages_data),
        len(file_types),
        len(config_files),
    )

    cache_key = cache_manager.generate_cache_key(kind="repo", username=username, repo=repo_name)
    cache_manager.save(cache_key, result, ttl=None, fingerprint=resolved_fingerprint)
    logger.info("[SYNC_CACHE] repo=%s job=%s - Cached repo data under key=%s", repo_name, job_id, cache_key)

    _persist_repo_metadata(job_id, username, result, cache_key)
    logger.info("***Cached repo %s/%s fingerprint=%s", username, repo_name, resolved_fingerprint)

    return True


def _persist_repo_metadata(job_id: str, username: str, repo_payload: Dict[str, Any], content_blob: str) -> None:
    if not table_manager.is_enabled():
        logger.warning(
            "Table manager disabled; skipping repo metadata persistence for %s/%s",
            username,
            repo_payload.get("name"),
        )
        return

    repo_name = repo_payload.get("name")
    if not repo_name:
        logger.warning("Cannot persist repo metadata without repo_name")
        return

    # Keep Table document minimal: full payload (including config_files) lives in blob cache.
    # Store only essential merge/query metadata. Training worker loads from blob, not Table.
    document = {
        "name": repo_name,
        "fingerprint": repo_payload.get("fingerprint"),
        "has_documentation": repo_payload.get("has_documentation"),
        "api_usage": repo_payload.get("api_usage"),
    }

    row = RepoMetadataRow(
        username=username,
        repo_name=repo_name,
        fingerprint=repo_payload.get("fingerprint"),
        job_id=job_id,
        document=document,
        metadata=repo_payload.get("metadata", {}),
        content_blob=content_blob,
        languages=repo_payload.get("languages", {}),
        categorized_types=repo_payload.get("categorized_types", {}),
        has_documentation=repo_payload.get("has_documentation"),
        readme_excerpt=(repo_payload.get("readme") or "")[:READ_ME_EXCERPT_MAX_CHARS],
        last_synced_at=repo_payload.get("metadata", {}).get("updated_at") if isinstance(repo_payload.get("metadata"), dict) else None,
    )
    table_manager.upsert_repo_metadata(row)


def _load_job_snapshot(job_id: str, username: str) -> Dict[str, Any]:
    if table_manager.is_enabled():
        session = table_manager.get_candidate_session(username, job_id)
        if session:
            return dict(session)

    cached = cache_manager.get(_job_cache_key(job_id))
    if cached.get("status") == "valid" and isinstance(cached.get("data"), dict):
        return dict(cached["data"])

    logger.warning("Job metadata missing for %s; initializing defaults", job_id)
    return {
        "job_id": job_id,
        "username": username,
        "synced_repos": [],
        "failed_repos": [],
        "expected_repos": [],
        "queued_repos": [],
        "completed_repos": 0,
        "total_repos": 0,
        "status": "queued",
    }


def _update_job_progress(
    job_id: str,
    username: str,
    repo_name: str,
    sync_failed: bool = False,
    *,
    message_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> None:
    job_info = _load_job_snapshot(job_id, username)
    queued_repos = job_info.get("queued_repos") or []
    expected_repos = job_info.get("expected_repos") or []

    if not isinstance(queued_repos, list):
        queued_repos = []
    if not isinstance(expected_repos, list):
        expected_repos = []

    logger.info(
        "[JOB_PROGRESS_START] job=%s user=%s repo=%s sync_failed=%s message_id=%s queued=%d expected=%d prior_completed=%s prior_total=%s",
        job_id,
        username,
        repo_name,
        sync_failed,
        message_id or "<none>",
        len([x for x in queued_repos if isinstance(x, str) and x]),
        len([x for x in expected_repos if isinstance(x, str) and x]),
        job_info.get("completed_repos"),
        job_info.get("total_repos"),
    )

    status_value = "failed" if sync_failed else "synced"

    # IMPORTANT: Avoid relying on list queries against RepoSyncStatus.
    # Some emulators/providers can behave unexpectedly for filtered list operations,
    # which can inflate progress by mixing status rows from other jobs/candidates.
    # Instead, treat RepoSyncStatus as the source of truth and read per-repo via point reads
    # for just the repos in this job.
    if table_manager.is_enabled():
        table_manager.upsert_repo_status(
            RepoSyncStatusRow(
                job_id=job_id,
                repo_name=repo_name,
                username=username,
                status=status_value,
                message_id=message_id,
                error=None,
            )
        )

        tracked = queued_repos or expected_repos
        tracked = [name for name in tracked if isinstance(name, str) and name]
        if not tracked and repo_name:
            tracked = [repo_name]

        synced: set[str] = set()
        failed: set[str] = set()
        for name in tracked:
            row = table_manager.get_repo_status(job_id, name)
            status = (row or {}).get("status")
            if status == "synced":
                synced.add(name)
            elif status == "failed":
                failed.add(name)
    else:
        synced = set(job_info.get("synced_repos", []) or [])
        failed = set(job_info.get("failed_repos", []) or [])
        if repo_name:
            if sync_failed:
                failed.add(repo_name)
                synced.discard(repo_name)
            else:
                synced.add(repo_name)
                failed.discard(repo_name)

    synced_list = sorted(synced)
    failed_list = sorted(failed)
    completed = len(synced_list)

    tracked_total = len([name for name in (queued_repos or expected_repos) if isinstance(name, str) and name])
    inferred_total = tracked_total or completed
    processed_count = len(synced | failed)
    total_target = max(job_info.get("total_repos", 0), inferred_total, processed_count, completed)

    updates = {
        "synced_repos": synced_list,
        "failed_repos": failed_list,
        "completed_repos": completed,
        "total_repos": total_target,
    }

    queued_set = set(name for name in queued_repos if isinstance(name, str) and name)
    processed = synced | failed
    pending = queued_set - processed if queued_set else set()

    logger.info(
        "[JOB_PROGRESS_COMPUTED] job=%s user=%s completed=%d total=%d synced=%d failed=%d pending=%d",
        job_id,
        username,
        updates.get("completed_repos"),
        updates.get("total_repos"),
        len(synced_list),
        len(failed_list),
        len(pending),
    )

    has_synced_repos = len(synced_list) > 0
    has_pending = len(pending) > 0
    should_merge = has_synced_repos and not has_pending

    logger.info(
        "[JOB_PROGRESS] job=%s synced=%d failed=%d pending=%d queued=%d should_merge=%s",
        job_id,
        len(synced_list),
        len(failed_list),
        len(pending),
        len(queued_set),
        should_merge,
    )

    if failed_list:
        logger.warning(
            "[JOB_FAILURES] job=%s - %d repos failed to sync: %s",
            job_id,
            len(failed_list),
            ", ".join(failed_list[:10]),
        )

    if pending:
        logger.info(
            "[JOB_PENDING] job=%s - %d repos still pending: %s",
            job_id,
            len(pending),
            ", ".join(sorted(pending)[:5]),
        )

    if should_merge:
        updates["status"] = "synced"
        logger.info(
            "[JOB_COMPLETE] job=%s ready for merge: synced=%d failed=%d",
            job_id,
            len(synced_list),
            len(failed_list),
        )

    if table_manager.is_enabled():
        table_manager.update_candidate_session(username, job_id, updates)
        logger.info(
            "[JOB_SESSION_UPDATED] job=%s user=%s status=%s",
            job_id,
            username,
            updates.get("status") or "<unchanged>",
        )
    elif cache_manager.use_cache and os.getenv("CF_BLOB_CACHE_ENABLED", "true").lower() == "true":
        existing = _load_job_snapshot(job_id, username)
        merged = dict(existing)
        merged.update(updates)
        cache_manager.save(_job_cache_key(job_id), merged, ttl=JOB_METADATA_TTL_SECONDS)
        logger.info(
            "[JOB_CACHE_UPDATED] job=%s user=%s status=%s",
            job_id,
            username,
            updates.get("status") or "<unchanged>",
        )

    logger.info(
        "Job %s progress: %s/%s (status=%s)",
        job_id,
        updates.get("completed_repos"),
        updates.get("total_repos"),
        updates.get("status", "processing"),
    )

    if should_merge:
        if queue_manager.is_enabled():
            enqueued = queue_manager.enqueue_merge_job(job_id, username, synced_list, trace_id=trace_id)
            logger.info(
                "[MERGE_ENQUEUED] job=%s with %d repos (skipped %d failed)",
                job_id,
                len(synced_list),
                len(failed_list),
            )
            if enqueued and table_manager.is_enabled():
                table_manager.update_candidate_session(
                    username,
                    job_id,
                    {"merge_enqueued_at": datetime.now(timezone.utc).isoformat()},
                )
        else:
            logger.warning("Queue manager disabled; merge job not enqueued for %s", job_id)


@bp.queue_trigger(arg_name="msg", queue_name="github-sync", connection="AzureWebJobsStorage")
def process_sync_job(msg: func.QueueMessage) -> None:
    payload = None
    username = None
    job_id = None
    repo_name = None
    trace_id = None
    message_id = None

    try:
        payload = _deserialize_message(msg)
        username = payload.get("username")
        job_id = payload.get("job_id")
        repo_name = payload.get("repo_name")
        fingerprint = payload.get("fingerprint")
        trace_id = payload.get("trace_id")

        queue_message_id = getattr(msg, "id", None)
        message_id = queue_message_id
        dequeue_count = getattr(msg, "dequeue_count", None)

        logger.info(
            "[SYNC_MESSAGE] job=%s user=%s repo=%s trace_id=%s message_id=%s queue_message_id=%s dequeue_count=%s",
            job_id or "<unknown>",
            username or "<unknown>",
            repo_name or "<unknown>",
            trace_id or "<none>",
            message_id or "<unknown>",
            queue_message_id or "<unknown>",
            dequeue_count if dequeue_count is not None else "<unknown>",
        )

        if not username or not job_id or not repo_name:
            raise ValueError(
                f"Missing required fields: username={username}, job_id={job_id}, repo_name={repo_name}"
            )

        logger.info("[SYNC] Starting sync for job=%s repo=%s user=%s", job_id, repo_name, username)
        _fetch_repo_bundle(job_id, username, repo_name, fingerprint)
        logger.info("[SYNC] Completed sync for job=%s repo=%s", job_id, repo_name)
        _update_job_progress(
            job_id,
            username,
            repo_name,
            sync_failed=False,
            message_id=message_id,
            trace_id=trace_id,
        )
    except ValueError as ve:
        logger.error("[SYNC_ERROR] Validation error for repo=%s: %s", repo_name or "unknown", ve)
        if job_id and username and repo_name:
            _update_job_progress(
                job_id,
                username,
                repo_name,
                sync_failed=True,
                message_id=message_id,
                trace_id=trace_id,
            )
        raise
    except Exception as exc:
        logger.error(
            "[SYNC_ERROR] Failed to sync repo=%s job=%s: %s",
            repo_name or "unknown",
            job_id or "unknown",
            exc,
            exc_info=True,
        )
        if job_id and username and repo_name:
            _update_job_progress(
                job_id,
                username,
                repo_name,
                sync_failed=True,
                message_id=message_id,
                trace_id=trace_id,
            )
        raise
