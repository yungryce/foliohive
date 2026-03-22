"""Timer-trigger reconciliation worker.

Handles two independent cleanup concerns:
- Candidate-level cleanup: delete all data for candidates inactive beyond retention period
- Cache summary cleanup: remove stale RepoCacheSummary table rows (secondary sweep)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import azure.functions as func

from foliohive_shared import cache_manager, table_manager

logger = logging.getLogger("foliohive.reconciler")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

CANDIDATE_CLEANUP_SCHEDULE = "0 0 */6 * * *"    # every 6 hours
CACHE_SUMMARY_CLEANUP_SCHEDULE = "0 0 3 * * *"  # daily at 03:00 UTC


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@bp.route(route="candidate/{username}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
def delete_candidate(req: func.HttpRequest) -> func.HttpResponse:
    """Immediately delete all table data for a candidate, bypassing retention period.

    Deletes candidate-scoped rows only:
    - RepoGitHubMetadata, RepoLanguages, UserProfile
    - JobMetadata, RepoSyncStatus (all jobs for this candidate)

    Global cache data (RepoCacheSummary rows and micro-summary blobs) is intentionally
    preserved. Those are shared by (repo_name, fingerprint) and cleaned up separately
    by the cleanup_stale_cache_summaries timer trigger.
    """
    username = req.route_params.get("username", "").strip()
    if not username:
        return func.HttpResponse(
            json.dumps({"error": "Username required"}),
            status_code=400,
            mimetype="application/json",
        )

    rows_deleted = table_manager.cleanup_candidate_data(username)
    logger.info(
        "[CANDIDATE_DELETE] username=%s rows_deleted=%d",
        username, rows_deleted,
    )
    return func.HttpResponse(
        json.dumps({"username": username, "rows_deleted": rows_deleted}),
        status_code=200,
        mimetype="application/json",
    )


@bp.timer_trigger(arg_name="timer", schedule=CANDIDATE_CLEANUP_SCHEDULE)
def cleanup_stale_candidates(timer: func.TimerRequest) -> None:
    """Delete all table data for candidates inactive beyond the retention period.

    A candidate is considered stale when their most recent JobMetadata row has
    not been updated within CF_CANDIDATE_RETENTION_DAYS (default: 30).

    For each stale candidate the following are deleted:
    - RepoGitHubMetadata, RepoLanguages, UserProfile (candidate-scoped table rows)
    - JobMetadata, RepoSyncStatus (lifecycle rows, cascade via job_ids)

    Micro-summary blobs and RepoCacheSummary rows are globally shared by
    (repo_name, fingerprint) and are owned by cleanup_stale_cache_summaries.
    """
    if os.getenv("CF_CANDIDATE_CLEANUP_ENABLED", "true").lower() != "true":
        return

    retention_days = _env_int("CF_CANDIDATE_RETENTION_DAYS", 30)
    cutoff = (_utcnow() - timedelta(days=retention_days)).isoformat()

    stale_usernames = table_manager.find_stale_candidates(cutoff)
    if not stale_usernames:
        return

    total_rows = 0
    for username in stale_usernames:
        rows_deleted = table_manager.cleanup_candidate_data(username)
        total_rows += rows_deleted
        logger.info(
            "[CANDIDATE_CLEANUP] username=%s rows_deleted=%d",
            username, rows_deleted,
        )

    logger.info(
        "[CANDIDATE_CLEANUP] total_candidates=%d total_rows=%d cutoff=%s",
        len(stale_usernames), total_rows, cutoff,
    )


@bp.timer_trigger(arg_name="timer", schedule=CACHE_SUMMARY_CLEANUP_SCHEDULE)
def cleanup_stale_cache_summaries(timer: func.TimerRequest) -> None:
    """Delete RepoCacheSummary rows and their blobs not updated within the retention period.

    RepoCacheSummary rows and micro-summary blobs are globally shared by
    (repo_name, fingerprint). A longer retention window (default: 30 days) ensures
    no active candidate loses a blob that another candidate triggered the cache for.
    """
    if os.getenv("CF_CACHE_SUMMARY_CLEANUP_ENABLED", "true").lower() != "true":
        return

    retention_days = _env_int("CF_CACHE_SUMMARY_RETENTION_DAYS", 30)
    cutoff = (_utcnow() - timedelta(days=retention_days)).isoformat()

    deleted_keys = table_manager.cleanup_stale_cache_summaries(cutoff)
    if deleted_keys:
        blobs_deleted = sum(1 for key in deleted_keys if cache_manager.delete(key))
        logger.info(
            "[CACHE_SUMMARY_CLEANUP] deleted_rows=%d blobs_deleted=%d (older than %d days)",
            len(deleted_keys), blobs_deleted, retention_days,
        )

