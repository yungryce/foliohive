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
    GitHubAPI,
    GitHubRepoManager,
    SummaryManager,
    table_manager,
)
from foliohive_shared.ai.data_filter import get_config_extractor

logger = logging.getLogger("foliohive.cache_worker")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()


# ---------------------------------------------------------------------------
# Config Extraction
# ---------------------------------------------------------------------------
def _extract_config_payloads(
    config_files: Dict[str, str],
) -> Dict[str, Any]:
    """Extract structured payloads from raw config file contents."""
    extracted_config_files: Dict[str, Any] = {}

    for filename, content in config_files.items():
        extractor = get_config_extractor(filename)
        if extractor is None:
            logger.warning("No extractor found for file %s; skipping", filename)
            continue

        extractor_key = getattr(extractor, "__name__", None) if extractor else None
        extracted = extractor(content or "")
        if extracted is None:
            logger.warning("Extractor for %s returned None for file %s; skipping", extractor_key, filename)
            continue

        extracted_config_files[filename] = extracted
        status = "failed" if isinstance(extracted, dict) and extracted.get("error") else "extracted"
        if status == "failed":
            logger.warning("Extraction failed for file %s with extractor %s: %s", filename, extractor_key, extracted.get("error"))

    return extracted_config_files


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
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


def _fetch_file_content(
        username: str,
        repo_name: str,
        ref: Optional[str] = None,
        job_id: Optional[str] = None
    ) -> Dict[str, Any]:
    """Fetch files from GitHub (zero blob caching).

    Architecture:
    - Fetch files from GitHub API → in-memory only
    - Extract config signals → in-memory only
    
    This eliminates blob I/O creating fast cache worker completion.
    
    Args:
        username: GitHub username
        repo_name: Repository name
        fingerprint: Snapshot of RepoGitHubMetadataRow.fingerprint from queue message
        job_id: Job ID for tracking and API usage
    
    Returns:
        Dict with api_usage, readme_content (in-memory), and config_files (in-memory)
    """

    repo_manager = _get_repo_manager(username)

    # Fetch files from GitHub (in-memory only)
    discovery = repo_manager.get_repo_blob_files(
        username=username,
        repo=repo_name,
        ref=ref,
        purpose="file_cache",
        job_id=job_id,
    )

    config_files = discovery.get("config_files", {})
    primary_readme = discovery.get("primary_readme", "")
    readme_files = discovery.get("readme_files", {})
    
    # Extract config signals (in-memory only, no blob persistence)
    extracted_config_content = _extract_config_payloads(config_files)
    serialized_config_files = {
        filename: json.dumps(payload)
        for filename, payload in extracted_config_content.items()
    }

    return {
        "primary_readme_content": primary_readme,
        "config_content": serialized_config_files,
        "readme_content": readme_files,
    }


