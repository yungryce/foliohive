"""Sync worker blueprint.

Consumes messages from the ``github-sync`` queue, fetches repository context
using shared managers, and persists results to the cache. 
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import azure.functions as func

from cloudfolio_shared import (
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    queue_manager,
    table_manager,
)
from cloudfolio_shared.table import RepoLanguagesRow, RepoGitHubMetadataRow, RepoAPIUsageRow

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


def _record_api_usage_from_tracker(
    username: str,
    job_id: str,
    repo_name: str,
    operation: str,
    api_usage_dict: Dict[str, Any],
) -> None:
    """Record API usage from GitHubRepoManager's ApiUsageTracker to table storage."""
    totals = api_usage_dict.get("totals", {})

    
    # Count REST vs GraphQL calls (simplified - assume all are REST unless endpoint_kind is graphql)
    api_calls_rest = totals.get("requests", 0)
    api_calls_graphql = 0  # GitHubRepoManager would need to track this separately
    
    # Count cache hits from file_targets
    file_targets = api_usage_dict.get("file_targets", {})
    cache_hits = sum(target.get("cache_hits", 0) for target in file_targets.values())
    
    # Generate operation key: {operation}|{timestamp}|{repo_name}
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    # Sanitize timestamp for Azure Table RowKey (no :, /, \, #, ?)
    safe_timestamp = now.replace(":", "-").replace("+", "_")
    operation_key = f"{operation}|{safe_timestamp}|{repo_name}"
    
    row = RepoAPIUsageRow(
        username=username,
        operation_key=operation_key,
        operation=operation,
        job_id=job_id,
        repo_name=repo_name,
        api_calls_rest=api_calls_rest,
        api_calls_graphql=api_calls_graphql,
        cache_hits=cache_hits,
        rate_limit_remaining=None,  # Would need to extract from last request
        rate_limit_reset=None,
        created_at=safe_timestamp,  # Use sanitized timestamp
    )
    
    table_manager.upsert_api_usage(row)
    logger.info(
        "[API_USAGE_RECORDED] operation=%s job=%s repo=%s rest_calls=%d cache_hits=%d",
        operation,
        job_id,
        repo_name,
        api_calls_rest,
        cache_hits,
    )


