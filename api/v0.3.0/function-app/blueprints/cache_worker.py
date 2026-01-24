"""Cache worker blueprint.

Consumes messages from the ``github-cache`` queue, fetches repository file contents
(readme, config) and persists them to blob cache for client consumption and training.

This worker operates asynchronously from metadata sync - files are cached in the
background while clients can access metadata immediately via table_manager.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import azure.functions as func

from cloudfolio_shared import (
    cache_manager,
    GitHubAPI,
    GitHubRepoManager,
    queue_manager,
    table_manager,
)
from cloudfolio_shared.table import RepoSyncStatusRow

logger = logging.getLogger("cloudfolio.cache_worker")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

STANDARD_CONFIG_FETCH_LIMIT = 20
STANDARD_CONFIG_MAX_CHARS = 4000
READ_ME_EXCERPT_MAX_CHARS = 4096


def _get_repo_manager(username: str) -> GitHubRepoManager:
    if not username:
        raise ValueError("Username is required")
    token = os.getenv("GITHUB_TOKEN")
    api = GitHubAPI(token=token, username=username)
    return GitHubRepoManager(api, username=username)


def _deserialize_message(msg: func.QueueMessage) -> Dict[str, Any]:
    body_bytes = msg.get_body()
    body_str = body_bytes.decode("utf-8") if isinstance(body_bytes, (bytes, bytearray)) else str(body_bytes)
    if not body_str or not body_str.strip():
        raise ValueError("Queue message body is empty")

    payload = json.loads(body_str)

    repo_name = payload.get("repo_name", "unknown")
    job_id = payload.get("job_id", "unknown")
    logger.info(
        "[CACHE_RECV] repo=%s job=%s - Top-level keys: %s",
        repo_name,
        job_id,
        sorted(list(payload.keys())),
    )

    return payload


def _fetch_and_cache_files(username: str, repo_name: str) -> bool:
    """Fetch and cache file contents (readme + config) - stored in cache_manager.
    
    Fetches:
    - README file contents
    - Config file contents (package.json, Dockerfile, etc.)
    
    Individual files cached separately (kind="file") for selective retrieval.
    This operation is expensive and runs asynchronously from metadata sync.
    """
    logger.info(
        "[FILES_FETCH_START] repo=%s - Fetching file contents (readme + config)",
        repo_name,
    )
    repo_manager = _get_repo_manager(username)

    mode = "rest"
    if os.getenv("ENABLE_CONFIG_DISCOVERY_GRAPHQL", "false").lower() == "true":
        mode = "graphql"

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
    primary_readme = discovery.get("readme", "")
    
    # Cache individual file blobs for selective retrieval
    cached_count = 0
    
    # Cache readme files
    for filename, content in readme_files.items():
        file_key = cache_manager.generate_cache_key(
            kind="file",
            username=username,
            repo=repo_name,
            file_type="readme",
            filename=filename,
        )
        cache_manager.save(file_key, content, ttl=None)
        cached_count += 1
        logger.debug("[FILE_CACHED] repo=%s type=readme file=%s", repo_name, filename)
    
    # Cache primary readme separately
    if primary_readme:
        primary_key = cache_manager.generate_cache_key(
            kind="file",
            username=username,
            repo=repo_name,
            file_type="readme",
            filename="PRIMARY",
        )
        cache_manager.save(primary_key, primary_readme, ttl=None)
        cached_count += 1
        logger.debug("[FILE_CACHED] repo=%s type=readme file=PRIMARY", repo_name)
    
    # Cache config files
    for filename, content in config_files.items():
        file_key = cache_manager.generate_cache_key(
            kind="file",
            username=username,
            repo=repo_name,
            file_type="config",
            filename=filename,
        )
        cache_manager.save(file_key, content, ttl=None)
        cached_count += 1
        logger.debug("[FILE_CACHED] repo=%s type=config file=%s", repo_name, filename)
    
    logger.info(
        "[FILES_FETCH_COMPLETE] repo=%s - Cached %d files (readme + config)",
        repo_name,
        cached_count,
    )
    
    return True


def _update_cache_progress(
    job_id: str,
    username: str,
    repo_name: str,
    cache_failed: bool = False,
    *,
    message_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Update job progress after file caching completes.
    
    Status transitions: synced → cached (or failed).
    Triggers merge when all repos are cached.
    """
    from datetime import datetime, timezone
    
    status_value = "failed" if cache_failed else "cached"
    now = datetime.now(timezone.utc).isoformat()
    
    # Update RepoSyncStatus with cache completion
    existing = table_manager.get_repo_status(job_id, repo_name)
    table_manager.upsert_repo_status(
        RepoSyncStatusRow(
            job_id=job_id,
            repo_name=repo_name,
            username=username,
            status=status_value,
            sync_message_id=existing.get("sync_message_id") if existing else None,
            cache_message_id=message_id,
            error=None,
            synced_at=existing.get("synced_at") if existing else None,
            cached_at=now if not cache_failed else None,
        )
    )
    
    # Query RepoSyncStatus to calculate caching progress
    statuses = table_manager.list_repo_statuses(job_id)
    cached = [row["repo_name"] for row in statuses if row.get("status") == "cached"]
    failed = [row["repo_name"] for row in statuses if row.get("status") == "failed"]
    synced = [row["repo_name"] for row in statuses if row.get("status") == "synced"]
    
    logger.info(
        "[CACHE_PROGRESS] job=%s cached=%d synced=%d failed=%d",
        job_id,
        len(cached),
        len(synced),
        len(failed),
    )
    
    # Trigger merge when all files are cached
    if not synced:  # No repos still in 'synced' state means all are cached or failed
        if cached:
            logger.info(
                "[JOB_CACHED] job=%s - All files cached, enqueuing merge with %d repos (skipped %d failed)",
                job_id,
                len(cached),
                len(failed),
            )
            table_manager.update_job_metadata(username, job_id, {"status": "merging"})
            
            enqueued = queue_manager.enqueue_merge_job(
                job_id,
                username,
                cached,  # Only merge successfully cached repos
                trace_id=trace_id,
            )
            if enqueued:
                table_manager.update_job_metadata(
                    username,
                    job_id,
                    {"merge_enqueued_at": now},
                )
        elif failed:
            table_manager.update_job_metadata(username, job_id, {"status": "failed"})
            logger.error("[JOB_FAILED] job=%s - All cache jobs failed", job_id)


