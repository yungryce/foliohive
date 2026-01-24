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
from cloudfolio_shared.table import RepoMetadataRow, RepoSyncStatusRow, RepoLanguagesRow, RepoGitHubMetadataRow

logger = logging.getLogger("cloudfolio.sync_worker")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

JOB_METADATA_TTL_SECONDS = 4 * 3600
READ_ME_EXCERPT_MAX_CHARS = 4096
STANDARD_CONFIG_FETCH_LIMIT = 20
STANDARD_CONFIG_MAX_CHARS = 4000


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


def _fetch_repo_metadata(username: str, repo_name: str, fingerprint: Optional[str] = None) -> bool:
    """Fetch repository metadata only (fast) - stored in table_manager.
    
    Fetches:
    - Repo metadata (description, stars, forks, etc.)
    - Languages statistics
    - File type discovery (filenames only, no content)
    
    Does NOT fetch file contents (readme/config). Use _fetch_and_cache_files for that.
    """
    logger.info(
        "[METADATA_FETCH_START] repo=%s - Fetching metadata (no file contents)",
        repo_name,
    )
    repo_manager = _get_repo_manager(username)
    if not repo_name:
        raise ValueError("Repository name missing")

    try:
        repo_metadata = repo_manager.get_repo_metadata(username=username, repo=repo_name, include_languages=True)
    except Exception as exc:
        logger.error("Failed to fetch metadata for %s/%s: %s", username, repo_name, exc)
        raise

    resolved_fingerprint = fingerprint or FingerprintManager.generate_metadata_fingerprint(repo_metadata)

    # Persist to table storage (metadata only)
    _persist_repo_metadata(username, repo_name, repo_metadata, resolved_fingerprint)
    logger.info("[METADATA_PERSISTED] repo=%s fingerprint=%s", repo_name, resolved_fingerprint)

    return True


def _persist_repo_metadata(
    username: str,
    repo_name: str,
    repo_metadata: Dict[str, Any],
    fingerprint: str,
) -> None:
    """Persist repo metadata to normalized tables only (no file caching).
    
    Persists to table_manager:
    - RepoMetadata: core metadata fields
    - RepoLanguages: language statistics
    - RepoGitHubMetadata: GitHub API fields
    
    File contents are handled separately by _fetch_and_cache_files.
    """
    if not repo_name:
        logger.warning("Cannot persist repo metadata without repo_name")
        return

    # 1. Persist core repo metadata (normalized - no nested JSON)
    row = RepoMetadataRow(
        username=username,
        repo_name=repo_name,
        fingerprint=fingerprint,
        content_blob=None,  # No blob reference - files cached separately
        has_documentation=None,  # Will be determined when files are fetched
        readme_excerpt=None,  # Will be populated when README is cached
        last_synced_at=repo_metadata.get("updated_at"),
    )
    table_manager.upsert_repo_metadata(row)

    # 2. Persist languages to normalized table
    languages = repo_metadata.get("languages", {})
    if languages and isinstance(languages, dict):
        total_bytes = sum(v for v in languages.values() if isinstance(v, (int, float)))
        lang_rows = []
        for lang, byte_count in languages.items():
            if not isinstance(byte_count, (int, float)):
                continue
            percentage = (byte_count / total_bytes * 100) if total_bytes > 0 else 0
            lang_rows.append(
                RepoLanguagesRow(
                    username=username,
                    repo_name=repo_name,
                    language=lang,
                    bytes_count=int(byte_count),
                    percentage=round(percentage, 2),
                )
            )
        if lang_rows:
            table_manager.batch_upsert_repo_languages(lang_rows)
            logger.info("[PERSIST_LANGUAGES] repo=%s languages=%d", repo_name, len(lang_rows))

    # 3. Persist GitHub metadata to normalized table
    github_row = RepoGitHubMetadataRow(
        username=username,
        repo_name=repo_name,
        description=repo_metadata.get("description"),
        topics=repo_metadata.get("topics", []),
        homepage_url=repo_metadata.get("homepage"),
        stars_count=repo_metadata.get("stargazers_count", 0),
        forks_count=repo_metadata.get("forks_count", 0),
        is_fork=repo_metadata.get("fork", False),
        is_archived=repo_metadata.get("archived", False),
        primary_language=repo_metadata.get("language"),
        license_name=repo_metadata.get("license", {}).get("name") if isinstance(repo_metadata.get("license"), dict) else None,
    )
    table_manager.upsert_repo_github_metadata(github_row)
    logger.info("[PERSIST_GITHUB_METADATA] repo=%s", repo_name)


