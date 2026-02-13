"""API Gateway blueprint for Cloudfolio.

Ported from `api/v0.3.0/api-gateway/function_app.py`.
"""

from __future__ import annotations

import json
import logging
import os
import random
import statistics
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import azure.functions as func

from foliohive_shared import (
    cache_manager,
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    queue_manager,
    table_manager,
    SummaryManager,
    get_file_budget,
    ApiUsageTracker,
    JobMetadataRow,
    RepoSyncStatusRow,
    RepoAPIUsageRow,
    UserProfileRow,
)

try:  # Azure SDK may be unavailable in local dev; ignore import failures gracefully
    from azure.core.exceptions import ResourceNotFoundError
except Exception:  # pragma: no cover - falls back in environments without azure core
    ResourceNotFoundError = None


@dataclass
class CandidateContext:
    """Context data for candidate endpoint operations.
    
    Encapsulates trace context, job metadata, and session tracking state
    to standardize data flow across endpoints.
    """
    trace: Dict[str, str]
    job: Optional[Dict[str, Any]]
    job_id: Optional[str]
    username: str


logger = logging.getLogger("cloudfolio.api_gateway")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

USERNAME_REQUIRED_MESSAGE = "Username required"
PROFILE_TTL_SECONDS = int(os.getenv("CF_PROFILE_TTL_SECONDS", "21600"))

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


def _select_repos_for_context(
    repo_rows: List[Dict[str, Any]],
    strategy: str = "recent",
    max_repos: int = 8,
) -> List[Dict[str, Any]]:
    """Select repos based on strategy for early context building.
    
    Centralizes repo selection to avoid redundant file fetching and duplication.
    Used in endpoints to select which repos to fetch file contents for.
    
    Args:
        repo_rows: List of repository metadata rows
        strategy: Selection strategy (recent, random, top_starred)
        max_repos: Maximum number of repos to select
    
    Returns:
        Selected repo rows (filtered and sorted per strategy)
    """
    
    if not repo_rows:
        return []
    
    if strategy == "recent":
        # Sort by last updated (most recent first)
        def get_update_time(repo: Dict[str, Any]) -> datetime:
            updated = repo.get("github_updated_at") or repo.get("updated_at", "")
            if not updated:
                return datetime.min
            try:
                # Handle ISO format with +00:00 or Z
                updated = updated.replace("+00:00", "").replace("Z", "")
                return datetime.fromisoformat(updated)
            except Exception:
                return datetime.min
        
        sorted_repos = sorted(repo_rows, key=get_update_time, reverse=True)
        return sorted_repos[:max_repos]
    
    elif strategy == "random":
        # Random sample for diversity
        if len(repo_rows) <= max_repos:
            return repo_rows
        return random.sample(repo_rows, max_repos)
    
    elif strategy == "top_starred":
        # Sort by stars (most starred first)
        sorted_repos = sorted(
            repo_rows,
            key=lambda r: r.get("stars_count", 0),
            reverse=True
        )
        return sorted_repos[:max_repos]
    
    else:
        # Default to recent
        logger.warning("Unknown selection strategy '%s', defaulting to 'recent'", strategy)
        return _select_repos_for_context(repo_rows, "recent", max_repos)
    

def _is_profile_fresh(profile: Dict[str, Any], ttl_seconds: int) -> bool:
    cached_at = profile.get("cached_at")
    if not cached_at:
        return False
    cached_dt = _parse_iso(cached_at)
    if cached_dt == datetime.min.replace(tzinfo=timezone.utc):
        return False
    return (datetime.now(timezone.utc) - cached_dt).total_seconds() < ttl_seconds

