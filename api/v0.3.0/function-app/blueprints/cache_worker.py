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

from cloudfolio_shared import (
    cache_manager,
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    table_manager,
)
from cloudfolio_shared.cache.repo_cache_retrieval import repo_cache_retrieval
from cloudfolio_shared.table import RepoAPIUsageRow, RepoDiscoveredPathsRow

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


def _calculate_bundle_fingerprint(username: str, job_id: str) -> str:
    """Calculate bundle fingerprint from repo statuses.
    
    Uses repo fingerprints from RepoGitHubMetadata table to generate
    a deterministic bundle-level fingerprint.
    """
    statuses = table_manager.list_repo_statuses(job_id)
    cached_repos = [s for s in statuses if s.get("status") == "cached"]
    
    # Sort by repo name for consistency
    cached_repos.sort(key=lambda r: r.get("repo_name", ""))
    
    fingerprints = []
    for repo_status in cached_repos:
        repo_name = repo_status.get("repo_name")
        if not repo_name:
            continue
        
        # Get GitHub metadata to extract fingerprint
        github_metadata = table_manager.get_repo_github_metadata(username, repo_name)
        if github_metadata and github_metadata.get("fingerprint"):
            fingerprints.append(github_metadata["fingerprint"])
    
    if fingerprints:
        return FingerprintManager.generate_bundle_fingerprint(fingerprints)
    
    # Fallback: use repo names if no fingerprints available
    repo_names = [r.get("repo_name") for r in cached_repos if r.get("repo_name")]
    return FingerprintManager.generate_content_fingerprint({"repos": sorted(repo_names)})


def _fetch_and_cache_files(username: str, repo_name: str, job_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and cache file contents (readme + config) - stored in cache_manager.
    
    Fetches:
    - README file contents
    - Config file contents (package.json, Dockerfile, etc.)
    
    Individual files cached separately (kind="file") for selective retrieval.
    Discovered file paths are persisted to table storage for later retrieval.
    This operation is expensive and runs asynchronously from metadata sync.
    
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
        if content == primary_readme:
            # Save as primary readme
            repo_cache_retrieval.save_primary_readme(
                username=username,
                repo=repo_name,
                content=content,
                ttl=None,
            )
        else:
            # Save as path-indexed readme
            repo_cache_retrieval.save_readme_file(
                username=username,
                repo=repo_name,
                path=filename,
                content=content,
                ttl=None,
            )
        cached_count += 1
    
    # Cache config files using centralized key generation
    for filename, content in config_files.items():
        repo_cache_retrieval.save_config_file(
            username=username,
            repo=repo_name,
            path=filename,
            content=content,
            ttl=None,
        )
        cached_count += 1
    
    logger.info(
        "[FILES_FETCH_COMPLETE] repo=%s - Cached %d files (readme + config)",
        repo_name,
        cached_count,
    )
    
    # Persist discovered paths to table storage for later retrieval
    if job_id:
        _persist_discovered_paths(
            job_id=job_id,
            username=username,
            repo_name=repo_name,
            config_files=config_files,
            readme_files=readme_files,
        )
    
    # Record API usage if job_id provided
    if job_id and api_usage:
        _record_api_usage_for_file_cache(username, job_id, repo_name, api_usage)
    
    return {
        "cached_count": cached_count,
        "api_usage": api_usage,
    }


def _persist_discovered_paths(
    job_id: str,
    username: str,
    repo_name: str,
    config_files: Dict[str, str],
    readme_files: Dict[str, str],
) -> None:
    """Persist discovered file paths to table storage for later retrieval.
    
    Separates discovered paths into readme_paths and config_paths for
    easy categorization by get_repo_files() in api_gateway.
    """
    from datetime import datetime, timezone
    
    # Extract paths
    readme_paths = list(readme_files.keys())
    config_paths = list(config_files.keys())
    all_paths = readme_paths + config_paths
    
    now = datetime.now(timezone.utc).isoformat()
    safe_timestamp = now.replace(":", "-").replace("+", "_")
    
    row = RepoDiscoveredPathsRow(
        job_id=job_id,
        repo_name=repo_name,
        username=username,
        discovered_paths=all_paths,
        readme_paths=readme_paths,
        config_paths=config_paths,
        created_at=safe_timestamp,
        updated_at=safe_timestamp,
    )
    
    table_manager.upsert_repo_discovered_paths(row)
    logger.info(
        "[PERSIST_DISCOVERED_PATHS] job=%s repo=%s total=%d readme=%d config=%d",
        job_id, repo_name, len(all_paths), len(readme_paths), len(config_paths)
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
    if not synced:  # No repos still in 'synced' state means all are cached or failed
        if cached:
            logger.info(
                "[JOB_CACHED] job=%s - All files cached, completing job with %d repos (skipped %d failed)",
                job_id,
                len(cached),
                len(failed),
            )
            
            # Calculate bundle fingerprint from table data
            fingerprint = _calculate_bundle_fingerprint(username, job_id)
            
            # Mark job complete
            table_manager.update_job_metadata(
                username,
                job_id,
                {
                    "status": "completed",
                    "bundle_fingerprint": fingerprint,
                    "completed_at": now,
                },
            )
            logger.info("[JOB_COMPLETED] job=%s repos=%d fingerprint=%s", job_id, len(cached), fingerprint)
            
            logger.info("[TRAINING_DISABLED] job=%s - Training queue deprecated", job_id)
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
        _fetch_and_cache_files(username, repo_name, job_id=job_id)
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

