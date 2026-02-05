"""API Gateway blueprint for Cloudfolio.

Ported from `api/v0.3.0/api-gateway/function_app.py`.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import azure.functions as func

from cloudfolio_shared import (
    cache_manager,
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    queue_manager,
    AIAssistant,
    RepoScoringService,
    table_manager,
)

from cloudfolio_shared.table import JobMetadataRow, RepoSyncStatusRow, RepoAPIUsageRow, UserProfileRow

try:  # Azure SDK may be unavailable in local dev; ignore import failures gracefully
    from azure.core.exceptions import ResourceNotFoundError
except Exception:  # pragma: no cover - falls back in environments without azure core
    ResourceNotFoundError = None


logger = logging.getLogger("cloudfolio.api_gateway")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

USERNAME_REQUIRED_MESSAGE = "Username required"
PROFILE_TTL_SECONDS = int(os.getenv("CF_PROFILE_TTL_SECONDS", "21600"))
PROFILE_TOP_LANGUAGES_LIMIT = 10


def _get_trace_context(req: func.HttpRequest) -> Dict[str, str]:
    """Build a small correlation context for logs.

    Avoid logging secrets. Keep values small and stable for tracing.
    """

    headers = getattr(req, "headers", {}) or {}
    session_id = headers.get("X-Session-Id") or headers.get("x-session-id") or ""
    request_id = (
        headers.get("X-Request-Id")
        or headers.get("x-request-id")
        or headers.get("X-Ms-Client-Request-Id")
        or headers.get("x-ms-client-request-id")
        or ""
    )
    if not request_id:
        logger.info("Generating new request ID for trace context")
        request_id = str(uuid.uuid4())

    trace_id = headers.get("X-Trace-Id") or headers.get("x-trace-id") or request_id
    return {
        "request_id": request_id,
        "session_id": session_id,
        "trace_id": trace_id,
    }


# ---------------------------------------------------------------------------
# Helper responses
# ---------------------------------------------------------------------------

def _create_success_response(
    data: Dict[str, Any],
    status_code: int = 200,
    cache_control: str = "public, max-age=900",
    request_id: Optional[str] = None,
) -> func.HttpResponse:
    payload = {
        "status": "success",
        "ok": True,
        "data": data,
        "meta": {
            "api_version": "0.3.0",
            "schema_version": "2026-01-27",
            "request_id": request_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
        },
    }
    return func.HttpResponse(
        json.dumps(payload, indent=2),
        status_code=status_code,
        mimetype="application/json",
        headers={
            "Cache-Control": cache_control,
            "Content-Type": "application/json; charset=utf-8",
        },
    )


def _create_error_response(
    message: str,
    status_code: int = 500,
    details: Optional[str] = None,
    error_code: str = "INTERNAL_ERROR",
    request_id: Optional[str] = None,
) -> func.HttpResponse:
    payload: Dict[str, Any] = {
        "status": "error",
        "ok": False,
        "error": {
            "code": error_code,
            "message": message,
        },
        "meta": {
            "api_version": "0.3.0",
            "schema_version": "2026-01-27",
            "request_id": request_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
        },
    }
    if details:
        payload["error"]["details"] = details
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def _restore_iso_timestamp(safe_timestamp: Optional[str]) -> Optional[str]:
    if not safe_timestamp:
        return None
    restored = safe_timestamp.replace("_", "+")
    if "T" in restored:
        date_part, time_part = restored.split("T", 1)
        time_part = time_part.replace("-", ":")
        restored = f"{date_part}T{time_part}"
    return restored


def _parse_json(req: func.HttpRequest) -> Dict[str, Any]:
    try:
        return req.get_json() or {}
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_repo_manager(username: str) -> GitHubRepoManager:
    if not username:
        raise ValueError("Username required")
    token = os.getenv("GITHUB_TOKEN")
    api = GitHubAPI(token=token, username=username)
    return GitHubRepoManager(api, username=username)

def _parse_iso(timestamp: Optional[str]) -> datetime:
    if not timestamp:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _is_profile_fresh(profile: Dict[str, Any], ttl_seconds: int) -> bool:
    cached_at = profile.get("cached_at")
    if not cached_at:
        return False
    cached_dt = _parse_iso(cached_at)
    if cached_dt == datetime.min.replace(tzinfo=timezone.utc):
        return False
    return (datetime.now(timezone.utc) - cached_dt).total_seconds() < ttl_seconds


def _build_profile_statistics(
    repo_rows: List[Dict[str, Any]],
    languages_by_repo: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    repo_count = len(repo_rows)
    stars_total = sum(int(row.get("stars_count") or 0) for row in repo_rows)
    forks_total = sum(int(row.get("forks_count") or 0) for row in repo_rows)

    language_totals: Dict[str, int] = defaultdict(int)
    for repo_languages in languages_by_repo.values():
        for lang in repo_languages or []:
            name = lang.get("language")
            if not name:
                continue
            language_totals[name] += int(lang.get("bytes_count") or 0)

    top_languages = [
        {"language": name, "bytes": bytes_count}
        for name, bytes_count in sorted(language_totals.items(), key=lambda item: item[1], reverse=True)
    ][:PROFILE_TOP_LANGUAGES_LIMIT]

    topics: List[str] = []
    for row in repo_rows:
        row_topics = row.get("topics") or []
        if isinstance(row_topics, list):
            topics.extend([topic for topic in row_topics if isinstance(topic, str) and topic])
    unique_topics = sorted(set(topics))

    return {
        "repo_count": repo_count,
        "stars_total": stars_total,
        "forks_total": forks_total,
        "top_languages": top_languages,
        "topics": unique_topics,
    }


def _build_profile_summary_payload(
    *,
    username: str,
    profile: Optional[Dict[str, Any]],
    job: Optional[Dict[str, Any]],
    repo_rows: List[Dict[str, Any]],
    languages_by_repo: Dict[str, List[Dict[str, Any]]],
    statistics: Dict[str, Any],
    max_repos: int = 8,
) -> Dict[str, Any]:
    """Build a compact, AI-friendly payload for profile summary generation."""
    sorted_repos = sorted(
        repo_rows,
        key=lambda row: (
            int(row.get("stars_count") or 0),
            int(row.get("forks_count") or 0),
            int(row.get("watchers") or 0),
        ),
        reverse=True,
    )
    top_repos = sorted_repos[:max_repos]

    repo_summaries: List[Dict[str, Any]] = []
    for repo in top_repos:
        repo_name = repo.get("repo_name")
        repo_topics = repo.get("topics")
        repo_topics = repo_topics[:10] if isinstance(repo_topics, list) else []

        repo_languages = languages_by_repo.get(repo_name) if repo_name else []
        repo_languages_sorted = sorted(
            repo_languages or [],
            key=lambda lang: int(lang.get("bytes_count") or 0),
            reverse=True,
        )
        top_repo_languages = [
            lang.get("language")
            for lang in repo_languages_sorted
            if lang.get("language")
        ][:3]

        repo_summaries.append(
            {
                "name": repo_name,
                "description": repo.get("description"),
                "primary_language": repo.get("primary_language"),
                "languages": top_repo_languages,
                "topics": repo_topics,
                "stats": {
                    "stars": int(repo.get("stars_count") or 0),
                    "forks": int(repo.get("forks_count") or 0),
                    "watchers": int(repo.get("watchers") or 0),
                    "open_issues": int(repo.get("open_issues") or 0),
                },
                "flags": {
                    "is_fork": bool(repo.get("is_fork")),
                    "is_archived": bool(repo.get("is_archived")),
                },
                "urls": {
                    "github": repo.get("html_url"),
                    "homepage": repo.get("homepage_url"),
                },
                "timestamps": {
                    "created_at": repo.get("github_created_at"),
                    "updated_at": repo.get("github_updated_at"),
                    "pushed_at": repo.get("github_pushed_at"),
                },
            }
        )

    job_metadata = None
    if job:
        job_metadata = {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "updated_at": _restore_iso_timestamp(job.get("updated_at")) or job.get("updated_at"),
            "created_at": _restore_iso_timestamp(job.get("created_at")) or job.get("created_at"),
        }

    return {
        "username": username,
        "github_profile": profile or {},
        "job_metadata": job_metadata or {},
        "statistics": statistics,
        "top_repositories": repo_summaries,
    }


def _refresh_user_profile(username: str, cached_profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    repo_manager = _get_repo_manager(username)
    profile = repo_manager.get_user_profile(username=username)
    if not isinstance(profile, dict):
        return cached_profile

    fingerprint = FingerprintManager.generate_user_profile_fingerprint(profile)
    cached_at = datetime.now(timezone.utc).isoformat()
    row = UserProfileRow(
        username=username,
        github_id=profile.get("id"),
        name=profile.get("name"),
        bio=profile.get("bio"),
        company=profile.get("company"),
        location=profile.get("location"),
        blog=profile.get("blog"),
        email=profile.get("email"),
        twitter_username=profile.get("twitter_username"),
        avatar_url=profile.get("avatar_url"),
        html_url=profile.get("html_url"),
        public_repos=profile.get("public_repos") or 0,
        public_gists=profile.get("public_gists") or 0,
        followers=profile.get("followers") or 0,
        following=profile.get("following") or 0,
        github_created_at=profile.get("created_at"),
        github_updated_at=profile.get("updated_at"),
        fingerprint=fingerprint,
        cached_at=cached_at,
    )
    table_manager.upsert_user_profile(row)
    return table_manager.get_user_profile(username) or asdict(row)


def _get_or_refresh_user_profile(username: str) -> Optional[Dict[str, Any]]:
    cached = table_manager.get_user_profile(username)
    if cached and _is_profile_fresh(cached, PROFILE_TTL_SECONDS):
        return cached

    refreshed = _refresh_user_profile(username, cached)
    return refreshed


def _fetch_candidate_jobs(username: str, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch job metadata - returns specific job if job_id provided, else latest."""
    if job_id:
        # Fetch specific job
        return table_manager.get_job_metadata(username, job_id)
    
    # Fetch latest job for user
    jobs = table_manager.list_jobs_metadata(username)
    if not jobs:
        return None
    jobs = [job for job in jobs if job and job.get("job_id")]
    if not jobs:
        return None
    jobs.sort(key=lambda row: _parse_iso(row.get("updated_at")), reverse=True)
    return jobs[0]