def _refresh_user_profile(username: str, cached_profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    repo_manager = _get_repo_manager(username)
    usage_tracker = ApiUsageTracker(owner=username, repo="user_profile")
    profile = repo_manager.get_user_profile(username=username, usage=usage_tracker)
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
    
    # Record API usage for profile refresh (1 REST API call)
    _record_api_usage(
        username=username,
        operation="profile_refresh",
        api_usage_dict=usage_tracker.to_dict(),
        job_id=None,
        repo_name=None,
    )
    
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

def _prepare_candidate_context(
    req: func.HttpRequest,
    username: str,
    *,
    job_id: Optional[str] = None,
    record_session: bool = True,
) -> CandidateContext:
    """Prepare candidate context with trace, job metadata, and session tracking.
    
    Standardizes the common pattern of:
    1. Extract trace context from request
    2. Fetch/resolve job metadata
    3. Record session tracking
    
    Args:
        req: HTTP request object
        username: GitHub username
        job_id: Optional specific job ID (None = fetch latest)
        record_session: Whether to record session tracking (default: True)
    
    Returns:
        CandidateContext with trace, job, job_id, and username
    """
    trace = _get_trace_context(req)
    session_id = trace.get("session_id") if trace else ""
    if not session_id or not username:
        return
    
    # Resolve job_id from request params if not provided
    resolved_job_id = job_id or req.params.get("job_id")
    job = _fetch_candidate_jobs(username, job_id=resolved_job_id)
    final_job_id = job.get("job_id") if job else None
    
    # Record session tracking if enabled
    if record_session:
        table_manager.upsert_session_candidate(session_id, username, final_job_id)
    
    return CandidateContext(
        trace=trace,
        job=job,
        job_id=final_job_id,
        username=username,
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


def _get_repo_files(
    username: str,
    selected_repos: List[Dict[str, Any]],
    job_id: Optional[str] = None,
    max_additional_readmes: int = 3,
    max_config_files: int = 3,
    include_readme: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Get cached file contents for selected repos with configurable limits.
    
    Consolidates fetching and limiting of cached file contents across repos.
    Retrieves discovered paths from table storage and uses RepoCacheRetrieval
    for pre-cached file categorization. Discovery and caching happens 
    asynchronously in cache_worker.py.
    
    Args:
        username: GitHub username
        selected_repos: List of repo metadata dicts (already filtered by strategy)
        job_id: Optional job ID to retrieve discovered paths from table storage
        max_additional_readmes: Max additional readme files per repo (default: 3)
        max_config_files: Max config files per repo (default: 3)
        include_readme: Whether to include primary readme content (default: True)
    
    Returns:
        Dict mapping repo_name -> {readme_content, readme_files, config_files}
    """
    repo_files = {}
    
    for repo in selected_repos:
        repo_name = repo.get("repo_name")
        if not repo_name:
            continue
        
        try:
            # Retrieve discovered paths from table storage if job_id provided
            discovered_paths = None
            if job_id:
                try:
                    paths_row = table_manager.get_repo_discovered_paths(job_id, repo_name)
                    if paths_row:
                        discovered_paths = paths_row.get("discovered_paths")
                        logger.info(
                            "Retrieved discovered paths for %s/%s job=%s: %d paths",
                            username, repo_name, job_id, len(discovered_paths or [])
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to retrieve discovered paths for %s/%s job=%s: %s",
                        username, repo_name, job_id, exc
                    )
            
            # Retrieve all cached files with limits
            files = cache_manager.get_repo_files(
                username=username,
                repo=repo_name,
                discovered_paths=discovered_paths,
                readme_candidates=["README.md", "README.rst", "README.txt", "readme.md"],
                max_readme_files=max_additional_readmes,
                max_config_files=max_config_files,
                include_readme=include_readme,
            )
            
            logger.info(
                "Retrieved cached files for %s/%s: readme=%s, readme_files=%d, config_count=%d",
                username,
                repo_name,
                "yes" if files.get("readme_content") else "no",
                len(files.get("readme_files", {})),
                len(files.get("config_files", {})),
            )
            
            repo_files[repo_name] = {
                "readme_content": files.get("readme_content"),
                "readme_files": files.get("readme_files", {}),
                "config_files": files.get("config_files", {})
            }
        
        except Exception as exc:
            logger.warning(
                "Failed to retrieve cached files for %s/%s: %s",
                username, repo_name, exc
            )
            repo_files[repo_name] = {
                "readme_content": None,
                "readme_files": {},
                "config_files": {}
            }
    
    return repo_files
    

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
            status_rows = table_manager.list_repo_statuses(job_id)
            target_repo_names = [
                row["repo_name"] for row in status_rows
                if row.get("status") in ("synced", "cached") and row.get("repo_name")
            ]
        
        if target_repo_names:
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


def _build_repo_statistics(
    languages: Optional[List[Dict[str, Any]]] = None,
    github_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert repo metadata to bundle entry.
    
    """
    entry: Dict[str, Any] = {}

    if languages:
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


def _build_repo_detail_entry(
    repo_names: Union[str, List[str]],
    *,
    ctx: CandidateContext,
) -> Dict[str, Any]:
    """Build one or many repo detail entries using normalized tables.
    
    Consolidates metadata and language lookups to avoid redundant queries when
    multiple repos are requested. Callers needing a single repo can pass a
    string; batch callers can pass a list of repo names.

    Args:
        repo_names: Repository name or list of names
        ctx: Candidate context with resolved job_id and username
    
    Returns:
        Dict with job_id, repo_entry (first), and entries (all repos processed)
    """
    # Normalize input to a list while preserving order
    target_names: List[str] = []
    if isinstance(repo_names, str) and repo_names:
        target_names = [repo_names]
    elif isinstance(repo_names, list):
        target_names = [name for name in repo_names if name]

    repo_rows: List[Dict[str, Any]] = []
    if target_names:
        repo_rows = _query_repo_rows(ctx.username, job_id=ctx.job_id, repo_names=target_names)
    elif ctx.job_id:
        repo_rows = _query_repo_rows(ctx.username, job_id=ctx.job_id)
        target_names = [row.get("repo_name") for row in repo_rows if row.get("repo_name")]

    if not target_names:
        return {"job_id": ctx.job_id, "repo_entry": {}, "entries": []}

    # Single query for metadata and languages (if job-scoped)
    languages_by_repo = table_manager.query_repo_languages(ctx.job_id) if ctx.job_id else {}

    entries: List[Dict[str, Any]] = []
    for name in target_names:
        github_metadata = next((row for row in repo_rows if row.get("repo_name") == name), None)
        languages = languages_by_repo.get(name)

        entry = _build_repo_statistics(languages=languages, github_metadata=github_metadata)
        if not entry.get("name"):
            entry["name"] = name
        entries.append(entry)

    primary_entry = entries[0] if entries else {}

    return {
        "job_id": ctx.job_id,
        "repo_entry": primary_entry,
        "entries": entries,
    }


def _aggregate_portfolio_statistics(
    repo_rows: List[Dict[str, Any]],
    languages_by_repo: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Aggregate statistics across all repositories.
    
    Computes portfolio-wide metrics by aggregating data from multiple repositories.
    This is distinct from _build_repo_statistics which handles single-repo transformations.
    
    Args:
        repo_rows: List of GitHub metadata dicts from RepoGitHubMetadata table
        languages_by_repo: Dict mapping repo_name to list of language dicts
    
    Returns:
        Dict with aggregated statistics: repo_count, stars_total, forks_total, 
        top_languages, topics, repo_names
    """
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

    # Calculate total bytes across all languages for percentage calculation
    total_all_bytes = sum(language_totals.values())
    
    top_languages = [
        {
            "language": name, 
            "bytes": bytes_count,
            "percentage": round((bytes_count / total_all_bytes * 100), 2) if total_all_bytes > 0 else 0.0
        }
        for name, bytes_count in sorted(language_totals.items(), key=lambda item: item[1], reverse=True)
    ]

    topics: List[str] = []
    for row in repo_rows:
        row_topics = row.get("topics") or []
        if isinstance(row_topics, list):
            topics.extend([topic for topic in row_topics if isinstance(topic, str) and topic])
    unique_topics = sorted(set(topics))

    return {
        "repo_names": [row.get("repo_name") for row in repo_rows if row.get("repo_name")],
        "repo_count": repo_count,
        "stars_total": stars_total,
        "forks_total": forks_total,
        "top_languages": top_languages,
        "topics": unique_topics,
    }


def _get_portfolio_bundle(
    username: str,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified portfolio data fetcher - single source of truth for portfolio queries.
    
    Consolidates the common pattern of querying repo metadata, languages, and 
    computing aggregated statistics. Used by get_profile, get_profile_summary, 
    and portfolio_query to eliminate redundant database queries.
    
    Args:
        username: GitHub username
        job_id: Optional job ID to query specific job data
    
    Returns:
        Dict containing:
            - repo_rows: List of GitHub metadata dicts
            - languages_by_repo: Dict mapping repo_name to language data
            - statistics: Aggregated portfolio statistics
    """
    repo_rows = _query_repo_rows(username, job_id=job_id)
    languages_by_repo = table_manager.query_repo_languages(job_id) if job_id else {}
    statistics = _aggregate_portfolio_statistics(repo_rows, languages_by_repo)
    
    return {
        "repo_rows": repo_rows,
        "languages_by_repo": languages_by_repo,
        "statistics": statistics,
    }


def _compute_repo_status_summary(job_id: str) -> Dict[str, Any]:
    """Compute repository status summary for a job.
    
    Consolidates duplicate status counting logic previously in get_job_status
    and _get_candidate_metadata. Single-pass computation of status counts and
    repo lists by status.
    
    Args:
        job_id: Job ID to compute status for
    
    Returns:
        Dict containing:
            - total: Total number of repos in job
            - counts: Dict mapping status names to counts
            - lists: Dict mapping status names to repo name lists
            - completed: Number of repos in terminal states (cached + failed)
            - percentage: Completion percentage (0-100)
    """
    statuses = table_manager.list_repo_statuses(job_id)
    total = len(statuses)
    
    status_counts = defaultdict(int)
    status_lists = defaultdict(list)
    
    for row in statuses:
        status = row.get("status")
        repo_name = row.get("repo_name")
        if status in ("pending", "synced", "cached", "failed") and repo_name:
            status_counts[status] += 1
            status_lists[status].append(repo_name)
    
    # Completed = cached files + failed (terminal states)
    completed = status_counts["cached"] + status_counts["failed"]
    percentage = int((completed / total * 100) if total else 0)
    
    return {
        "total": total,
        "counts": dict(status_counts),
        "lists": dict(status_lists),
        "completed": completed,
        "percentage": percentage,
    }


def _get_candidate_metadata(ctx: CandidateContext, job: Dict[str, Any]) -> Dict[str, Any]:
    """Build bundle from table data with status summary.
    
    Args:
        ctx: Candidate context with username and job_id
        job: Job metadata dict
    
    Returns:
        Dict with username, job_id, data array, and status_summary for progress tracking
    """
    job = job or {}
    job_id = job.get("job_id") or ctx.job_id or None
    username = ctx.username

    # Compute status summary using shared function
    status_summary = None
    if job_id:
        status_summary = _compute_repo_status_summary(job_id)
        counts = status_summary["counts"]
        
        # Log progress if any repos are not completed
        if counts.get("failed", 0) or counts.get("pending", 0) or counts.get("synced", 0):
            logger.info(
                "[CANDIDATE_PROGRESS] job=%s user=%s cached=%d synced=%d pending=%d failed=%d",
                job_id,
                username,
                counts.get("cached", 0),
                counts.get("synced", 0),
                counts.get("pending", 0),
                counts.get("failed", 0),
            )

    # Reuse consolidated detail builder to avoid duplicate queries
    detail_bundle = _build_repo_detail_entry([], ctx=ctx)
    entries = detail_bundle.get("entries", [])

    result = {
        "username": username,
        "job_id": job_id,
        "fingerprint": job.get("bundle_fingerprint"),
        "last_modified": _restore_iso_timestamp(job.get("updated_at")) or job.get("updated_at"),
        "status": job.get("status"),
        "data": entries,
    }
    
    # Include status summary if available (for progress tracking without separate polling)
    if status_summary:
        result["status_summary"] = {
            "total": status_summary["total"],
            "completed": status_summary["completed"],
            "percentage": status_summary["percentage"],
            "pending": status_summary["counts"].get("pending", 0),
            "synced": status_summary["counts"].get("synced", 0),
            "cached": status_summary["counts"].get("cached", 0),
            "failed": status_summary["counts"].get("failed", 0),
        }

    return result

def _record_api_usage(
    username: str,
    operation: str,
    api_usage_dict: Dict[str, Any],
    *,
    job_id: Optional[str] = None,
    repo_name: Optional[str] = None,
) -> None:
    """Record API usage for GitHub operations.
    
    Generic helper to record API calls, cache hits, and rate limit info
    for observability and cost analysis across all operations.
    
    Args:
        username: GitHub username (PartitionKey)
        operation: Operation type (e.g., "freshness_check", "metadata_sync", "file_cache", "profile_refresh")
        api_usage_dict: Dict with "totals" and optional "file_targets" keys
        job_id: Optional job ID (None for user-level operations like freshness/profile)
        repo_name: Optional repo name (None for user-level operations)
    """
    # Handle None or empty api_usage_dict gracefully
    if not api_usage_dict:
        api_usage_dict = {"totals": {"requests": 0}, "file_targets": {}}

    totals = api_usage_dict.get("totals", {})
    file_targets = api_usage_dict.get("file_targets", {})
    cache_hits = sum(target.get("cache_hits", 0) for target in file_targets.values())

    now = datetime.now(timezone.utc).isoformat()
    # Sanitize timestamp for Azure Table RowKey (no :, /, \, #, ?)
    safe_timestamp = now.replace(":", "-").replace("+", "_")

    # Build composite RowKey: operation|timestamp|repo_name (or all_repos/user for user-level ops)
    row_key_part = repo_name or "all_repos" if operation == "freshness_check" else repo_name or operation
    operation_key = f"{operation}|{safe_timestamp}|{row_key_part}"

    row = RepoAPIUsageRow(
        username=username,
        operation_key=operation_key,
        operation=operation,
        job_id=job_id,
        repo_name=repo_name,
        api_calls_rest=totals.get("requests", 0),
        api_calls_graphql=0,
        cache_hits=cache_hits,
        created_at=safe_timestamp,
    )
    
    table_manager.upsert_api_usage(row)


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

    # Track API usage for freshness check
    usage_tracker = ApiUsageTracker(owner=username, repo="all_repos")
    all_repos = repo_manager.get_all_repos_metadata(
        username=username,
        include_languages=False,
        usage=usage_tracker,
    )
    api_usage = usage_tracker.to_dict()

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
        # Fingerprint match = valid (don't re-sync)
        elif current_fingerprint and current_fingerprint == cached_fingerprint:
            valid_repos.append(repo_metadata)
        # New repo (no cached fingerprint)
        else:
            stale_repos.append({**repo_metadata, "fingerprint": current_fingerprint})

    # Record API usage if we have trace context
    _record_api_usage(
        username=username,
        operation="freshness_check",
        api_usage_dict=api_usage,
        job_id=None,
        repo_name=None,
    )

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
    """Health check endpoint."""
    status = {
        "status": "ok",
        "cache": cache_manager.use_cache,
        "version": os.getenv("BUILD_BUILDNUMBER", "dev"),
    }
    return _create_success_response(status, cache_control="no-cache")


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

    try:
        freshness = _identify_repo_freshness(username, trace=trace)
    except (ValueError, TypeError, KeyError) as exc:
        logger.error("Failed to analyze repo freshness: %s", exc, exc_info=True)
        return _create_error_response("Failed to analyze repositories", 500)

    stale_repos = freshness["stale_repos"]
    valid_repos = freshness["cached_bundle"]  # Valid (non-stale) repos from cache

    # If nothing is stale and not forcing refresh, return early
    if not stale_repos and not force_refresh:
        return _create_success_response({"status": "fresh", "repos_count": len(valid_repos)})

    # Determine repos to queue: stale only, or stale + valid if force_refresh
    repos_to_queue = stale_repos + valid_repos if force_refresh else stale_repos
    if not repos_to_queue:
        return _create_success_response({"status": "fresh", "repos_count": 0})

    job_id = str(uuid.uuid4())

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

    job = table_manager.get_job_metadata(username, job_id)
    if not job:
        return _create_error_response("Job not found", 404)

    job_status = job.get("status", "unknown")

    # Compute detailed progress from RepoSyncStatus using shared function
    status_summary = _compute_repo_status_summary(job_id)
    counts = status_summary["counts"]
    lists = status_summary["lists"]

    payload = {
        "job_id": job_id,
        "username": username,
        "status": job_status,
        "metadata_ready": job_status in ("metadata_ready", "completed"),
        "files_ready": job_status == "completed",
        "progress": {
            "total": status_summary["total"],
            "completed": status_summary["completed"],
            "percentage": status_summary["percentage"],
            "pending": counts.get("pending", 0),
            "synced": counts.get("synced", 0),  # Metadata synced, files pending
            "cached": counts.get("cached", 0),  # Files cached (ready)
            "failed": counts.get("failed", 0),
        },
        "created_at": job.get("created_at"),
        "repo_details": {
            "pending": lists.get("pending", [])[:10],  # First 10 for UI
            "synced": lists.get("synced", [])[:10],
            "cached": lists.get("cached", [])[:10],
            "failed": lists.get("failed", [])[:10],
        },
    }

    return _create_success_response(payload, cache_control="no-cache")


@bp.route(route="candidate/{username}/{repo}/cache-status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_cache_status(req: func.HttpRequest) -> func.HttpResponse:
    """Check cache status for a repository and enqueue job if needed."""

    username = req.route_params.get("username")
    repo = req.route_params.get("repo")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)
    if not repo:
        return _create_error_response("Repository name is required", 400)
    
    # Don't record session for polling endpoints
    ctx = _prepare_candidate_context(req, username, record_session=False)

    status_result = _check_repo_cache_status(
        username,
        repo,
        job_id=ctx.job_id,
        trace_id=ctx.trace.get("trace_id"),
    )

    return _create_success_response(status_result, cache_control="no-cache")


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

    ctx = _prepare_candidate_context(req, username)

    # Try table storage first (most recent data)
    if ctx.job_id:
        payload = _get_candidate_metadata(ctx, ctx.job or {})
        if payload.get("data"):
            return _create_success_response(payload, request_id=ctx.trace.get("request_id"))

        # Job exists but no repo data yet
        return _create_error_response(
            f"Candidate for '{username}' not ready (job in progress)",
            404,
            error_code="NOT_READY",
            request_id=ctx.trace.get("request_id"),
        )

    # No job found for username
    return _create_error_response(
        f"No candidate found for '{username}'. Trigger refresh first.",
        404,
        error_code="NOT_FOUND",
        request_id=ctx.trace.get("request_id"),
    )


@bp.route(route="candidate/{username}/profile", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_profile(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve aggregated candidate profile data.

    Combines GitHub user profile (cached in tables), latest job metadata,
    and repo statistics from normalized tables.
    """
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, status_code=400, error_code="BAD_REQUEST")

    ctx = _prepare_candidate_context(req, username)

    # Use unified portfolio data fetcher
    bundle = _get_portfolio_bundle(username, job_id=ctx.job_id)
    profile = _get_or_refresh_user_profile(username)

    payload = {
        "username": username,
        "github_profile": profile,
        "job_metadata": ctx.job,
        "statistics": bundle["statistics"],
    }
    return _create_success_response(payload, cache_control="no-cache", request_id=ctx.trace.get("request_id"))


@bp.route(route="candidate/{username}/summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_profile_summary(req: func.HttpRequest) -> func.HttpResponse:
    """Generate an AI summary for a candidate profile (HTML output)."""
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(
            USERNAME_REQUIRED_MESSAGE,
            status_code=400,
            error_code="VALIDATION_ERROR",
            request_id=_get_trace_context(req).get("request_id")
        )

    ctx = _prepare_candidate_context(req, username)
    
    # Use unified portfolio data fetcher
    bundle = _get_portfolio_bundle(username, job_id=ctx.job_id)
    logger.info("Fetched portfolio bundle for summary generation: repos=%d, languages=%d, stats=%s",
                len(bundle["repo_rows"]), len(bundle["languages_by_repo"]), bundle["statistics"])
    
    if not bundle["repo_rows"]:
        return _create_error_response(
            f"No repository data available for '{username}'. Trigger refresh first.",
            404,
            error_code="NOT_READY",
            request_id=ctx.trace.get("request_id"),
        )
    
    profile = _get_or_refresh_user_profile(username)

    # Get budget-aware limits for profile summary type
    file_budget = get_file_budget("profile")
    
    # Select repos early to minimize file fetching
    selected_repos = _select_repos_for_context(
        bundle["repo_rows"], 
        strategy="recent", 
        max_repos=file_budget["max_repos"]
    )
    
    # Build repo files dict with budget-aware limits
    repo_files = _get_repo_files(
        username,
        selected_repos,
        job_id=ctx.job_id,
        max_additional_readmes=file_budget["max_readme_files"],
        max_config_files=file_budget["max_config_files"],
        include_readme=False
    )
    
    # Log file counts only (not content)
    repo_files_summary = {}
    for repo_name, file_data in repo_files.items():
        repo_files_summary[repo_name] = {
            "readme_content": "yes" if file_data.get("readme_content") else "no",
            "readme_files_count": len(file_data.get("readme_files", {})),
            "config_files_count": len(file_data.get("config_files", []))
        }
    logger.info("repo files for summary generation (limited): %s", repo_files_summary)
    logger.info("Generated statistics for profile summary: %s", bundle["statistics"])
                
    # New path: Use SummaryManager with caching
    manager = SummaryManager(username=username)

    result = manager.get_or_generate_profile_summary(
        job_id=ctx.job_id or "default",
        profile=profile or {},
        repo_rows=bundle["repo_rows"],
        statistics=bundle["statistics"],
        repo_files=repo_files,
    )

    payload = {
        "username": username,
        "job_id": ctx.job_id,
        "summary_html": result["summary_html"],
        "cache_metadata": result.get("metadata", {})
    }

    return _create_success_response(payload, cache_control="no-cache", request_id=ctx.trace.get("request_id"))


@bp.route(route="candidate/{username}/{repo}/readme-summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_summary(req: func.HttpRequest) -> func.HttpResponse:
    """Generate an AI summary for a repository README (HTML output)."""
    username = req.route_params.get("username")
    repo = req.route_params.get("repo")
    if not username or not repo:
        return _create_error_response(
            "Both username and repository name are required",
            status_code=400,
            error_code="VALIDATION_ERROR",
            request_id=_get_trace_context(req).get("request_id"),
        )

    ctx = _prepare_candidate_context(req, username)

    # Get repo metadata for context
    detail = _build_repo_detail_entry(repo, ctx=ctx)
    repo_entry = detail.get("repo_entry", {})

    # Get budget-aware limits for readme summary type
    file_budget = get_file_budget("readme")

    # Build repo files dict with budget-aware limits and include primary readme
    repo_files = _get_repo_files(
        username,
        [{"repo_name": repo}],
        job_id=ctx.job_id,
        max_additional_readmes=file_budget["max_readme_files"],
        max_config_files=file_budget["max_config_files"],
        include_readme=True,  # Always include primary readme for repo summary
    )

    logger.info("repo files for summary generation (limited): %s", repo_files.get(repo, {}))
    logger.info("Generated repo metadata for repo summary: %s", repo_entry)

    # New path: Use SummaryManager with caching
    manager = SummaryManager(username=username)

    result = manager.get_or_generate_readme_summary(
        job_id=ctx.job_id or "default",
        repo_name=repo,
        repo_metadata=repo_entry,
        repo_files=repo_files.get(repo, {}),
    )

    payload = {
        "username": username,
        "repo": repo,
        "job_id": ctx.job_id,
        "repo_entry": repo_entry,
        "readme_summary_html": result["summary_html"],
        "cache_metadata": result.get("metadata", {})
    }

    return _create_success_response(payload, cache_control="no-cache")


@bp.route(route="ai", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def portfolio_query(req: func.HttpRequest) -> func.HttpResponse:
    """Process AI query against candidate's portfolio using bundle-level context.
    
    Supports selection strategies:
    - recent: Most recently updated repos (default, shows current work)
    - random: Random sampling for diversity
    - top_starred: Most popular repos by stars
    """
    body = _parse_json(req)
    query = body.get("query")
    username = body.get("username")
    if not query or not username:
        return _create_error_response(
            "Both 'query' and 'username' are required in the request body",
            400,
            error_code="VALIDATION_ERROR",
            request_id=_get_trace_context(req).get("request_id"),
        )

    # Fetch candidate session and repo data
    profile = _get_or_refresh_user_profile(username)
    ctx = _prepare_candidate_context(req, username)
    
    # Use unified portfolio data fetcher
    bundle = _get_portfolio_bundle(username, job_id=ctx.job_id) if ctx else None
    
    if not ctx or not bundle or not bundle["repo_rows"]:
        return _create_error_response(
            f"No candidate data available for '{username}'. Trigger refresh first.",
            404,
            error_code="NOT_READY",
            request_id=ctx.trace.get("request_id") if ctx else None,
        )
    
    # Get budget-aware limits for query summary type
    file_budget = get_file_budget("query")
    
    # Select repos early to minimize file fetching
    selected_repos = _select_repos_for_context(
        bundle["repo_rows"], 
        strategy="recent", 
        max_repos=file_budget["max_repos"]
    )
    
    # Build repo files dict with budget-aware limits
    repo_files = _get_repo_files(
        username,
        selected_repos,
        job_id=ctx.job_id,
        max_additional_readmes=file_budget["max_readme_files"],
        max_config_files=file_budget["max_config_files"],
    )

    logger.info("repo files for summary generation (limited): %s", repo_files)
    logger.info("Generated statistics for portfolio query: %s", bundle["statistics"])
    
    # Use SummaryManager to build bundle context and generate response
    # Pass selected_repos instead of repo_rows to avoid redundant selection in SummaryManager
    manager = SummaryManager(username=username)
    response = manager.get_or_generate_query_response(
        job_id=ctx.job_id or "default",
        query=query,
        repo_rows=selected_repos,  # Already filtered by strategy
        repo_files=repo_files,
    )
    
    return _create_success_response(response)