@bp.queue_trigger(arg_name="msg", queue_name="github-cache", connection="AzureWebJobsStorage")
def process_cache_job(msg: func.QueueMessage) -> None:
    """Process file caching job - fetches and caches repo file contents.
    
    This worker operates asynchronously from metadata sync. Files are cached in the
    background for client consumption (via get_repo_files) and training workflows.
    """
    payload = None
    username = None
    job_id = None
    repo_name = None
    trace_id = None

    try:
        payload = _deserialize_message(msg)
        username = payload.get("username")
        job_id = payload.get("job_id")
        repo_name = payload.get("repo_name")
        trace_id = payload.get("trace_id")

        queue_message_id = getattr(msg, "id", None)
        dequeue_count = getattr(msg, "dequeue_count", None)

        logger.info(
            "[CACHE_MESSAGE] job=%s user=%s repo=%s trace_id=%s message_id=%s dequeue_count=%s",
            job_id or "<unknown>",
            username or "<unknown>",
            repo_name or "<unknown>",
            trace_id or "<none>",
            queue_message_id or "<unknown>",
            dequeue_count if dequeue_count is not None else "<unknown>",
        )

        if not username or not job_id or not repo_name:
            raise ValueError(
                f"Missing required fields: username={username}, job_id={job_id}, repo_name={repo_name}"
            )

        # Fetch and cache file contents
        logger.info("[CACHE] Starting file cache for job=%s repo=%s user=%s", job_id, repo_name, username)
        _fetch_and_cache_files(username, repo_name)
        logger.info("[CACHE] File cache completed for job=%s repo=%s", job_id, repo_name)
        
        # Update progress tracking
        _update_cache_progress(
            job_id,
            username,
            repo_name,
            cache_failed=False,
            message_id=queue_message_id,
            trace_id=trace_id,
        )

    except ValueError as ve:
        logger.error("[CACHE_ERROR] Validation error for repo=%s: %s", repo_name or "unknown", ve)
        if job_id and username and repo_name:
            _update_cache_progress(
                job_id,
                username,
                repo_name,
                cache_failed=True,
                message_id=queue_message_id,
                trace_id=trace_id,
            )
        raise
    except Exception as exc:
        logger.error(
            "[CACHE_ERROR] Failed to cache files for repo=%s job=%s: %s",
            repo_name or "unknown",
            job_id or "unknown",
            exc,
            exc_info=True,
        )
        if job_id and username and repo_name:
            _update_cache_progress(
                job_id,
                username,
                repo_name,
                cache_failed=True,
                message_id=queue_message_id,
                trace_id=trace_id,
            )
        raise
