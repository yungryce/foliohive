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


def _should_enqueue_merge(
    expected: Iterable[str],
    synced: Iterable[str],
    failed: Iterable[str],
) -> bool:
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
    synced: Set[str] = set()
    failed: Set[str] = set()
    for repo_name in repo_names:
        if not repo_name:
            continue
        row = table_manager.get_repo_status(job_id, repo_name)
        status = (row or {}).get("status")
        if status == "synced":
            synced.add(repo_name)
        elif status == "failed":
            failed.add(repo_name)
    return synced, failed


def _reconcile_session(session: Dict[str, Any]) -> None:
    job_id = session.get("job_id")
    username = session.get("username")
    trace_id = session.get("trace_id")
    if not job_id or not username:
        return

    expected = session.get("expected_repos") or session.get("queued_repos") or []
    if not expected:
        return

    logger.info(
        "[RECONCILE_CHECK] job=%s user=%s status=%s expected=%d",
        job_id,
        username,
        session.get("status") or "<unknown>",
        len(expected) if isinstance(expected, list) else 0,
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
    can_requeue = _should_requeue(session.get("last_requeue_at"), requeue_cooldown)

    if missing and can_requeue and queue_manager.is_enabled():
        requeued: List[str] = []
        for repo_name in sorted(missing):
            if queue_manager.enqueue_sync_job(job_id, username, repo_name, trace_id=trace_id):
                requeued.append(repo_name)
        if requeued:
            table_manager.update_candidate_session(
                username,
                job_id,
                {
                    "queued_repos": sorted(set(expected) | set(requeued)),
                    "last_requeue_at": _utcnow().isoformat(),
                    "status": session.get("status") or "queued",
                },
            )
            logger.info("[RECONCILE] job=%s requeued=%d", job_id, len(requeued))

    if _should_enqueue_merge(expected, synced, failed):
        if session.get("merge_enqueued_at"):
            return
        if queue_manager.is_enabled():
            queued = queue_manager.enqueue_merge_job(job_id, username, sorted(synced), trace_id=trace_id)
            if queued:
                table_manager.update_candidate_session(
                    username,
                    job_id,
                    {"merge_enqueued_at": _utcnow().isoformat(), "status": "synced"},
                )
                logger.info("[RECONCILE] job=%s merge enqueued", job_id)


@bp.timer_trigger(arg_name="timer", schedule=DEFAULT_SCHEDULE, run_on_startup=False)
def reconcile_jobs(timer: func.TimerRequest) -> None:
    if not table_manager.is_enabled():
        logger.warning("[RECONCILE] Table manager disabled; skipping")
        return
    min_age_seconds = _env_int("CF_RECONCILE_MIN_AGE_SECONDS", 180)
    updated_before = (_utcnow() - timedelta(seconds=min_age_seconds)).isoformat()
    sessions = table_manager.list_candidate_sessions_by_status(
        ["queued", "processing", "synced"],
        updated_before=updated_before,
    )
    if not sessions:
        return
    for session in sessions:
        _reconcile_session(session)


@bp.timer_trigger(arg_name="timer", schedule=CACHE_CLEANUP_SCHEDULE, run_on_startup=False)
def cleanup_non_bundle_cache(timer: func.TimerRequest) -> None:
    if os.getenv("CF_CACHE_CLEANUP_ENABLED", "true").lower() != "true":
        return
    max_age_hours = _env_int("CF_CACHE_CLEANUP_MAX_AGE_HOURS", 24)
    prefixes = get_non_bundle_cache_prefixes()
    deleted = cache_manager.cleanup_stale_non_bundle_blobs(prefixes, max_age_hours=max_age_hours)
    if deleted:
        logger.info("[CACHE_CLEANUP] deleted=%d", deleted)