def _load_job_snapshot(job_id: str, username: str) -> Dict[str, Any]:
    """Load job metadata from table storage (source of truth).
    
    Replaces cache fallback with direct table query per normalized schema.
    """
    job = table_manager.get_job_metadata(username, job_id)
    if job:
        logger.info("[LOAD_JOB_SNAPSHOT] job=%s user=%s source=table_metadata found=true", job_id, username)
        return dict(job)
    
    # Job not found - return minimal defaults
    logger.warning("[LOAD_JOB_SNAPSHOT] job=%s user=%s - Job metadata not found; initializing defaults", job_id, username)
    return {
        "job_id": job_id,
        "username": username,
        "status": "queued",
        "created_at": None,
        "updated_at": None,
    }


def _update_job_progress(
    job_id: str,
    username: str,
    repo_name: str,
    sync_failed: bool = False,
    *,
    message_id: Optional[str] = None,
) -> None:
    """Update job progress after metadata sync completes.
    
    Status transitions: pending → synced (or failed).
    Cached status updated by cache_worker.
    """
    job_info = _load_job_snapshot(job_id, username)

    # Legacy: For backwards compatibility, check if old list fields exist
    queued_repos = job_info.get("queued_repos", [])
    expected_repos = job_info.get("expected_repos", [])

    if not isinstance(queued_repos, list):
        queued_repos = []
    if not isinstance(expected_repos, list):
        expected_repos = []

    logger.info(
        "[JOB_PROGRESS_START] job=%s user=%s repo=%s sync_failed=%s message_id=%s queued=%d expected=%d",
        job_id,
        username,
        repo_name,
        sync_failed,
        message_id or "<none>",
        len([x for x in queued_repos if isinstance(x, str) and x]),
        len([x for x in expected_repos if isinstance(x, str) and x]),
    )

    status_value = "failed" if sync_failed else "synced"
    now = datetime.now(timezone.utc).isoformat()

    # Update RepoSyncStatus with sync completion
    table_manager.upsert_repo_status(
        RepoSyncStatusRow(
            job_id=job_id,
            repo_name=repo_name,
            username=username,
            status=status_value,
            sync_message_id=message_id,
            cache_message_id=None,  # Will be set by cache_worker
            error=None,
            synced_at=now if not sync_failed else None,
            cached_at=None,  # Will be set by cache_worker
        )
    )

    # Query RepoSyncStatus to calculate progress
    statuses = table_manager.list_repo_statuses(job_id)
    synced = {row["repo_name"] for row in statuses if row.get("status") == "synced"}
    failed = {row["repo_name"] for row in statuses if row.get("status") == "failed"}
    pending = {row["repo_name"] for row in statuses if row.get("status") == "pending"}
    
    synced_list = sorted(synced)
    failed_list = sorted(failed)
    completed = len(synced_list)

    # No updates to JobMetadataRow - these are derived fields computed from RepoSyncStatus
    # Only update status if needed
    queued_set = set(name for name in queued_repos if isinstance(name, str) and name)
    processed = synced | failed
    pending = queued_set - processed if queued_set else set()

    logger.info(
        "[JOB_PROGRESS_COMPUTED] job=%s user=%s completed=%d synced=%d failed=%d pending=%d",
        job_id,
        username,
        completed,
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

    # Update job-level status when all metadata synced
    if not pending:
        if synced:
            # All repos synced metadata - job transitions to caching phase
            table_manager.update_job_metadata(username, job_id, {"status": "caching"})
            logger.info("[JOB_SYNCED] job=%s - All metadata synced, transitioning to caching", job_id)
        elif failed:
            # All failed - mark job as failed
            table_manager.update_job_metadata(username, job_id, {"status": "failed"})
            logger.error("[JOB_FAILED] job=%s - All repos failed", job_id)


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

        # Fetch metadata only (fast) - stored in table_manager
        logger.info("[SYNC] Starting metadata sync for job=%s repo=%s user=%s", job_id, repo_name, username)
        _fetch_repo_metadata(username, repo_name, fingerprint)
        logger.info("[SYNC] Metadata sync completed for job=%s repo=%s", job_id, repo_name)
        
        # Enqueue file caching job (async background task)
        logger.info("[CACHE] Enqueuing file cache for job=%s repo=%s", job_id, repo_name)
        enqueued = queue_manager.enqueue_cache_job(
            username=username,
            job_id=job_id,
            repo_name=repo_name,
            fingerprint=fingerprint,
            trace_id=trace_id,
        )
        if enqueued:
            logger.info("[CACHE_ENQUEUED] job=%s repo=%s - File caching job enqueued", job_id, repo_name)
        else:
            logger.warning("[CACHE_ENQUEUE_FAILED] job=%s repo=%s - Failed to enqueue cache job", job_id, repo_name)
 
        _update_job_progress(
            job_id,
            username,
            repo_name,
            sync_failed=False,
            message_id=message_id,
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
            )
        raise
