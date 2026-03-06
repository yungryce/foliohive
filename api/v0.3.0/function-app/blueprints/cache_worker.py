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

logger = logging.getLogger("foliohive.cache_worker")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

STANDARD_CONFIG_FETCH_LIMIT = 20
STANDARD_CONFIG_MAX_CHARS = 4000
READ_ME_EXCERPT_MAX_CHARS = 4096


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

    mode = "rest"
    if os.getenv("ENABLE_CONFIG_DISCOVERY_GRAPHQL", "false").lower() == "true":
        mode = "graphql"

    # Fetch files from GitHub (in-memory only)
    # Use default_branch from queue message to avoid redundant metadata API call
    discovery = repo_manager.get_repo_blob_files(
        username=username,
        repo=repo_name,
        mode=mode,
        ref=ref,
        purpose="file_cache",
        job_id=job_id,
        limit=STANDARD_CONFIG_FETCH_LIMIT,
        max_chars=STANDARD_CONFIG_MAX_CHARS,
        readme_max_chars=READ_ME_EXCERPT_MAX_CHARS,
    )

    config_files = discovery.get("config_files", {})
    readme_files = discovery.get("readme_files", {})
    primary_readme = discovery.get("primary_readme", "")
    api_usage = discovery.get("api_usage", {})
    paths_discovered = discovery.get("paths_discovered", 0)
    
    # Validate file fetch completeness (only if paths were discovered)
    total_discovered = len(config_files) + len(readme_files)
    file_targets = api_usage.get("file_targets", {})
    requested_count = len([ft for ft in file_targets.values() if ft.get("selected")])
    missing_count = max(0, requested_count - total_discovered) if requested_count > 0 else 0
    
    if paths_discovered > 0:  # Only validate fetch if paths were discovered
        if missing_count > 0 or requested_count != total_discovered:
            logger.warning(
                "[FILE_FETCH_VALIDATION] repo=%s mode=%s discovered=%d requested=%d returned=%d missing=%d readme_found=%s",
                repo_name, mode, paths_discovered, requested_count, total_discovered, missing_count, bool(primary_readme)
            )
        else:
            logger.info(
                "[FILE_FETCH_VALIDATION] repo=%s mode=%s discovered=%d requested=%d returned=%d complete=True readme_found=%s",
                repo_name, mode, paths_discovered, requested_count, total_discovered, bool(primary_readme)
            )
    
    # Extract config signals (in-memory only, no blob persistence)
    extracted_config_content = repo_manager.extract_config_payloads(config_files)
    
    # Serialize extracted dicts to JSON strings for consistent token estimation
    # build_repo_context() expects string values for accurate token counting
    serialized_config_files = {}
    for filename, payload in extracted_config_content.items():
        if isinstance(payload, dict):
            serialized_config_files[filename] = json.dumps(payload)
        else:
            # Fallback for non-dict payloads (shouldn't occur, but defensive)
            serialized_config_files[filename] = str(payload)
    extracted_config_content = serialized_config_files
    
    logger.info(
        "[EXTRACTION_COMPLETE] repo=%s extracted=%d total_discovered=%d",
        repo_name,
        len(extracted_config_content),
        len(config_files),
    )
    
    # Log if proceeding with partial data
    has_readme = bool(primary_readme)
    config_count = len(extracted_config_content)
    expected_configs = len(config_files)  # Pre-extraction count
    
    # Categorize the data quality
    if paths_discovered == 0:
        data_quality = "no_paths"
    elif not has_readme and config_count == 0:
        data_quality = "metadata_only"
    elif not has_readme:
        data_quality = "partial_no_readme"
    elif config_count < expected_configs:
        data_quality = "partial_incomplete_configs"
    elif missing_count > 0:
        data_quality = "fetch_failures"
    else:
        data_quality = "complete"
    
    if data_quality != "complete":
        logger.warning(
            "[DATA_QUALITY] repo=%s quality=%s discovered=%d readme=%s configs=%d/%d missing_files=%d",
            repo_name, data_quality, paths_discovered, has_readme, config_count, expected_configs, missing_count
        )
    
    logger.info(
        "[FETCH_COMPLETE] repo=%s readme_length=%d extracted_configs=%s api_usage_summary=%s",
        repo_name,
        len(primary_readme),
        list(extracted_config_content.keys()),
        {
            "total_requests": api_usage.get("totals", {}).get("requests", 0),
            "cache_hits": sum(ft.get("cache_hits", 0) for ft in api_usage.get("file_targets", {}).values())
        },
    )

    return {
        "readme_content": primary_readme,
        "config_content": extracted_config_content,
        "api_usage": api_usage,
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
    logger.info("***********************ertisn************************")
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

        # Fetch files and extract signals (in-memory only)
        fetch_result = _fetch_file_content(username, repo_name, job_id=job_id, ref=branch_ref)
        logger.info(
            "[FETCH_RESULT_DETAIL] repo=%s readme_content=%s",
            repo_name,
            fetch_result.get("readme_content", "")[:100] if fetch_result.get("readme_content") else "<empty>",
        )

        # Log extracted config content
        extracted_config_content = fetch_result.get("config_content", {})
        for config_name, config_payload in extracted_config_content.items():
            logger.info(
                "[EXTRACTED_CONFIG] repo=%s config=%s payload=%s",
                repo_name,
                config_name,
                json.dumps(config_payload, indent=2) if isinstance(config_payload, dict) else str(config_payload),
            )

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
        logger.info(
            "[CACHE_REGISTRATION] repo=%s fingerprint=%s cache_key=%s status=pending",
            repo_name, fingerprint, cache_key
        )

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
            readme_content=fetch_result.get("readme_content"),
            config_content=fetch_result.get("config_content", {}),
        )
        logger.info("[MICRO_SUMMARY_RESULT] repo=%s result_keys=%s", repo_name, sorted(summary_result.keys()))
        
        if summary_result.get("summary"):
            summary_ready = True
            logger.info("[MICRO_SUMMARY] Generated for %s/%s", username, repo_name)
        else:
            summary_failed = True
            logger.warning("[MICRO_SUMMARY] Failed for %s/%s reason=%s", username, repo_name, summary_result.get("reason") or summary_result.get("error"))
        
        # Update progress tracking (status: synced → summary_ready or failed)
        _update_cache_progress(
            job_id,
            username,
            repo_name,
            summary_failed=summary_failed,
            summary_ready=summary_ready,
            message_id=queue_message_id,
            trace_id=trace_id,
        )

    except ValueError as ve:
        logger.info("************************ValueError processing cache job")
        logger.info("ValueError processing cache job: %s", str(ve))
        logger.error("ValueError processing cache job: %s", str(ve))
        logger.warning("ValueError processing cache job: %s", str(ve))
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
        logger.info("************************Exception processing cache job")
        logger.info("Exception processing cache job: %s", str(exc))
        logger.error("Exception processing cache job: %s", str(exc))
        logger.warning("Exception processing cache job: %s", str(exc))
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

