"""Timer-trigger reconciliation worker.

Re-enqueues missing repo sync jobs and ensures merge is triggered once per job.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import azure.functions as func

from cloudfolio_shared import cache_manager, queue_manager, table_manager
from cloudfolio_shared.github.github_repo_manager import get_non_bundle_cache_prefixes

logger = logging.getLogger("cloudfolio.reconciler")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

DEFAULT_SCHEDULE = "0 */3 * * * *"  # every 3 minutes
CACHE_CLEANUP_SCHEDULE = "0 0 */6 * * *"  # every 6 hours


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.rstrip("Z") + ("+00:00" if value.endswith("Z") else "")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _compute_missing_repos(
    expected: Iterable[str],
    synced: Iterable[str],
    failed: Iterable[str],
) -> Set[str]:
    expected_set = {name for name in expected if name}
    processed = {name for name in synced if name} | {name for name in failed if name}
    return expected_set - processed


def _should_trigger_cache_jobs(
    expected: Iterable[str],
    synced: Iterable[str],
    failed: Iterable[str],
) -> bool:
    """Check if all expected repos are synced and ready for cache jobs.
    
    With merge worker removed, reconciliation now only ensures cache jobs
    are enqueued after sync completes. Cache worker handles final completion.
    """
    expected_set = {name for name in expected if name}
    if not expected_set:
        return False
    processed = {name for name in synced if name} | {name for name in failed if name}
    has_synced = any(synced)
    return bool(expected_set) and has_synced and expected_set.issubset(processed)


def _should_requeue(last_requeue_at: Optional[str], cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return True
    last_time = _parse_iso_ts(last_requeue_at)
    if not last_time:
        return True
    return _utcnow() - last_time >= timedelta(seconds=cooldown_seconds)


def _collect_job_sets(job_id: str, repo_names: Iterable[str]) -> Tuple[Set[str], Set[str]]:
    """Collect repos that completed metadata sync vs failed.
    
    Returns:
        Tuple of (synced_or_cached, failed) repo name sets
        synced_or_cached: Repos that completed metadata sync (may or may not have files cached)
        failed: Repos that failed at any stage
    """
    synced: Set[str] = set()
    failed: Set[str] = set()
    for repo_name in repo_names:
        if not repo_name:
            continue
        row = table_manager.get_repo_status(job_id, repo_name)
        status = (row or {}).get("status")
        # Count synced or cached repos as successfully processed for metadata
        if status in ("synced", "cached"):
            synced.add(repo_name)
        elif status == "failed":
            failed.add(repo_name)
    return synced, failed


def _reconcile_session(job: Dict[str, Any]) -> None:
    """Reconcile a job session by re-enqueuing missing repos and triggering cache jobs.
    
    Uses RepoSyncStatus as source of truth for expected repos (normalized schema).
    Supports current job statuses: queued, syncing, metadata_ready, completed, failed.
    """
    job_id = job.get("job_id")
    username = job.get("username")
    trace_id = job.get("trace_id")
    if not job_id or not username:
        return

    # Get expected repos from RepoSyncStatus table (normalized schema)
    statuses = table_manager.list_repo_statuses(job_id)
    if not statuses:
        return
    
    expected = [s.get("repo_name") for s in statuses if s.get("repo_name")]

    logger.info(
        "[RECONCILE_CHECK] job=%s user=%s status=%s expected=%d",
        job_id,
        username,
        job.get("status") or "<unknown>",
        len(expected),
    )

    synced, failed = _collect_job_sets(job_id, expected)
    missing = _compute_missing_repos(expected, synced, failed)

    logger.info(
        "[RECONCILE_SETS] job=%s user=%s synced=%d failed=%d missing=%d",
        job_id,
        username,
        len(synced),
        len(failed),
        len(missing),
    )

    requeue_cooldown = _env_int("CF_RECONCILE_REQUEUE_COOLDOWN_SECONDS", 600)
    can_requeue = _should_requeue(job.get("last_requeue_at"), requeue_cooldown)

    if missing and can_requeue:
        requeued: List[str] = []
        for repo_name in sorted(missing):
            if queue_manager.enqueue_sync_job(job_id, username, repo_name, trace_id=trace_id):
                requeued.append(repo_name)
        if requeued:
            # Preserve current job status when requeuing
            current_status = job.get("status", "queued")
            table_manager.update_job_metadata(
                username,
                job_id,
                {"last_requeue_at": _utcnow().isoformat()},
            )
            logger.info(
                "[RECONCILE] job=%s status=%s requeued=%d",
                job_id,
                current_status,
                len(requeued),
            )

    # Note: With merge worker removed, cache jobs are now enqueued by sync_worker.
    # Reconciliation only ensures missing cache jobs are triggered if sync completed
    # but cache jobs were never enqueued (edge case recovery).
    if _should_trigger_cache_jobs(expected, synced, failed):
        # Check if cache jobs were already enqueued by looking at RepoSyncStatus
        statuses = table_manager.list_repo_statuses(job_id)
        has_cached = any(s.get("status") in ("cached", "failed") for s in statuses)
        
        if not has_cached:
            # Edge case: sync completed but no cache jobs were enqueued
            # This shouldn't happen in normal flow, but reconciliation ensures recovery
            logger.warning(
                "[RECONCILE] job=%s - Sync completed but no cache progress detected, triggering cache jobs",
                job_id,
            )
            for repo_name in sorted(synced):
                if queue_manager.enqueue_cache_job(
                    username=username,
                    job_id=job_id,
                    repo_name=repo_name,
                    trace_id=trace_id,
                ):
                    logger.info("[RECONCILE] job=%s repo=%s - Cache job enqueued", job_id, repo_name)


@bp.timer_trigger(arg_name="timer", schedule=DEFAULT_SCHEDULE, run_on_startup=False)
def reconcile_jobs(timer: func.TimerRequest) -> None:
    """Reconcile incomplete jobs by re-enqueuing missing repos.
    
    Monitors jobs in active states (not completed/failed) to ensure all repos are processed.
    Current job statuses: queued, syncing, metadata_ready, completed, failed.
    """
    min_age_seconds = _env_int("CF_RECONCILE_MIN_AGE_SECONDS", 180)
    updated_before = (_utcnow() - timedelta(seconds=min_age_seconds)).isoformat()
    jobs = table_manager.list_jobs_metadata_by_status(
        ["queued", "syncing", "metadata_ready"],
        updated_before=updated_before,
    )
    if not jobs:
        return
    for job in jobs:
        _reconcile_session(job)


@bp.timer_trigger(arg_name="timer", schedule=CACHE_CLEANUP_SCHEDULE, run_on_startup=False)
def cleanup_non_bundle_cache(timer: func.TimerRequest) -> None:
    if os.getenv("CF_CACHE_CLEANUP_ENABLED", "true").lower() != "true":
        return
    max_age_hours = _env_int("CF_CACHE_CLEANUP_MAX_AGE_HOURS", 24)
    prefixes = get_non_bundle_cache_prefixes()
    deleted = cache_manager.cleanup_stale_non_bundle_blobs(prefixes, max_age_hours=max_age_hours)
    if deleted:
        logger.info("[CACHE_CLEANUP] deleted=%d", deleted)
