"""Sync worker blueprint.

Consumes messages from the ``github-sync`` queue, fetches repository context
using shared managers, and persists results to the cache. 
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, Optional

import azure.functions as func

from foliohive_shared import (
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    queue_manager,
    table_manager,
)
from foliohive_shared.table import RepoLanguagesRow, RepoGitHubMetadataRow

logger = logging.getLogger("foliohive.sync_worker")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

JOB_METADATA_TTL_SECONDS = 4 * 3600
READ_ME_EXCERPT_MAX_CHARS = 4096
STANDARD_CONFIG_FETCH_LIMIT = 20
STANDARD_CONFIG_MAX_CHARS = 4000


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
        logger.error("Queue message body is empty or whitespace")
        raise ValueError("Queue message body is empty")

    payload = json.loads(body_str)
    return payload


def _fetch_repo_metadata(
    username: str,
    repo_name: str,
    fingerprint: str,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch repository metadata only (fast) - stored in table_manager.
    
    Args:
        username: GitHub username
        repo_name: Repository name
        fingerprint: Optional ETag for conditional fetch
        job_id: Job ID for tracking and persistence
    
    Fetches:
    - Repo metadata (description, stars, forks, etc.)
    - Languages statistics
    - File type discovery (filenames only, no content)
    
    Does NOT fetch file contents (readme/config). Use _fetch_and_cache_files for that.
    
    Returns:
        Dict with 'fingerprint', 'api_usage', and 'default_branch' keys
    """
    repo_manager = _get_repo_manager(username)
    if not repo_name:
        raise ValueError("Repository name missing")

    try:
        repo_metadata = repo_manager.get_repo_metadata(
            username=username,
            repo=repo_name,
            purpose="metadata_sync",
            job_id=job_id,
            include_languages=True,
        )
    except Exception as exc:
        logger.error("Failed to fetch metadata for %s/%s: %s", username, repo_name, exc)
        raise

    resolved_fingerprint = fingerprint or FingerprintManager.generate_metadata_fingerprint(repo_metadata)
    default_branch = repo_metadata.get("default_branch")

    _persist_repo_metadata(username, repo_name, repo_metadata, resolved_fingerprint, job_id, default_branch)

    return {
        "fingerprint": resolved_fingerprint,
        "default_branch": default_branch,
    }


def _persist_repo_metadata(
    username: str,
    repo_name: str,
    repo_metadata: Dict[str, Any],
    fingerprint: str,
    job_id: Optional[str] = None,
    default_branch: Optional[str] = None,
) -> None:
    """Persist repo metadata to normalized tables.
    
    Persists to table_manager:
    - RepoLanguages: language statistics (partitioned by job_id)
    - RepoGitHubMetadata: GitHub API fields + fingerprint + default_branch
    
    File contents are handled separately by cache_worker._fetch_and_cache_files.
    """
    if not repo_name:
        logger.error("Cannot persist repo metadata without repo_name")
        return

    languages = repo_metadata.get("languages", {})
    if languages and isinstance(languages, dict) and job_id:
        total_bytes = sum(v for v in languages.values() if isinstance(v, (int, float)))
        lang_rows = []
        
        for lang, byte_count in languages.items():
            if not isinstance(byte_count, (int, float)):
                continue
            percentage = (byte_count / total_bytes * 100) if total_bytes > 0 else 0
            lang_rows.append(
                RepoLanguagesRow(
                    username=username,
                    job_id=job_id,
                    repo_name=repo_name,
                    language=lang,
                    bytes_count=int(byte_count),
                    percentage=round(percentage, 2),
                )
            )
        if lang_rows:
            table_manager.batch_upsert_repo_languages(lang_rows)

    github_row = RepoGitHubMetadataRow(
        username=username,
        repo_name=repo_name,
        job_id=job_id,
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
        default_branch=default_branch,
    )
    table_manager.upsert_repo_github_metadata(github_row)


def _update_sync_progress(
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
    
    Tracks all valid repo states (pending, synced, cached, summary_ready, failed)
    to provide accurate job-level progress regardless of which worker transitions repos first.
    """

    status_value = "failed" if sync_failed else "synced"
    now = datetime.now(timezone.utc).isoformat()

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

    statuses = table_manager.list_repo_statuses(job_id)
    total_repos = len(statuses)  # Complete job manifest
    status_counts = Counter(row.get("status") for row in statuses)
    
    synced_count = status_counts.get("synced", 0)
    failed_count = status_counts.get("failed", 0)
    summary_ready_count = status_counts.get("summary_ready", 0)

    job = table_manager.get_job_metadata(username, job_id)
    current_status = job.get("status") if job else "queued"
    
    if (synced_count > 0 or failed_count > 0 or summary_ready_count > 0) and current_status == "queued":
        table_manager.update_job_metadata_conditional(username, job_id, {"status": "syncing"})
    
    completed_count = synced_count + failed_count + summary_ready_count
    if completed_count == total_repos and current_status == "syncing":
        if (synced_count + summary_ready_count) > 0:
            table_manager.update_job_metadata_conditional(username, job_id, {"status": "metadata_ready"})
        else:
            table_manager.update_job_metadata_conditional(username, job_id, {"status": "failed"})



@bp.queue_trigger(arg_name="msg", queue_name="github-sync", connection="AzureWebJobsStorage")
def process_sync_job(msg: func.QueueMessage) -> None:
    started_at = perf_counter()
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

        if not username or not job_id or not repo_name:
            raise ValueError(
                f"Missing required fields: username={username}, job_id={job_id}, repo_name={repo_name}"
            )

        fetch_result = _fetch_repo_metadata(username, repo_name, fingerprint, job_id=job_id)
        
        _update_sync_progress(
            job_id,
            username,
            repo_name,
            sync_failed=False,
            message_id=message_id,
        )
        
        default_branch = fetch_result.get("default_branch")
        enqueued = queue_manager.enqueue_cache(
            username=username,
            job_id=job_id,
            repo_name=repo_name,
            trace_id=trace_id,
            fingerprint=fingerprint,
            default_branch=default_branch,
        )
        if not enqueued:
            logger.error("[CACHE_ENQUEUE_FAILED] job=%s repo=%s - Failed to enqueue cache job, marking sync failed", job_id, repo_name)
            _update_sync_progress(
                job_id,
                username,
                repo_name,
                sync_failed=True,
                message_id=message_id,
                error="Cache enqueue failed",
            )
    except ValueError as ve:
        if job_id and username and repo_name:
            _update_sync_progress(
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
            _update_sync_progress(
                job_id,
                username,
                repo_name,
                sync_failed=True,
                message_id=message_id,
                error=str(exc),
            )
        raise
