"""Timer-trigger reconciliation worker.

Re-enqueues missing repo sync jobs
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import azure.functions as func

from foliohive_shared import cache_manager, queue_manager, table_manager
from foliohive_shared.github.github_repo_manager import get_non_bundle_cache_prefixes

logger = logging.getLogger("cloudfolio.reconciler")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

DEFAULT_SCHEDULE = "0 */3 * * * *"  # every 3 minutes
JOB_CLEANUP_SCHEDULE = "0 0 */3 * * *"  # every 3 hours
REPO_METADATA_CLEANUP_SCHEDULE = "0 0 */2 * * *"  # every 2 hours
DISCOVERED_PATHS_CLEANUP_SCHEDULE = "0 0 */2 * * *"  # every 2 hours
CACHE_CLEANUP_SCHEDULE = "0 0 */6 * * *"  # every 6 hours


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@bp.timer_trigger(arg_name="timer", schedule=JOB_CLEANUP_SCHEDULE, run_on_startup=True)
def cleanup_old_jobs(timer: func.TimerRequest) -> None:
    """Cleanup old jobs and cascade-delete related job-scoped tables.
    
    Job-based cleanup pattern: Removes completed/failed job artifacts after retention period.
    Cascade deletes: JobMetadata, RepoLanguages, RepoSyncStatus.
    """
    if os.getenv("CF_JOB_CLEANUP_ENABLED", "true").lower() != "true":
        return

    retention_days = _env_int("CF_JOB_RETENTION_DAYS", 30)
    cutoff = (_utcnow() - timedelta(days=retention_days)).isoformat()

    deleted_rows = table_manager.cleanup_old_jobs(cutoff)
    if deleted_rows:
        logger.info("[JOB_CLEANUP] deleted_rows=%d (older than %d days)", deleted_rows, retention_days)


@bp.timer_trigger(arg_name="timer", schedule=REPO_METADATA_CLEANUP_SCHEDULE, run_on_startup=True)
def cleanup_old_repo_github_metadata(timer: func.TimerRequest) -> None:
    """Cleanup stale RepoGitHubMetadata entries.
    
    Fingerprint-based cleanup pattern: Removes cached metadata not recently accessed.
    Stale metadata will be refetched on next sync via fingerprint comparison in _identify_repo_freshness.
    """
    if os.getenv("CF_REPO_GITHUB_METADATA_CLEANUP_ENABLED", "true").lower() != "true":
        return

    retention_days = _env_int("CF_REPO_GITHUB_METADATA_RETENTION_DAYS", 30)
    cutoff = (_utcnow() - timedelta(days=retention_days)).isoformat()

    deleted_metadata = table_manager.cleanup_old_repo_github_metadata(cutoff)
    if deleted_metadata:
        logger.info("[REPO_GITHUB_METADATA_CLEANUP] deleted_metadata=%d (older than %d days)", deleted_metadata, retention_days)


@bp.timer_trigger(arg_name="timer", schedule=DISCOVERED_PATHS_CLEANUP_SCHEDULE, run_on_startup=True)
def cleanup_old_discovered_paths(timer: func.TimerRequest) -> None:
    """Cleanup stale RepoDiscoveredPaths entries.
    
    Fingerprint-based cleanup pattern: Removes cached blob path references not recently accessed.
    Orphaned or fingerprint-mismatched paths will be refetched on next cache job.
    """
    if os.getenv("CF_DISCOVERED_PATHS_CLEANUP_ENABLED", "true").lower() != "true":
        return

    retention_days = _env_int("CF_DISCOVERED_PATHS_RETENTION_DAYS", 30)
    cutoff = (_utcnow() - timedelta(days=retention_days)).isoformat()

    deleted_paths = table_manager.cleanup_old_discovered_paths(cutoff)
    if deleted_paths:
        logger.info("[DISCOVERED_PATHS_CLEANUP] deleted_paths=%d (older than %d days)", deleted_paths, retention_days)
