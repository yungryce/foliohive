"""Cache worker blueprint.

Consumes messages from the ``github-cache`` queue, fetches repository file contents
(readme, config) and persists them to blob cache for client consumption.

This worker operates asynchronously from metadata sync - files are cached in the
background while clients can access metadata immediately via table_manager.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, Optional

import azure.functions as func

from foliohive_shared import (
    cache_manager,
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    table_manager,
    RepoAPIUsageRow,
    RepoDiscoveredPathsRow,
)

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


def _fetch_and_cache_files(
        username: str,
        repo_name: str,
        fingerprint: str,
        job_id: Optional[str] = None
    )-> Dict[str, Any]:
    """Fetch and cache file contents with generous persistence limits.

    Persistence Strategy:
    - Max 20 config files × 4KB each = 80KB per repo
    - Handles all summary types without re-caching
    - Retrieval layer (FILE_BUDGETS) determines what's used

    Example:
    - profile summary: Uses 2 configs × 4KB = 8KB
    - readme summary: Uses 3 configs × 4KB = 12KB
    - query summary: Uses 2 configs × 4KB = 8KB
    
    This design avoids re-caching when use cases change.
    
    Args:
        username: GitHub username
        repo_name: Repository name
        fingerprint: Snapshot of RepoGitHubMetadataRow.fingerprint from queue message
        job_id: Job ID for tracking and persistence
    
    Returns:
        Dict with 'cached_count' and 'api_usage' keys
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
    primary_readme = discovery.get("primary_readme", "")
    api_usage = discovery.get("api_usage", {})
    
    # Cache individual file blobs for selective retrieval
    cached_count = 0
    
    # Cache readme files using centralized key generation
    for filename, content in readme_files.items():
        # Determine if this is the primary readme or a secondary one
        file_identifier = "PRIMARY" if content == primary_readme else filename
        key = cache_manager.generate_cache_key(
            username=username,
            repo=repo_name,
            file_type="readme",
            filename=file_identifier,
        )
        cache_manager.save(key, content)
        logger.info(
            "Cached readme file: repo=%s file_identifier=%s key=%s",
            repo_name, file_identifier, key
        )
        cached_count += 1
    
    # Cache config files using centralized key generation
    for filename, content in config_files.items():
        key = cache_manager.generate_cache_key(
            username=username,
            repo=repo_name,
            file_type="config",
            filename=filename,
        )
        cache_manager.save(key, content)
        logger.info(
            "Cached config file: repo=%s filename=%s key=%s",
            repo_name, filename, key
        )
        cached_count += 1
    
    logger.info(
        "[FILES_FETCH_COMPLETE] repo=%s - Cached %d files (readme + config)",
        repo_name,
        cached_count,
    )
    
    # Persist discovered paths to table storage for later retrieval
    # fingerprint is snapshot copy from sync_worker, passed via queue message
    if job_id:
        _persist_discovered_paths(
            username=username,
            repo_name=repo_name,
            config_files=config_files,
            readme_files=readme_files,
            fingerprint=fingerprint
        )
    
    # Record API usage if job_id provided
    if job_id and api_usage:
        _record_api_usage_for_file_cache(username, job_id, repo_name, api_usage)
    
    return {
        "cached_count": cached_count,
        "api_usage": api_usage,
    }


def _persist_discovered_paths(
    username: str,
    repo_name: str,
    config_files: Dict[str, str],
    readme_files: Dict[str, str],
    fingerprint: str,
) -> None:
    """Persist discovered file paths to table storage for later retrieval.
    
    Separates discovered paths into readme_paths and config_paths for
    easy categorization by get_repo_files() in api_gateway.
    
    Args:
        username: GitHub username
        repo_name: Repository name
        config_files: Dict of config filename -> content
        readme_files: Dict of readme filename -> content
        fingerprint: Snapshot copy of RepoGitHubMetadataRow.fingerprint at cache time.
            Used for cache invalidation - when fingerprints mismatch, blobs are stale.
    """
    from datetime import datetime, timezone
    
    # Extract paths
    readme_paths = list(readme_files.keys())
    config_paths = list(config_files.keys())
    all_paths = readme_paths + config_paths
    
    now = datetime.now(timezone.utc).isoformat()
    safe_timestamp = now.replace(":", "-").replace("+", "_")
    
    row = RepoDiscoveredPathsRow(
        username=username,
        repo_name=repo_name,
        fingerprint=fingerprint,
        discovered_paths=all_paths,
        readme_paths=readme_paths,
        config_paths=config_paths,
        created_at=safe_timestamp,
        updated_at=safe_timestamp,
    )
    
    table_manager.upsert_repo_discovered_paths(row)
    logger.info(
        "[PERSIST_DISCOVERED_PATHS] user=%s repo=%s fingerprint=%s total=%d readme=%d config=%d",
        username, repo_name, fingerprint or "<none>", len(all_paths), len(readme_paths), len(config_paths)
    )


