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
from collections import Counter
from typing import Any, Dict, Optional
from datetime import datetime, timezone, timedelta
from time import perf_counter

import azure.functions as func

from foliohive_shared import (
    GitHubAPI,
    GitHubRepoManager,
    RepoCacheSummaryRow,
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
    *,
    message_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Update job progress after micro-summary generation completes.
    
    Status transitions: synced → summary_ready (or failed).
    Tracks micro-summary caching progress.
    """
    
    status_value = "failed" if summary_failed else "summary_ready"
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

    # Count all states using Counter (Pythonic, single pass)
    status_counts = Counter(row.get("status") for row in statuses)
    
    # Detect and log unknown statuses
    known_statuses = {"synced", "summary_ready", "failed", "pending"}
    unknown_statuses = set(status_counts.keys()) - known_statuses
    for unknown_status in unknown_statuses:
        logger.warning(
            "[UNKNOWN_STATUS] job=%s status='%s' count=%d",
            job_id,
            unknown_status,
            status_counts[unknown_status],
        )
    
    # Get counts for known states
    synced_count = status_counts.get("synced", 0)
    failed_count = status_counts.get("failed", 0)
    pending_count = status_counts.get("pending", 0)
    summary_ready_count = status_counts.get("summary_ready", 0)
    
    logger.info(
        "[CACHE_PROGRESS] job=%s summary_ready=%d failed=%d synced=%d pending=%d total=%d",
        job_id,
        summary_ready_count,
        failed_count,
        synced_count,
        pending_count,
        total_repos,
    )
    
    job = table_manager.get_job_metadata(username, job_id)
    current_status = job.get("status") if job else "queued"
    
    if (summary_ready_count > 0 or failed_count > 0) and current_status in ("syncing", "metadata_ready"):
        table_manager.update_job_metadata_conditional(username, job_id, {"status": "caching_started"})
    
    # Transition: caching_started → completed when all repos have summary or failed
    completed_count = summary_ready_count + failed_count
    if completed_count == total_repos and current_status == "caching_started":
        table_manager.update_job_metadata_conditional(username, job_id, {"status": "completed"})


@bp.queue_trigger(arg_name="msg", queue_name="github-cache", connection="AzureWebJobsStorage")
def process_cache_job(msg: func.QueueMessage) -> None:
    """Process micro-summary generation job (zero blob caching).
    
    This worker generates micro-summaries from GitHub metadata + README + extracted configs.
    Files are fetched fresh for each repo but only the micro-summary is persisted.
    
    Fingerprint-based cache invalidation avoids regenerating when repo hasn't changed.
    """
    started_at = perf_counter()
    payload = None
    username = None
    job_id = None
    repo_name = None
    trace_id = None
    logger.info("[LATENCY_START] fn=process_cache_job")

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

        summary_manager = SummaryManager(username=username)
        summary_failed = False

        # Fetch files and extract signals (in-memory only)
        fetch_result = _fetch_file_content(username, repo_name, job_id=job_id, ref=branch_ref)

        metadata_row = table_manager.get_repo_github_metadata(username, repo_name) or {}
        repo_languages = table_manager.get_repo_languages(username, repo_name)

        languages_tuples = [
            (lang.get("language"), lang.get("percentage")) 
            for lang in sorted(repo_languages, key=lambda x: x.get("percentage", 0), reverse=True)
        ][:5]  # Top 5 languages

        cache_key = summary_manager.build_repo_micro_summary_cache_key(repo_name, fingerprint)
        pending_cache_entry = RepoCacheSummaryRow(
            repo_name=repo_name,
            fingerprint=fingerprint,
            job_id=job_id,
            cache_key=cache_key,
            cache_status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        table_manager.upsert_cache_summary(pending_cache_entry)

        summary_result = summary_manager.generate_repo_micro_summary(
            username=username,
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
            skip_cache_lookup=True,
        )
        
        error_msg = None
        if not summary_result.get("summary"):
            summary_failed = True
            error_msg = summary_result.get("error")
            logger.warning("[MICRO_SUMMARY] Failed for %s/%s error=%s", username, repo_name, error_msg)
        
        # Update progress tracking (status: synced → summary_ready or failed)
        _update_cache_progress(
            job_id,
            username,
            repo_name,
            summary_failed=summary_failed,
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
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "[LATENCY_FINISH] fn=process_cache_job job_id=%s username=%s repo=%s elapsed_ms=%.2f",
            job_id,
            username,
            repo_name,
            elapsed_ms,
        )