def _fetch_repo_metadata(
    username: str,
    repo_name: str,
    fingerprint: Optional[str] = None,
    job_id: Optional[str] = None,  # pylint: disable=unused-argument
) -> Dict[str, Any]:
    """Fetch repository metadata only (fast) - stored in table_manager.
    
    Args:
        username: GitHub username
        repo_name: Repository name
        fingerprint: Optional ETag for conditional fetch
        job_id: Reserved for future use in tracing/correlation
    
    Fetches:
    - Repo metadata (description, stars, forks, etc.)
    - Languages statistics
    - File type discovery (filenames only, no content)
    
    Does NOT fetch file contents (readme/config). Use _fetch_and_cache_files for that.
    
    Returns:
        Dict with 'fingerprint' and 'api_usage' keys
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

    # Return both fingerprint and API usage for tracking
    return {
        "fingerprint": resolved_fingerprint,
        "api_usage": repo_metadata.get("api_usage", {}),
    }


def _persist_repo_metadata(
    username: str,
    repo_name: str,
    repo_metadata: Dict[str, Any],
    fingerprint: str,
) -> None:
    """Persist repo metadata to normalized tables.
    
    Persists to table_manager:
    - RepoLanguages: language statistics
    - RepoGitHubMetadata: GitHub API fields + fingerprint
    
    File contents are handled separately by cache_worker._fetch_and_cache_files.
    """
    if not repo_name:
        logger.warning("Cannot persist repo metadata without repo_name")
        return

    # 1. Persist languages to normalized table
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
                    repo_language_key=f"{repo_name}|{lang}",
                    repo_name=repo_name,
                    language=lang,
                    bytes_count=int(byte_count),
                    percentage=round(percentage, 2),
                )
            )
        if lang_rows:
            logger.info(
                "[PERSIST_LANGUAGES_ROWS] repo=%s count=%d rows=%s",
                repo_name,
                len(lang_rows),
                [(r.repo_language_key, r.language, r.bytes_count, r.percentage) for r in lang_rows]
            )
            table_manager.batch_upsert_repo_languages(lang_rows)
            logger.info("[PERSIST_LANGUAGES] repo=%s languages=%d", repo_name, len(lang_rows))

    # 2. Persist GitHub metadata to normalized table (includes fingerprint)
    github_row = RepoGitHubMetadataRow(
        username=username,
        repo_name=repo_name,
        fingerprint=fingerprint,
        description=repo_metadata.get("description"),
        topics=repo_metadata.get("topics", []),
        html_url=repo_metadata.get("html_url"),
        homepage_url=repo_metadata.get("homepage"),
        stars_count=repo_metadata.get("stargazers_count", 0),
        forks_count=repo_metadata.get("forks_count", 0),
        open_issues=repo_metadata.get("open_issues_count", 0),
        watchers=repo_metadata.get("watchers_count", 0),
        primary_language=repo_metadata.get("language"),
        is_fork=repo_metadata.get("fork", False),
        is_archived=repo_metadata.get("archived", False),
        license_name=repo_metadata.get("license", {}).get("name") if isinstance(repo_metadata.get("license"), dict) else None,
        github_created_at=repo_metadata.get("created_at"),
        github_updated_at=repo_metadata.get("updated_at"),
        github_pushed_at=repo_metadata.get("pushed_at"),
    )
    table_manager.upsert_repo_github_metadata(github_row)
    logger.info("[PERSIST_GITHUB_METADATA] repo=%s fingerprint=%s", repo_name, fingerprint)


def _update_job_progress(
    job_id: str,
    username: str,
    repo_name: str,
    sync_failed: bool = False,
    *,
    message_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Update job progress after metadata sync completes.
    
    Status transitions: pending → synced (or failed).
    Cached status updated by cache_worker.
    """

    status_value = "failed" if sync_failed else "synced"
    now = datetime.now(timezone.utc).isoformat()

    # Update RepoSyncStatus with sync completion (partial update)
    update_dict = {
        "status": status_value,
        "sync_message_id": message_id,
        "synced_at": now if not sync_failed else None,
    }
    if error:
        update_dict["error"] = error
    
    table_manager.update_repo_status(
        job_id,
        repo_name,
        update_dict
    )

    # Query RepoSyncStatus to calculate progress
    statuses = table_manager.list_repo_statuses(job_id)
    
    # Single pass to count and collect repo names
    status_counts = defaultdict(int)
    status_lists = defaultdict(list)
    
    for row in statuses:
        status = row.get("status")
        if status in ("synced", "failed", "pending"):
            status_counts[status] += 1
            status_lists[status].append(row["repo_name"])
    
    synced_list = sorted(status_lists["synced"])
    failed_list = sorted(status_lists["failed"])
    pending_list = sorted(status_lists["pending"])
    synced = bool(status_lists["synced"])  # For job status logic below
    failed = bool(status_lists["failed"])  # For job status logic below
    completed = status_counts["synced"]

    logger.info(
        "[JOB_PROGRESS_COMPUTED] job=%s user=%s completed=%d synced=%d failed=%d pending=%d",
        job_id,
        username,
        completed,
        len(synced_list),
        len(failed_list),
        len(pending_list),
    )

    has_synced_repos = status_counts["synced"] > 0

    logger.info(
        "[JOB_PROGRESS] job=%s synced=%d failed=%d pending=%d",
        job_id,
        len(synced_list),
        len(failed_list),
        len(pending_list),
    )
    if failed_list:
        logger.warning(
            "[JOB_FAILURES] job=%s - %d repos failed to sync: %s",
            job_id,
            len(failed_list),
            ", ".join(failed_list[:10]),
        )

    if pending_list:
        logger.info(
            "[JOB_PENDING] job=%s - %d repos still pending: %s",
            job_id,
            len(pending_list),
            ", ".join(pending_list[:5]),
        )

    # Get current job status to check for transitions
    job = table_manager.get_job_metadata(username, job_id)
    current_status = job.get("status") if job else "queued"
    
    # Set syncing status when first repo completes sync (transition from queued)
    if has_synced_repos and current_status == "queued":
        table_manager.update_job_metadata(username, job_id, {"status": "syncing"})
        logger.info("[JOB_SYNCING] job=%s - First metadata synced, job in progress", job_id)
    
    # Update job-level status when all metadata synced. Investigate failures.
    if not pending_list:
        if synced:
            # All repos synced metadata - files being cached in background
            table_manager.update_job_metadata(username, job_id, {"status": "syncing"})
            logger.info("[JOB_SYNCED] job=%s - All metadata synced, files caching in background", job_id)
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
        fetch_result = _fetch_repo_metadata(username, repo_name, fingerprint, job_id=job_id)
        logger.info("[SYNC] Metadata sync completed for job=%s repo=%s", job_id, repo_name)
        
        # Record API usage to table
        api_usage = fetch_result.get("api_usage", {})
        if api_usage:
            _record_api_usage_from_tracker(
                username=username,
                job_id=job_id,
                repo_name=repo_name,
                operation="metadata_sync",
                api_usage_dict=api_usage,
            )
        
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
        if job_id and username and repo_name:
            _update_job_progress(
                job_id,
                username,
                repo_name,
                sync_failed=True,
                message_id=message_id,
                error=str(ve),
            )
        raise
    except Exception as exc:
        if job_id and username and repo_name:
            _update_job_progress(
                job_id,
                username,
                repo_name,
                sync_failed=True,
                message_id=message_id,
                error=str(exc),
            )
        raise