def _record_api_usage_for_file_cache(
    username: str,
    job_id: str,
    repo_name: str,
    api_usage_dict: Dict[str, Any],
) -> None:
    """Record API usage for file caching operation."""
    from datetime import datetime, timezone
    
    totals = api_usage_dict.get("totals", {})
    file_targets = api_usage_dict.get("file_targets", {})
    cache_hits = sum(target.get("cache_hits", 0) for target in file_targets.values())
    
    now = datetime.now(timezone.utc).isoformat()
    # Sanitize timestamp for Azure Table (no :, /, \, #, ? in any field)
    safe_timestamp = now.replace(":", "-").replace("+", "_")
    operation_key = f"file_cache|{safe_timestamp}|{repo_name}"
    
    row = RepoAPIUsageRow(
        username=username,
        operation_key=operation_key,
        operation="file_cache",
        job_id=job_id,
        repo_name=repo_name,
        api_calls_rest=totals.get("requests", 0),
        api_calls_graphql=0,
        cache_hits=cache_hits,
        created_at=safe_timestamp,  # Use sanitized timestamp
    )
    
    logger.info(
        "[RECORD_API_USAGE_ROW] username=%s operation_key=%s operation=%s job_id=%s repo_name=%s rest=%d graphql=%d cache_hits=%d created_at=%s",
        username,
        operation_key,
        "file_cache",
        job_id,
        repo_name,
        totals.get("requests", 0),
        0,
        cache_hits,
        now,
    )
    
    table_manager.upsert_api_usage(row)
    logger.info(
        "[API_CACHE_USAGE_RECORDED] operation=file_cache job=%s repo=%s rest_calls=%d cache_hits=%d",
        job_id,
        repo_name,
        row.api_calls_rest,
        cache_hits,
    )


def _update_cache_progress(
    job_id: str,
    username: str,
    repo_name: str,
    cache_failed: bool = False,
    *,
    message_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Update job progress after file caching completes.
    
    Status transitions: synced → cached (or failed).
    """
    from datetime import datetime, timezone
    
    status_value = "failed" if cache_failed else "cached"
    now = datetime.now(timezone.utc).isoformat()
    
    # Update RepoSyncStatus with cache completion (partial update)
    update_dict = {
        "status": status_value,
        "cache_message_id": message_id,
        "cached_at": now if not cache_failed else None,
    }
    if error:
        update_dict["error"] = error
    
    table_manager.update_repo_status(
        job_id,
        repo_name,
        update_dict
    )
    
    # Query RepoSyncStatus to calculate caching progress
    statuses = table_manager.list_repo_statuses(job_id)

    status_counts = defaultdict(int)
    status_lists = defaultdict(list)

    for row in statuses:
        status = row.get("status")
        if status in ("cached", "failed", "synced"):
            status_counts[status] += 1
            status_lists[status].append(row["repo_name"])

    cached = status_lists["cached"]
    failed = status_lists["failed"]
    synced = status_lists["synced"]
    
    logger.info(
        "[CACHE_PROGRESS] job=%s cached=%d synced=%d failed=%d",
        job_id,
        len(cached),
        len(synced),
        len(failed),
    )
    
    # Get current job status to check for transitions
    job = table_manager.get_job_metadata(username, job_id)
    current_status = job.get("status") if job else "queued"
    
    # Set metadata_ready when first repo cached (metadata available for display)
    if cached and current_status not in ("metadata_ready", "completed"):
        table_manager.update_job_metadata(
            username,
            job_id,
            {"status": "metadata_ready"},
        )
        logger.info(
            "[JOB_METADATA_READY] job=%s - First repo cached (%d/%d), metadata available for display",
            job_id,
            len(cached),
            len(statuses),
        )
    
    # Complete job when all files are cached 
    if not synced and failed:
        table_manager.update_job_metadata(username, job_id, {"status": "failed"})
        logger.error("[JOB_FAILED] job=%s - All cache jobs failed", job_id)


@bp.queue_trigger(arg_name="msg", queue_name="github-cache", connection="AzureWebJobsStorage")
def process_cache_job(msg: func.QueueMessage) -> None:
    """Process file caching job - fetches and caches repo file contents.
    
    This worker operates asynchronously from metadata sync. Files are cached in the
    background for client consumption (via get_repo_files) and training workflows.
    
    Fingerprint-based cache invalidation avoids expensive GitHub API calls when
    repo blobs haven't changed (similar to metadata sync pattern).
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
        fingerprint = payload.get("fingerprint")  # Extract from queue message
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
        logger.info("[CACHE] Starting file cache for job=%s repo=%s user=%s fingerprint=%s", job_id, repo_name, username, fingerprint)
        _fetch_and_cache_files(username, repo_name, fingerprint, job_id=job_id)
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
        if job_id and username and repo_name:
            _update_cache_progress(
                job_id,
                username,
                repo_name,
                cache_failed=True,
                message_id=queue_message_id,
                trace_id=trace_id,
                error=str(ve),
            )
        raise
    except Exception as exc:
        if job_id and username and repo_name:
            _update_cache_progress(
                job_id,
                username,
                repo_name,
                cache_failed=True,
                message_id=queue_message_id,
                trace_id=trace_id,
                error=str(exc),
            )
        raise