def _update_cache_progress(
    job_id: str,
    username: str,
    repo_name: str,
    summary_failed: bool = False,
    summary_ready: bool = False,
    *,
    message_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Update job progress after micro-summary generation completes.
    
    Status transitions: synced → summary_ready (or failed).
    Tracks micro-summary caching progress.
    """
    from datetime import datetime, timezone, timedelta

    _STALE_PENDING_THRESHOLD = timedelta(minutes=30)

    logger.info(
        "[CACHE_PROGRESS_UPDATE] job=%s repo=%s summary_ready=%s summary_failed=%s message_id=%s trace_id=%s error=%s",
        job_id,
        repo_name,
        summary_ready,
        summary_failed,
        message_id or "<unknown>",
        trace_id or "<none>",
        error or "<none>",
    )
    
    status_value = "failed" if summary_failed else ("summary_ready" if summary_ready else "failed")
    now = datetime.now(timezone.utc).isoformat()
    
    # Update RepoSyncStatus with cache completion (partial update)
    update_dict = {
        "status": status_value,
        "cache_message_id": message_id,
        "cached_at": now if not summary_failed else None,
    }
    if error:
        update_dict["error"] = error
    
    table_manager.update_repo_status(
        job_id,
        repo_name,
        update_dict
    )
    
    # Query RepoSyncStatus to calculate caching progress (control source for total repo count)
    statuses = table_manager.list_repo_statuses(job_id)
    total_repos = len(statuses)

    status_counts = defaultdict(int)
    status_lists = defaultdict(list)
    pending_rows: list = []

    for row in statuses:
        status = row.get("status")
        repo = row.get("repo_name")

        if status in ("synced", "summary_ready", "failed"):
            status_counts[status] += 1
            status_lists[status].append(repo)
        elif status == "pending":
            pending_rows.append(row)

    summary_ready_list = sorted(status_lists.get("summary_ready", []))
    failed_list = list(sorted(status_lists.get("failed", [])))
    synced_list = sorted(status_lists.get("synced", []))

    # Safety valve: promote stale pending repos to failed so the job can complete.
    # A pending repo with no queue message processed after the threshold is assumed dropped.
    now_utc = datetime.now(timezone.utc)
    stale_pending: list = []
    for pending_row in pending_rows:
        updated_at_str = pending_row.get("updated_at")
        is_stale = True  # Default: treat as stale when no timestamp
        if updated_at_str:
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
                is_stale = (now_utc - updated_at) > _STALE_PENDING_THRESHOLD
            except (ValueError, TypeError):
                pass  # Can't parse timestamp — treat as stale
        if is_stale:
            stale_pending.append(pending_row.get("repo_name"))

    if stale_pending:
        logger.warning(
            "[STALE_PENDING] job=%s - %d pending repos exceeded %s threshold, marking failed: %s",
            job_id, len(stale_pending), _STALE_PENDING_THRESHOLD, stale_pending,
        )
        for stale_repo in stale_pending:
            table_manager.update_repo_status(
                job_id,
                stale_repo,
                {"status": "failed", "error": "stale: no queue message processed within timeout"},
            )
        failed_list.extend(stale_pending)
        failed_list.sort()
    
    logger.info(
        "[CACHE_PROGRESS] job=%s summary_ready=%d failed=%d synced=%d pending=%d total=%d",
        job_id,
        len(summary_ready_list),
        len(failed_list),
        len(synced_list),
        len(pending_rows),
        total_repos,
    )
    
    # Get current job status to check for transitions
    job = table_manager.get_job_metadata(username, job_id)
    current_status = job.get("status") if job else "queued"
    
    # Transition: metadata_ready → caching_started when first repo is processed (success or failure)
    if (len(summary_ready_list) > 0 or len(failed_list) > 0) and current_status == "metadata_ready":
        logger.info(
            "[JOB_CACHING_STARTED] job=%s - First repo processed (%d summary_ready, %d failed / %d total)",
            job_id,
            len(summary_ready_list),
            len(failed_list),
            total_repos,
        )
        table_manager.update_job_metadata(username, job_id, {"status": "caching_started"})

    
    # Transition: caching_started → completed when all repos have summary or failed
    completed_count = len(summary_ready_list) 
    # completed_count = len(summary_ready_list) + len(failed_list)
    if completed_count == total_repos and current_status == "caching_started":
        logger.info(
            "[JOB_COMPLETED] job=%s - All micro-summaries processed (%d summary_ready, %d failed)",
            job_id,
            len(summary_ready_list),
            len(failed_list),
        )
        table_manager.update_job_metadata(username, job_id, {"status": "completed"})



@bp.queue_trigger(arg_name="msg", queue_name="github-cache", connection="AzureWebJobsStorage")
def process_cache_job(msg: func.QueueMessage) -> None:
    """Process micro-summary generation job (zero blob caching).
    
    This worker generates micro-summaries from GitHub metadata + README + extracted configs.
    Files are fetched fresh for each repo but only the micro-summary is persisted.
    
    Fingerprint-based cache invalidation avoids regenerating when repo hasn't changed.
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
        fingerprint = payload.get("fingerprint")
        branch_ref = payload.get("default_branch")  # Use default_branch from queue message for consistency

        queue_message_id = getattr(msg, "id", None)

        if not username or not job_id or not repo_name:
            raise ValueError(
                f"Missing required fields: username={username}, job_id={job_id}, repo_name={repo_name}"
            )

        # Fetch files and extract signals (in-memory only)
        fetch_result = _fetch_file_content(username, repo_name, job_id=job_id, ref=branch_ref)

        summary_ready = False
        summary_failed = False
        
        summary_manager = SummaryManager(username=username)

        metadata_row = table_manager.get_repo_github_metadata(username, repo_name) or {}
        repo_languages_raw = table_manager.get_repo_languages(job_id, repo_name)

        repo_languages = [
            lang for lang in repo_languages_raw
            if lang.get("repo_name") == repo_name
        ]

        languages_tuples = [
            (lang.get("language"), lang.get("percentage")) 
            for lang in sorted(repo_languages, key=lambda x: x.get("percentage", 0), reverse=True)
        ][:5]  # Top 5 languages

        cache_key = summary_manager.build_repo_micro_summary_cache_key(repo_name, fingerprint)
        table_manager.register_pending_cache_summary(username, repo_name, fingerprint, cache_key)

        summary_result = summary_manager.generate_repo_micro_summary(
            repo_name=repo_name,
            fingerprint=fingerprint,
            job_id=job_id,
            repo_metadata={
                "name": repo_name,
                "description": metadata_row.get("description") or "",
                "topics": metadata_row.get("topics") or [],
                "languages": languages_tuples,
                "stats": {
                    "stars": metadata_row.get("stars_count", 0),
                    "forks": metadata_row.get("forks_count", 0),
                },
            },
            primary_readme_content=fetch_result.get("primary_readme_content"),
            config_content=fetch_result.get("config_content", {}),
            secondary_readme_content=list(fetch_result.get("readme_content", {}).values()), 
        )
        
        error_msg = None
        if summary_result.get("summary"):
            summary_ready = True
            logger.info("[MICRO_SUMMARY] Generated for %s/%s", username, repo_name)
        else:
            summary_failed = True
            error_msg = summary_result.get("error")
            logger.warning("[MICRO_SUMMARY] Failed for %s/%s error=%s", username, repo_name, error_msg)
        
        # Update progress tracking (status: synced → summary_ready or failed)
        _update_cache_progress(
            job_id,
            username,
            repo_name,
            summary_failed=summary_failed,
            summary_ready=summary_ready,
            message_id=queue_message_id,
            trace_id=trace_id,
            error=error_msg,
        )

    except ValueError as ve:
        if job_id and username and repo_name:
            _update_cache_progress(
                job_id,
                username,
                repo_name,
                summary_failed=True,
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
                summary_failed=True,
                message_id=queue_message_id,
                trace_id=trace_id,
                error=str(exc),
            )
        raise