def _record_user_session(trace: Dict[str, str], username: str, job_id: Optional[str]) -> None:
    """Record session tracking for user
    """
    session_id = trace.get("session_id") if trace else ""
    if not session_id or not username:
        return

    try:
        table_manager.upsert_session_candidate(session_id, username, job_id)
    except Exception as exc:  # pragma: no cover - safety net
        logger.warning(
            "Failed to persist user candidate (session_id=%s user=%s job_id=%s): %s",
            session_id,
            username,
            job_id or "<none>",
            exc,
        )

def _check_repo_cache_status(
    username: str,
    repo: str,
    *,
    job_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Check cache status for a repo and enqueue job if needed.
    
    Returns:
        Dict with keys: status, job_id, cache_message_id, error
        status: "cached" | "processing" | "pending" | "failed" | "not_found"
    """
    # Resolve job_id if not provided
    resolved_job_id = job_id
    if not resolved_job_id:
        job = _fetch_candidate_jobs(username)
        resolved_job_id = job.get("job_id") if job else None
    
    if not resolved_job_id:
        return {
            "status": "not_found",
            "message": "No job found for user. Trigger a refresh first.",
        }
    
    # Check RepoSyncStatus
    repo_status = table_manager.get_repo_status(resolved_job_id, repo)
    current_status = repo_status.get("status") if repo_status else None
    cache_message_id = repo_status.get("cache_message_id") if repo_status else None
    error = repo_status.get("error") if repo_status else None
    
    logger.info(
        "[CACHE_STATUS_CHECK] repo=%s job=%s status=%s cache_message_id=%s",
        repo, resolved_job_id, current_status, cache_message_id
    )
    
    result = {
        "job_id": resolved_job_id,
        "cache_message_id": cache_message_id,
    }
    
    if current_status == "cached":
        result["status"] = "cached"
        result["message"] = "Files are cached and ready."
    elif current_status == "synced" and cache_message_id:
        result["status"] = "processing"
        result["message"] = "Cache job is in progress."
    elif current_status == "failed":
        # Re-enqueue on failed status
        logger.info(
            "[CACHE_STATUS_RETRY] repo=%s job=%s - Previous cache job failed, re-enqueueing",
            repo, resolved_job_id
        )
        queue_manager.enqueue_cache_job(
            job_id=resolved_job_id,
            username=username,
            repo_name=repo,
            trace_id=trace_id,
        )
        result["status"] = "processing"
        result["message"] = "Previous cache job failed. Re-enqueued for retry."
        result["error"] = error
    elif current_status == "pending" or not repo_status:
        # Enqueue cache job
        logger.info(
            "[CACHE_STATUS_ENQUEUE] repo=%s job=%s status=%s - Enqueueing cache job",
            repo, resolved_job_id, current_status or "none"
        )
        queue_manager.enqueue_cache_job(
            job_id=resolved_job_id,
            username=username,
            repo_name=repo,
            trace_id=trace_id,
        )
        result["status"] = "processing"
        result["message"] = "Cache job enqueued."
    else:
        result["status"] = "pending"
        result["message"] = f"Unexpected status: {current_status}"
    
    return result


def _get_repo_file_content(
    username: str,
    repo: str,
    *,
    file_type: str = "readme",
) -> Dict[str, Any]:
    """Helper: retrieve cached file contents for a repo.
    
    Retrieves individual cached files (kind="file") by type.
    Raises ValueError if cache is not available.
    """
    if file_type not in ("readme", "config", "all"):
        raise ValueError("Invalid file type. Use: readme, config, or all")

    response_payload = {
        "username": username,
        "repo": repo,
    }

    # Retrieve readme files
    if file_type in ("readme", "all"):
        readme_files = {}
        primary_readme = ""
        
        # Retrieve primary readme
        primary_key = cache_manager.generate_cache_key(
            kind="file",
            username=username,
            repo=repo,
            file_type="readme",
            filename="PRIMARY",
        )
        primary_result = cache_manager.get(primary_key)
        logger.info("Retrieved primary readme cache for user=%s repo=%s status=%s primary_key=%s", username, repo, primary_result.get("status"), primary_key)
        if primary_result.get("status") == "valid":
            primary_readme = primary_result.get("data", "")
            logger.debug("Retrieved primary readme for user=%s repo=%s", username, repo)
        
        response_payload["primary_readme"] = primary_readme
        response_payload["readme_files"] = readme_files

    # Retrieve config files
    if file_type in ("config", "all"):
        config_files = {}
        response_payload["config_files"] = config_files

    logger.info("Retrieved file content for user=%s repo=%s file_type=%s", username, repo, file_type)

    return response_payload


def _query_repo_rows(
    username: str,
    job_id: Optional[str] = None,
    repo_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Query repo GitHub metadata from table storage.

    Queries RepoGitHubMetadata table (which now contains fingerprint).
    Uses RepoSyncStatus to map job_id to repo names in normalized schema.

    Args:
        username: Required username filter
        job_id: Job ID to get repos for (queries RepoSyncStatus first)
        repo_names: Optional list of specific repo names to retrieve

    Returns:
        List of GitHub metadata dicts with fingerprint
    """
    try:
        target_repo_names = repo_names
        
        if job_id and not target_repo_names:
            logger.info("Querying RepoSyncStatus for job=%s to get repo list", job_id)
            status_rows = table_manager.list_repo_statuses(job_id)
            target_repo_names = [
                row["repo_name"] for row in status_rows
                if row.get("status") in ("synced", "cached") and row.get("repo_name")
            ]
            logger.info("Found %d ready repos for job=%s (statuses: synced/cached)", len(target_repo_names), job_id)
        
        if target_repo_names:
            logger.info("Querying GitHub metadata for user=%s repos=%d", username, len(target_repo_names))
            result = []
            for repo_name in target_repo_names:
                meta = table_manager.get_repo_github_metadata(username, repo_name)
                if meta:
                    result.append(meta)
            return result
        else:
            logger.warning("No job_id or repo_names provided for GitHub metadata query")
            return []
    except Exception:
        logger.warning("Failed to query GitHub metadata for user=%s job=%s", username, job_id or "none", exc_info=True)
        return []


def _build_repo_detail_entry(
    username: str,
    repo_name: str,
    *,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper: build a single repo bundle entry using normalized tables."""
    job = _fetch_candidate_jobs(username, job_id)
    resolved_job_id = job.get("job_id") if job else None

    repo_rows = _query_repo_rows(username, job_id=resolved_job_id, repo_names=[repo_name])
    github_metadata = repo_rows[0] if repo_rows else None

    languages = None
    if resolved_job_id:
        all_languages = table_manager.query_repo_languages(resolved_job_id)
        languages = all_languages.get(repo_name)

    entry = _repo_row_to_bundle_entry(languages=languages, github_metadata=github_metadata)
    if not entry.get("name"):
        entry["name"] = repo_name

    return {
        "job_id": resolved_job_id,
        "repo_entry": entry,
    }


def _repo_row_to_bundle_entry(
    languages: Optional[List[Dict[str, Any]]] = None,
    github_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert repo metadata to bundle entry.
    
    """
    entry: Dict[str, Any] = {}

    if languages:
        # Transform list to dict format expected by frontend: {language: bytes_count}
        entry["languages"] = {lang["language"]: lang["bytes_count"] for lang in languages if lang.get("language")}
        total_bytes = sum(lang.get("bytes_count", 0) for lang in languages if isinstance(lang.get("bytes_count"), (int, float)))
        sorted_langs = sorted(
            [
                {
                    "name": lang.get("language"),
                    "pct": lang.get("percentage"),
                    "bytes": lang.get("bytes_count"),
                }
                for lang in languages
                if lang.get("language")
            ],
            key=lambda item: item.get("pct") or 0,
            reverse=True,
        )
        entry["languages_top"] = sorted_langs[:4]
        entry["languages_total_bytes"] = total_bytes

    if github_metadata:
        topics = github_metadata.get("topics") or []
        topics = topics[:10] if isinstance(topics, list) else []
        entry["name"] = github_metadata.get("repo_name")
        entry["urls"] = {
            "github": github_metadata.get("html_url"),
            "homepage": github_metadata.get("homepage_url"),
        }
        entry["stats"] = {
            "stars": github_metadata.get("stars_count", 0),
            "forks": github_metadata.get("forks_count", 0),
        }
        entry["flags"] = {
            "fork": github_metadata.get("is_fork", False),
            "archived": github_metadata.get("is_archived", False),
        }
        entry["timestamps"] = {
            "pushed_at": github_metadata.get("github_pushed_at"),
            "updated_at": github_metadata.get("github_updated_at"),
            "created_at": github_metadata.get("github_created_at"),
        }
        # Map GitHub metadata to expected frontend structure
        entry["metadata"] = {
            "description": github_metadata.get("description"),
            "fingerprint": github_metadata.get("fingerprint"),
            "watchers_count": github_metadata.get("watchers", 0),
            "open_issues_count": github_metadata.get("open_issues", 0),
            "topics": topics,
            "license_name": github_metadata.get("license_name"),
        }
        
    return entry


def _get_candidate_metadata(username: str, job: Dict[str, Any], repo_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build bundle from table data - uses single-pass status counting.
    
    Args:
        repo_rows: GitHub metadata dicts (from RepoGitHubMetadata table)
    """
    job_id = job.get("job_id") or None
    entries: List[Dict[str, Any]] = []

    # Single-pass count of repo statuses (normalized schema: pending → synced → cached)
    status_counts = defaultdict(int)
    status_lists = defaultdict(list)

    if job_id:
        status_rows = table_manager.list_repo_statuses(job_id)
        for row in status_rows:
            status = row.get("status")
            repo_name = row.get("repo_name")
            if status in ("pending", "synced", "cached", "failed") and repo_name:
                status_counts[status] += 1
                status_lists[status].append(repo_name)

        pending = status_lists["pending"]
        synced = status_lists["synced"]
        cached = status_lists["cached"]
        failed = status_lists["failed"]

        if failed or pending or synced:
            logger.info(
                "[CANDIDATE_PROGRESS] job=%s user=%s cached=%d synced=%d pending=%d failed=%d",
                job_id,
                username,
                len(cached),
                len(synced),
                len(pending),
                len(failed),
            )

    # Build entries from GitHub metadata (repo_rows now contains RepoGitHubMetadata)
    # Query all languages once and group by repo_name (more efficient than N queries)
    all_languages_by_repo = table_manager.query_repo_languages(job_id) if job_id else {}

    for github_meta in repo_rows:
        repo_name = github_meta.get("repo_name")
        if not repo_name:
            continue
        
        languages = all_languages_by_repo.get(repo_name, [])
        logger.info(
            "[CANDIDATE_REPO_LANGUAGES] user=%s repo=%s languages_count=%d languages=%s",
            username,
            repo_name,
            len(languages),
            [f"{lang.get('language')}:{lang.get('bytes_count')}" for lang in languages[:3]],
        )
        
        entries.append(
            _repo_row_to_bundle_entry(
                languages=languages,
                github_metadata=github_meta,
            )
        )
    result = {
        "username": username,
        "job_id": job_id,
        "fingerprint": job.get("bundle_fingerprint"),
        "last_modified": _restore_iso_timestamp(job.get("updated_at")) or job.get("updated_at"),
        "status": job.get("status"),
        "data": entries,
    }

    logger.info(
        "[CANDIDATE_METADATA_BUILT] job=%s user=%s repos=%d cached=%d synced=%d pending=%d failed=%d",
        job_id,
        username,
        len(entries),
        status_counts.get("cached", 0),
        status_counts.get("synced", 0),
        status_counts.get("pending", 0),
        status_counts.get("failed", 0),
    )
    logger.info("Built candidate metadata: %s", json.dumps(result, indent=2))
    return result


def _bundle_from_cache(username: str) -> Optional[Dict[str, Any]]:
    """Load bundle from cache (not job metadata).
    
    Per normalized schema: only 'bundle' kind is cached to storage.
    Job metadata queries go to table_manager exclusively.
    """
    bundle_cache_key = cache_manager.generate_cache_key(kind="bundle", username=username)
    result = cache_manager.get(bundle_cache_key)
    if result.get("status") != "valid" or result.get("data") is None:
        return None
    return {
        "username": username,
        "fingerprint": result.get("fingerprint"),
        "last_modified": result.get("last_modified"),
        "size_bytes": result.get("size_bytes"),
        "data": result.get("data"),
    }

def _persist_job_metadata(
    job_id: str,
    username: str,
    *,
    status: str = "queued",
    force_refresh: bool = False,
    created_at: Optional[str] = None,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """Persist job metadata - list fields removed in normalized schema.
    
    Creates or updates a job metadata row. Generates created_at timestamp if not provided.
    Preserves existing bundle_fingerprint, trace_id, and request_id on updates.
    """
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()

    existing = table_manager.get_job_metadata(username, job_id)
    row = JobMetadataRow(
        username=username,
        job_id=job_id,
        status=status,
        bundle_fingerprint=(existing.get("bundle_fingerprint") if existing else None),
        force_refresh=force_refresh if not existing else bool(existing.get("force_refresh") or force_refresh),
        created_at=(existing.get("created_at") if existing else created_at) or created_at,
        updated_at=existing.get("updated_at") if existing else None,
        trace_id=trace_id if not existing else (existing.get("trace_id") or trace_id),
        request_id=request_id if not existing else (existing.get("request_id") or request_id),
    )
    table_manager.upsert_job_metadata(row)


def _identify_repo_freshness(username: str, trace: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Identify stale repositories by comparing fingerprints.
    
    Uses table_manager for metadata fingerprints (lightweight, normalized).
    No blob fetching - eliminates ~400KB transfer per freshness check.
    
    Raises:
        ValueError: If token is missing, invalid, or rate limit is exceeded.
    """
    repo_manager = _get_repo_manager(username)

    logger.info(
        "[API_GATEWAY_FRESHNESS] user=%s - Fetching all repos metadata from GitHub (1 API call)",
        username,
    )

    # Fetch current state from GitHub (unavoidable - freshness requires live data)
    all_repos = repo_manager.get_all_repos_metadata(username=username, include_languages=False)
    
    # Track API usage for freshness check
    api_usage = all_repos[0].get("api_usage") if all_repos and isinstance(all_repos, list) and isinstance(all_repos[0], dict) else {}
    
    current_fingerprints = {
        repo.get("name"): FingerprintManager.generate_metadata_fingerprint(repo)
        for repo in all_repos
        if repo.get("name")
    }

    # Query cached fingerprints from GitHub metadata table
    github_metadata_rows = table_manager.query_repo_github_metadata(username)
    cached_fingerprints = {
        row.get("repo_name"): row.get("fingerprint")
        for row in github_metadata_rows
        if row.get("repo_name") and row.get("fingerprint")
    }

    stale_repos: List[Dict[str, Any]] = []
    valid_repos: List[Dict[str, Any]] = []

    for repo_metadata in all_repos:
        repo_name = repo_metadata.get("name")
        if not repo_name:
            continue

        current_fingerprint = current_fingerprints.get(repo_name)
        cached_fingerprint = cached_fingerprints.get(repo_name)

        # Fingerprint mismatch = stale
        if current_fingerprint and current_fingerprint != cached_fingerprint:
            stale_repos.append({**repo_metadata, "fingerprint": current_fingerprint})
            logger.info(
                "[REPO_STALE] user=%s repo=%s cached_fp=%s current_fp=%s",
                username,
                repo_name,
                cached_fingerprint or "<none>",
                current_fingerprint[:8],
            )
        # Fingerprint match = valid (don't re-sync)
        elif current_fingerprint and current_fingerprint == cached_fingerprint:
            valid_repos.append(repo_metadata)
            logger.info(
                "[REPO_FRESH] user=%s repo=%s fp=%s",
                username,
                repo_name,
                current_fingerprint[:8],
            )
        # New repo (no cached fingerprint)
        else:
            stale_repos.append({**repo_metadata, "fingerprint": current_fingerprint})
            logger.info(
                "[REPO_NEW] user=%s repo=%s",
                username,
                repo_name,
            )  
    logger.info(
        "Repo freshness for user=%s: stale=%d, valid=%d",
        username,
        len(stale_repos),
        len(valid_repos),
    )
    
    # Record API usage if we have trace context
    if api_usage and trace:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        # Sanitize timestamp for Azure Table RowKey (no :, /, \, #, ?)
        safe_timestamp = now.replace(":", "-").replace("+", "_")
        operation_key = f"freshness_check|{safe_timestamp}|all_repos"
        
        totals = api_usage.get("totals", {})
        file_targets = api_usage.get("file_targets", {})
        cache_hits = sum(target.get("cache_hits", 0) for target in file_targets.values())
        
        row = RepoAPIUsageRow(
            username=username,
            operation_key=operation_key,
            operation="freshness_check",
            job_id=None,  # No job_id for freshness checks
            repo_name=None,  # User-level operation
            api_calls_rest=totals.get("requests", 0),
            api_calls_graphql=0,
            cache_hits=cache_hits,
            created_at=safe_timestamp,  # Use sanitized timestamp
        )
        table_manager.upsert_api_usage(row)
    
    return {
        "stale_repos": stale_repos,
        "cached_bundle": valid_repos,
        "bundle_status": "fresh" if not stale_repos else "stale",
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    status = {
        "status": "ok",
        "cache": cache_manager.use_cache,
        "version": os.getenv("BUILD_BUILDNUMBER", "dev"),
    }
    return _create_success_response(status, cache_control="no-cache")


@bp.route(route="candidate/{username}/{repo}/cache-status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_cache_status(req: func.HttpRequest) -> func.HttpResponse:
    """Check cache status for a repository and enqueue job if needed."""
    username = req.route_params.get("username")
    repo = req.route_params.get("repo")
    if not username or not repo:
        return _create_error_response("Username and repository name are required", 400)
    
    trace = _get_trace_context(req)
    job_id = req.params.get("job_id")
    
    logger.info(
        "[CACHE_STATUS_REQUEST] request_id=%s username=%s repo=%s job_id=%s",
        trace["request_id"],
        username,
        repo,
        job_id or "auto",
    )
    
    status_result = _check_repo_cache_status(
        username,
        repo,
        job_id=job_id,
        trace_id=trace.get("trace_id"),
    )
    
    logger.info(
        "[CACHE_STATUS_RESPONSE] request_id=%s username=%s repo=%s status=%s",
        trace["request_id"],
        username,
        repo,
        status_result.get("status"),
    )
    
    return _create_success_response(status_result, cache_control="no-cache")


@bp.route(route="candidate/{username}/{repo}/readme-summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_readme_summary(req: func.HttpRequest) -> func.HttpResponse:
    """Generate an AI summary for a repository README (HTML output)."""
    username = req.route_params.get("username")
    repo = req.route_params.get("repo")
    if not username or not repo:
        return _create_error_response("Username and repository name are required", 400)
    trace = _get_trace_context(req)
    logger.info(
        "[REPO_README_SUMMARY_REQUEST] request_id=%s username=%s repo=%s",
        trace["request_id"],
        username,
        repo,
    )

    # Check cache status first
    status_result = _check_repo_cache_status(
        username,
        repo,
        trace_id=trace.get("trace_id"),
    )
    
    if status_result.get("status") != "cached":
        logger.info(
            "[REPO_README_SUMMARY_PENDING] request_id=%s username=%s repo=%s status=%s",
            trace["request_id"],
            username,
            repo,
            status_result.get("status"),
        )
        return _create_error_response(
            status_result.get("message", "Cache not ready"),
            202,  # Accepted - processing
            error_code="CACHE_NOT_READY",
            request_id=trace["request_id"],
        )
    else:
        logger.info(
            "[REPO_README_SUMMARY_CACHED] request_id=%s username=%s repo=%s - Cache is ready, status=%s",
            trace["request_id"],
            username,
            repo,
            status_result.get("status"),
        )
    
    # Cache is ready - retrieve and generate summary
    try:
        files_payload = _get_repo_file_content(username, repo, file_type="readme")
        primary_readme = files_payload.get("primary_readme") or ""
    except ValueError as exc:
        return _create_error_response(str(exc), 404, request_id=trace["request_id"])

    if not primary_readme:
        return _create_error_response("No README content available", 404, request_id=trace["request_id"])

    assistant = AIAssistant(username=username)
    summary_html = assistant.summarize_readme_html(primary_readme, repo_name=repo)

    detail = _build_repo_detail_entry(username, repo, job_id=status_result.get("job_id"))
    payload = {
        "username": username,
        "repo": repo,
        "job_id": detail.get("job_id"),
        "repo_entry": detail.get("repo_entry"),
        "readme_summary_html": summary_html,
    }

    logger.info(
        "[REPO_README_SUMMARY_RESPONSE] request_id=%s username=%s repo=%s status=success",
        trace["request_id"],
        username,
        repo,
    )

    return _create_success_response(payload, cache_control="no-cache")


@bp.route(route="candidate/{username}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_candidate_repos_metadata(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve candidate portfolio metadata.
    
    Returns repository metadata for a GitHub username. If no job_id is provided,
    returns the latest completed job. Queries normalized tables for real-time data.
    
    Query parameters:
        job_id (optional): Specific job ID to retrieve
    
    Returns:
        200: Candidate metadata with repository list
        404: No job found or job still in progress
        400: Invalid request (missing username)
    
    Response structure:
        {
            "username": str,
            "job_id": str,
            "fingerprint": str,
            "status": str,
            "data": [repo_metadata...]
        }
    """
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400, error_code="VALIDATION_ERROR")
    
    job_id = req.params.get("job_id")

    trace = _get_trace_context(req)
    logger.info(
        "[CANDIDATE_REQUEST] request_id=%s username=%s",
        trace["request_id"],
        username,
    )
    candidate_job = _fetch_candidate_jobs(username, job_id=job_id)
    if candidate_job is None:
        return _create_error_response("No job found for user", 404, error_code="NOT_FOUND", request_id=trace.get("request_id"))
    job_id = candidate_job.get("job_id")

    # Try table storage first (most recent data)
    if job_id:
        repo_rows = _query_repo_rows(username, job_id=job_id)
        if repo_rows:
            logger.info(
                "[CANDIDATE_RESPONSE] request_id=%s username=%s job_id=%s source=table repos=%d",
                trace["request_id"],
                username,
                job_id,
                len(repo_rows),
            )
            payload = _get_candidate_metadata(username, candidate_job, repo_rows)
            return _create_success_response(payload, request_id=trace.get("request_id"))


        # Job exists but no repo data yet
        return _create_error_response(
            f"Candidate for '{username}' not ready (job in progress)",
            404,
            error_code="NOT_READY",
            request_id=trace.get("request_id"),
        )

    # No job found for username
    return _create_error_response(
        f"No candidate found for '{username}'. Trigger refresh first.",
        404,
        error_code="NOT_FOUND",
        request_id=trace.get("request_id"),
    )


@bp.route(route="candidate/{username}/profile", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_candidate_profile(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve aggregated candidate profile data.

    Combines GitHub user profile (cached in tables), latest job metadata,
    and repo statistics from normalized tables.
    """
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, status_code=400, error_code="BAD_REQUEST")

    trace = _get_trace_context(req)
    job_id = req.params.get("job_id")
    job = _fetch_candidate_jobs(username, job_id=job_id)
    resolved_job_id = job.get("job_id") if job else None

    _record_user_session(trace, username, resolved_job_id)

    repo_rows = _query_repo_rows(username, job_id=resolved_job_id)
    languages_by_repo = table_manager.query_repo_languages(resolved_job_id) if resolved_job_id else {}

    profile = _get_or_refresh_user_profile(username)
    statistics = _build_profile_statistics(repo_rows, languages_by_repo)

    payload = {
        "username": username,
        "github_profile": profile,
        "job_metadata": job,
        "statistics": statistics,
    }
    return _create_success_response(payload, cache_control="no-cache", request_id=trace.get("request_id"))


@bp.route(route="candidate/{username}/summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_candidate_summary(req: func.HttpRequest) -> func.HttpResponse:
    """Generate an AI summary for a candidate profile (HTML output)."""
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, status_code=400, error_code="BAD_REQUEST")

    trace = _get_trace_context(req)
    job_id = req.params.get("job_id")
    job = _fetch_candidate_jobs(username, job_id=job_id)
    resolved_job_id = job.get("job_id") if job else None

    _record_user_session(trace, username, resolved_job_id)

    repo_rows = _query_repo_rows(username, job_id=resolved_job_id)
    languages_by_repo = table_manager.query_repo_languages(resolved_job_id) if resolved_job_id else {}

    profile = _get_or_refresh_user_profile(username)
    statistics = _build_profile_statistics(repo_rows, languages_by_repo)
    summary_payload = _build_profile_summary_payload(
        username=username,
        profile=profile,
        job=job,
        repo_rows=repo_rows,
        languages_by_repo=languages_by_repo,
        statistics=statistics,
    )

    assistant = AIAssistant(username=username)
    summary_html = assistant.summarize_profile_html(summary_payload, username=username)

    payload = {
        "username": username,
        "job_id": resolved_job_id,
        "summary_html": summary_html,
    }
    return _create_success_response(payload, cache_control="no-cache", request_id=trace.get("request_id"))


@bp.route(route="candidate/{username}/refresh", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def trigger_candidate_refresh(req: func.HttpRequest) -> func.HttpResponse:
    """Trigger refresh of candidate portfolio data.
    
    Analyzes repository freshness by comparing GitHub metadata fingerprints with
    cached data. Enqueues sync jobs only for stale repositories unless force_refresh
    is specified.
    
    Request body:
        {
            "force_refresh": bool (default: false)
        }
    
    Behavior:
        - force_refresh=false: Only syncs repositories with changed metadata
        - force_refresh=true: Syncs all repositories regardless of freshness
        - Returns early if no stale repos and not forcing refresh
    
    Returns:
        202: Refresh job started successfully
            Response: {
                "status": "processing",
                "job_id": str,
                "repos_queued": int,
                "status_url": str
            }
        200: No refresh needed (all repos fresh)
            Response: {
                "status": "fresh",
                "repos_count": int
            }
        400: Invalid request (missing username)
        500: Failed to analyze repository freshness
        502: Failed to enqueue sync jobs
    
    Flow:
        1. Fetch current GitHub metadata (unavoidable for freshness check)
        2. Compare fingerprints with table_manager cached data
        3. Create job metadata and RepoSyncStatus rows (audit trail)
        4. Enqueue sync jobs for stale/all repos
        5. Return job_id and status polling URL
    """
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)

    trace = _get_trace_context(req)
    body = _parse_json(req)
    force_refresh = bool(body.get("force_refresh", False))

    logger.info(
        "[REFRESH_REQUEST] request_id=%s session_id=%s username=%s force_refresh=%s",
        trace["request_id"],
        trace["session_id"],
        username,
        force_refresh,
    )

    try:
        freshness = _identify_repo_freshness(username, trace=trace)
    except Exception as exc:
        logger.error("Failed to analyze repo freshness: %s", exc, exc_info=True)
        return _create_error_response("Failed to analyze repositories", 500)

    stale_repos = freshness["stale_repos"]
    valid_repos = freshness["cached_bundle"]  # Valid (non-stale) repos from cache

    # If nothing is stale and not forcing refresh, return early
    if not stale_repos and not force_refresh:
        logger.info(
            "[REFRESH_RESPONSE] request_id=%s username=%s status=fresh repos=%d",
            trace["request_id"],
            username,
            len(valid_repos),
        )
        return _create_success_response({"status": "fresh", "repos_count": len(valid_repos)})

    # Determine repos to queue: stale only, or stale + valid if force_refresh
    repos_to_queue = stale_repos + valid_repos if force_refresh else stale_repos
    if not repos_to_queue:
        return _create_success_response({"status": "fresh", "repos_count": 0})

    job_id = str(uuid.uuid4())
    expected_repo_names = [repo.get("name") for repo in repos_to_queue if repo.get("name")]

    logger.info(
        "[REFRESH_JOB_CREATED] request_id=%s username=%s job_id=%s stale=%d valid=%d total_to_queue=%d",
        trace["request_id"],
        username,
        job_id,
        len(stale_repos),
        len(valid_repos),
        len(expected_repo_names),
    )

    _persist_job_metadata(
        job_id,
        username,
        force_refresh=force_refresh,
        trace_id=trace.get("trace_id"),
        request_id=trace.get("request_id"),
    )

    # Create pending status rows for all repos upfront (before enqueuing)
    # This provides full audit trail: pending → synced → cached
    for repo_metadata in repos_to_queue:
        repo_name = repo_metadata.get("name")
        if repo_name:
            table_manager.upsert_repo_status(
                RepoSyncStatusRow(
                    job_id=job_id,
                    repo_name=repo_name,
                    username=username,
                    status="pending",
                )
            )
    logger.info(
        "[REFRESH_PENDING_CREATED] job=%s user=%s repos=%d",
        job_id,
        username,
        len(expected_repo_names),
    )

    enqueued = 0
    for repo_metadata in repos_to_queue:
        repo_name = repo_metadata.get("name")
        if not repo_name:
            continue

        repo_fingerprint = repo_metadata.get("fingerprint")
        if queue_manager.enqueue_sync_job(
            job_id,
            username,
            repo_name,
            repo_fingerprint,
            trace_id=trace.get("trace_id"),
            request_id=trace.get("request_id"),
            session_id=trace.get("session_id"),
        ):
            enqueued += 1

    logger.info(
        "[REFRESH_ENQUEUED] request_id=%s username=%s job_id=%s enqueued=%d/%d",
        trace["request_id"],
        username,
        job_id,
        enqueued,
        len(expected_repo_names),
    )

    if enqueued == 0:
        return _create_error_response("Failed to enqueue sync jobs", 502)

    response = {
        "status": "processing",
        "job_id": job_id,
        "repos_queued": enqueued,
        "status_url": f"/api/candidate/{username}/status?job_id={job_id}",
    }
    return _create_success_response(response, status_code=202, cache_control="no-cache")


@bp.route(route="candidate/{username}/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    """Poll job progress and status.
    
    Returns real-time progress tracking for a specific job. Uses job-level status
    from JobMetadata table with detailed repo-level progress from RepoSyncStatus.
    
    Query parameters:
        job_id (required): Job ID to check status for
    
    Returns:
        200: Job status retrieved successfully
        404: Job not found
        400: Missing required parameters
    
    Response structure:
        {
            "job_id": str,
            "username": str,
            "status": str,  // "queued" | "syncing" | "metadata_ready" | "completed" | "failed"
            "metadata_ready": bool,  // True when first repo cached (can display metadata)
            "files_ready": bool,     // True when all files cached (can display README)
            "progress": {
                "total": int,
                "completed": int,       // cached + failed
                "percentage": int,
                "pending": int,         // Waiting to sync
                "synced": int,          // Metadata synced, files pending
                "cached": int,          // Files cached (ready)
                "failed": int           // Terminal failure state
            },
            "created_at": str,
            "repo_details": {
                "pending": [str...],    // First 10 repos
                "synced": [str...],
                "cached": [str...],
                "failed": [str...]
            }
        }
    
    Status progression:
        queued → syncing → metadata_ready → completed
               ↘ failed (terminal)
    
    Cache-Control: no-cache (always fetch fresh status)
    """
    username = req.route_params.get("username")
    job_id = req.params.get("job_id")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)
    if not job_id:
        return _create_error_response("job_id query parameter required", 400)

    trace = _get_trace_context(req)
    logger.info(
        "[STATUS_REQUEST] request_id=%s session_id=%s username=%s job_id=%s",
        trace["request_id"],
        trace["session_id"],
        username,
        job_id,
    )

    job = table_manager.get_job_metadata(username, job_id)
    if not job:
        return _create_error_response("Job not found", 404)
    
    job_status = job.get("status", "unknown")
    
    # Compute detailed progress from RepoSyncStatus (for UI progress bars)
    statuses = table_manager.list_repo_statuses(job_id)
    total = len(statuses)
    
    # Single pass to collect repo names by status
    status_counts = defaultdict(int)
    status_lists = defaultdict(list)
    
    for row in statuses:
        status = row.get("status")
        repo_name = row.get("repo_name")
        if status in ("pending", "synced", "cached", "failed") and repo_name:
            status_counts[status] += 1
            status_lists[status].append(repo_name)
    
    pending = status_lists["pending"]
    synced = status_lists["synced"]
    cached = status_lists["cached"]
    failed = status_lists["failed"]
    
    # Completed = cached files + failed (terminal states)
    completed = len(cached) + len(failed)
    
    payload = {
        "job_id": job_id,
        "username": username,
        "status": job_status,
        "metadata_ready": job_status in ("metadata_ready", "completed"),
        "files_ready": job_status == "completed",
        "progress": {
            "total": total,
            "completed": completed,
            "percentage": int((completed / total * 100) if total else 0),
            "pending": len(pending),
            "synced": len(synced),  # Metadata synced, files pending
            "cached": len(cached),  # Files cached (ready)
            "failed": len(failed),
        },
        "created_at": job.get("created_at"),
        "repo_details": {
            "pending": pending[:10],  # First 10 for UI
            "synced": synced[:10],
            "cached": cached[:10],
            "failed": failed[:10],
        },
    }
    logger.info(
        "[STATUS_RESPONSE] request_id=%s job=%s status=%s metadata_ready=%s files_ready=%s total=%d cached=%d synced=%d pending=%d failed=%d",
        trace["request_id"],
        job_id,
        payload["status"],
        payload["metadata_ready"],
        payload["files_ready"],
        total,
        len(cached),
        len(synced),
        len(pending),
        len(failed),
    )
    return _create_success_response(payload, cache_control="no-cache")


@bp.route(route="session/bundle/{username}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_session_bundle(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve bundle for username with session tracking.

    This endpoint combines bundle retrieval with session candidate tracking,
    useful for UI contexts where browsing history should be recorded.
    """
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)

    trace = _get_trace_context(req)
    session_id = trace.get("session_id")
    if not session_id:
        return _create_error_response("X-Session-Id header required for session tracking", 400)

    requested_job_id = req.params.get("job_id")
    logger.info(
        "[SESSION_BUNDLE_REQUEST] request_id=%s session_id=%s username=%s job_id=%s",
        trace["request_id"],
        session_id,
        username,
        requested_job_id or "<latest>",
    )

    session = _fetch_candidate_jobs(username, requested_job_id)
    resolved_job_id = session.get("job_id") if session else requested_job_id

    # Record session tracking
    _record_user_session(trace, username, resolved_job_id)

    repo_rows = _query_repo_rows(username, job_id=resolved_job_id)

    if session and repo_rows:
        logger.info(
            "[SESSION_BUNDLE_RESPONSE] request_id=%s session_id=%s username=%s job_id=%s source=table repos=%d",
            trace["request_id"],
            session_id,
            username,
            resolved_job_id or "<unknown>",
            len(repo_rows),
        )
        payload = _get_candidate_metadata(username, session, repo_rows)
        return _create_success_response(payload)

    cache_payload = _bundle_from_cache(username)
    if cache_payload:
        logger.info(
            "[SESSION_BUNDLE_RESPONSE] request_id=%s session_id=%s username=%s source=cache repos=%d",
            trace["request_id"],
            session_id,
            username,
            len(cache_payload.get("data") or []),
        )
        return _create_success_response(cache_payload)

    if session:
        return _create_error_response(f"Bundle for '{username}' not ready", 404)

    return _create_error_response(f"No valid bundle found for '{username}'", 404)


@bp.route(route="session/candidates", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_session_candidates(req: func.HttpRequest) -> func.HttpResponse:
    """List usernames recently viewed in this session.

    Returns browsing history for the session, useful for 'recently viewed' UI features.
    """
    trace = _get_trace_context(req)
    session_id = trace.get("session_id")
    if not session_id:
        return _create_error_response("X-Session-Id required", 400)

    limit = 10
    limit_param = req.params.get("limit")
    if limit_param:
        try:
            limit = max(1, min(50, int(limit_param)))
        except (TypeError, ValueError):
            limit = 10

    candidates = table_manager.list_session_candidates(session_id, limit=limit)
    payload = {
        "candidates": [
            {
                "username": row.get("username"),
                "latest_job_id": row.get("latest_job_id"),
                "last_viewed_at": row.get("last_viewed_at"),
                "query_count": row.get("query_count"),
            }
            for row in candidates
            if row.get("username")
        ]
    }
    return _create_success_response(payload, cache_control="no-cache")


@bp.route(route="ai", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def portfolio_query(req: func.HttpRequest) -> func.HttpResponse:
    body = _parse_json(req)
    query = body.get("query")
    username = body.get("username")
    if not query or not username:
        return _create_error_response("Request body must contain 'query' and 'username'", 400)

    repos_bundle = None
    session = _fetch_candidate_jobs(username)
    job_id = session.get("job_id") if session else None
    repo_rows = _query_repo_rows(username, job_id=job_id) if session else []
    if session and repo_rows:
        table_bundle = _get_candidate_metadata(username, session, repo_rows)
        repos_bundle = table_bundle.get("data")

    if not repos_bundle:
        return _create_error_response("No repository bundle available. Trigger refresh first.", 400)

    include_readme_summary = bool(body.get("include_readme_summary"))
    repo_name = body.get("repo_name")
    readme_summary_html = None
    if include_readme_summary and repo_name:
        try:
            files_payload = _get_repo_file_content(username, repo_name, file_type="readme")
            primary_readme = files_payload.get("primary_readme") or ""
            if primary_readme:
                assistant = AIAssistant(username=username)
                readme_summary_html = assistant.summarize_readme_html(primary_readme, repo_name=repo_name)
        except Exception:
            readme_summary_html = None

    try:
        scoring_service = RepoScoringService(username=username)
        scored = scoring_service.score_repositories(query, repos_bundle)
        assistant = AIAssistant(username=username)
        response = assistant.process_scored_repositories(query, scored)
        if readme_summary_html:
            response["readme_summary_html"] = readme_summary_html
        return _create_success_response(response)
    except Exception as exc:
        logger.error("AI query failed: %s", exc, exc_info=True)
        return _create_error_response("Failed to process query", 500)


