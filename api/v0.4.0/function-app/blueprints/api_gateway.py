"""API Gateway blueprint for foliohive.

"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import perf_counter
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
    JobMetadataRow,
    RepoGitHubMetadataRow,
    RepoSyncStatusRow,
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
    status: Optional[str] = None


logger = logging.getLogger("foliohive.api_gateway")
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
            "api_version": "0.4.0",
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
            "api_version": "0.4.0",
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


# ---------------------------------------------------------------------------
# Candidate Context Preparation
# ---------------------------------------------------------------------------

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
    resolved_username = job.get("username") if job else None 
    status = job.get("status") if job else None
    
    # Record session tracking if enabled
    if record_session:
        table_manager.upsert_session_candidate(session_id, resolved_username, final_job_id)
    
    return CandidateContext(
        trace=trace,
        job=job,
        job_id=final_job_id,
        username=resolved_username,
        status=status,
    )


 # ---------------------------------------------------------------------------
 # Profile Helpers
 # ---------------------------------------------------------------------------

def _is_profile_fresh(profile: Dict[str, Any], ttl_seconds: int) -> bool:
    cached_at = profile.get("cached_at")
    if not cached_at:
        return False
    cached_dt = _parse_iso(cached_at)
    if cached_dt == datetime.min.replace(tzinfo=timezone.utc):
        return False
    return (datetime.now(timezone.utc) - cached_dt).total_seconds() < ttl_seconds

def _refresh_user_profile(username: str, cached_profile: Optional[Dict[str, Any]], job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    repo_manager = _get_repo_manager(username)
    profile = repo_manager.get_user_profile(username=username, purpose="user_profile")
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
        job_id=job_id,
        cached_at=cached_at,
    )
    table_manager.upsert_user_profile(row)

    return table_manager.get_user_profile(username) or asdict(row)


def _get_or_refresh_user_profile(username: str, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cached = table_manager.get_user_profile(username)
    if cached and _is_profile_fresh(cached, PROFILE_TTL_SECONDS):
        return cached

    refreshed = _refresh_user_profile(username, cached, job_id)
    return refreshed

    
# ---------------------------------------------------------------------------
# Core Logic Helpers
# ---------------------------------------------------------------------------


def _get_single_repo_entry(
    repo_name: str,
    *,
    ctx: CandidateContext,
) -> Dict[str, Any]:
    """Get metadata entry for a single repository.
    
    Args:
        repo_name: A specific repository name
        ctx: Candidate context with resolved job_id and username
    
    Returns:
        Single repo entry dict with metadata and languages, or empty dict if not found
    """

    # Query for the specific repo
    github_metadata = table_manager.get_repo_github_metadata(ctx.username, repo_name)
    languages = table_manager.get_repo_languages(ctx.username, repo_name)
    entry = _build_repo_statistics(languages=languages, github_metadata=github_metadata)
    if not entry.get("name"):
        entry["name"] = repo_name

    return entry


def _get_repos_entries(
    ctx: CandidateContext,
) -> List[Dict[str, Any]]:
    """Get metadata entries for multiple repositories in a job.
    
    Args:
        ctx: Candidate context with resolved job_id and username
    
    Returns:
        List of repo entry dicts with metadata and languages
    """

    all_repos = table_manager.query_repo_github_metadata(ctx.username) 
    if not all_repos:
        return []
    
    languages_by_repo = table_manager.query_all_repo_languages(ctx.job_id) if ctx.job_id else {} 
    entries: List[Dict[str, Any]] = []
    for github_metadata in all_repos:
        repo_name = github_metadata.get("repo_name")
        if not repo_name:
            continue

        languages = languages_by_repo.get(repo_name)
        entries.append(_build_repo_statistics(languages=languages, github_metadata=github_metadata))

    return entries


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
            "watchers_count": github_metadata.get("watchers", 0),
            "open_issues_count": github_metadata.get("open_issues", 0),
            "topics": topics,
            "license_name": github_metadata.get("license_name"),
        }
        entry["fingerprint"] = github_metadata.get("fingerprint")
        
    return entry


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

    all_repos = table_manager.query_repo_github_metadata(username)
    if not all_repos:
        return []

    languages_by_repo = table_manager.query_all_repo_languages(job_id) if job_id else {}
    statistics = _aggregate_portfolio_statistics(all_repos, languages_by_repo)
    
    return {
        "repo_rows": all_repos,
        "languages_by_repo": languages_by_repo,
        "statistics": statistics,
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
    Preserves existing trace_id, and request_id on updates.
    """
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()

    existing = table_manager.get_job_metadata(username, job_id)
    row = JobMetadataRow(
        username=username,
        job_id=job_id,
        status=status,
        force_refresh=force_refresh if not existing else bool(existing.get("force_refresh") or force_refresh),
        created_at=(existing.get("created_at") if existing else created_at) or created_at,
        updated_at=existing.get("updated_at") if existing else None,
        trace_id=trace_id if not existing else (existing.get("trace_id") or trace_id),
        request_id=request_id if not existing else (existing.get("request_id") or request_id),
    )
    table_manager.upsert_job_metadata(row)


def _update_valid_repos_job_id(
    username: str,
    valid_repos: List[Dict[str, Any]],
    job_id: str,
) -> None:
    """Update job_id for valid (non-stale) repositories.
    
    Associates valid repos with the current job even though they don't require
    re-syncing. This prevents drift where valid repos retain old job_id while
    stale repos get the new job_id, ensuring all repos for a candidate are
    tied to the same job_id for consistent data provenance.
    
    Args:
        username: GitHub username
        valid_repos: List of repo metadata dicts for repos that passed freshness check
        job_id: Current job ID to associate with repos
    """
    for repo_metadata in valid_repos:
        repo_name = repo_metadata.get("name")
        if not repo_name:
            continue
        
        # Fetch existing metadata and update job_id
        existing = table_manager.get_repo_github_metadata(username, repo_name)
        if existing:
            # Reconstruct row with updated job_id while preserving all other fields
            updated_row = RepoGitHubMetadataRow(
                username=username,
                repo_name=repo_name,
                job_id=job_id,  # Update to current job_id
                fingerprint=existing.get("fingerprint"),
                description=existing.get("description"),
                topics=existing.get("topics"),
                html_url=existing.get("html_url"),
                homepage_url=existing.get("homepage_url"),
                stars_count=existing.get("stars_count", 0),
                forks_count=existing.get("forks_count", 0),
                watchers=existing.get("watchers", 0),
                open_issues=existing.get("open_issues", 0),
                primary_language=existing.get("primary_language"),
                is_fork=existing.get("is_fork", False),
                is_archived=existing.get("is_archived", False),
                license_name=existing.get("license_name"),
                github_created_at=existing.get("github_created_at"),
                github_updated_at=existing.get("github_updated_at"),
                github_pushed_at=existing.get("github_pushed_at"),
                default_branch=existing.get("default_branch"),
                created_at=existing.get("created_at"),
                last_accessed_at=existing.get("last_accessed_at"),
            )
            table_manager.upsert_repo_github_metadata(updated_row)


def _identify_repo_freshness(username: str, trace: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Identify stale repositories by comparing fingerprints.
    
    Uses table_manager for metadata fingerprints (lightweight, normalized).
    No blob fetching - eliminates ~400KB transfer per freshness check.
    
    Raises:
        ValueError: If token is missing, invalid, or rate limit is exceeded.
    """
    repo_manager = _get_repo_manager(username)

    all_repos = repo_manager.get_all_repos_metadata(
        username=username,
        purpose="freshness_check",
        include_languages=False,
    )

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
    Validates that each candidate's associated job still exists; filters out stale candidates
    whose jobs have been deleted from the database.
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
    
    # Validate candidates: filter out those whose jobs no longer exist
    valid_candidates = []
    for row in candidates:
        username = row.get("username")
        latest_job_id = row.get("latest_job_id")
        
        if not username:
            continue
        
        # If candidate has a job_id, verify it still exists
        if latest_job_id:
            job = table_manager.get_job_metadata(username, latest_job_id)
            if not job:
                continue
        
        # Candidate is valid (either has no job_id or job exists)
        valid_candidates.append({
            "username": username,
            "latest_job_id": latest_job_id,
            "last_viewed_at": row.get("last_viewed_at"),
            "query_count": row.get("query_count"),
        })
    
    payload = {
        "candidates": valid_candidates
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
    started_at = perf_counter()
    username = req.route_params.get("username")
    logger.info("[LATENCY_START] fn=trigger_candidate_refresh username=%s", username)
    try:
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

        # Update valid repos' job_id so they're associated with the current job
        _update_valid_repos_job_id(username, valid_repos, job_id)

        # Create pending status rows for repos that need to be queued/synced
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
            table_manager.update_job_metadata_conditional(username, job_id, {"status": "failed"})
            return _create_error_response("Failed to enqueue sync jobs", 502)

        response = {
            "status": "processing",
            "job_id": job_id,
            "repos_queued": enqueued,
            "status_url": f"/api/candidate/{username}/status?job_id={job_id}",
        }
        return _create_success_response(response, status_code=202, cache_control="no-cache")
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "[LATENCY_FINISH] fn=trigger_candidate_refresh username=%s elapsed_ms=%.2f",
            username,
            elapsed_ms,
        )


@bp.route(route="candidate/{username}/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    """Poll job progress and status.
    
    Returns real-time status for a specific job from JobMetadata table.
    Job state is tracked via status field: queued → syncing → metadata_ready → 
    caching_started → completed (or failed at any stage).
    
    RepoSyncStatus is used only for backend operation coordination and is not
    exposed to clients.
    
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
            "status": str,  // "queued" | "syncing" | "metadata_ready" | "caching_started" | "completed" | "failed"
            "metadata_ready": bool,  // True when metadata available (metadata_ready or later)
            "summary_ready": bool,   // True when summaries generated (completed)
            "created_at": str,
            "progress": {
                "total": int,          // Total number of repos in job
                "completed": int,      // summary_ready + failed (terminal states)
                "percentage": int,     // Completion percentage (0-100)
                "pending": int,        // Waiting to be processed
                "synced": int,         // Metadata synced, summary pending
                "summary_ready": int,  // Micro-summary generated
                "failed": int          // Failed state
            },
            "repo_details": {
                "pending": [str],      // List of pending repo names
                "synced": [str],       // List of synced repo names
                "summary_ready": [str],// List of repos with ready summaries
                "failed": [str]        // List of failed repo names
            }
        }
    
    Status progression:
        queued → syncing → metadata_ready → caching_started → completed
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

    # Build progress from RepoSyncStatus for client polling
    progress = {
        "total": 0,
        "completed": 0,
        "percentage": 0,
        "pending": 0,
        "synced": 0,
        "summary_ready": 0,
        "failed": 0,
    }
    repo_details = {
        "pending": [],
        "synced": [],
        "summary_ready": [],
        "failed": [],
    }
    
    status_rows = table_manager.list_repo_statuses(job_id)
    if status_rows:
        progress["total"] = len(status_rows)
        for row in status_rows:
            status = row.get("status", "pending")
            repo = row.get("repo_name", "unknown")
            
            if status == "pending":
                progress["pending"] += 1
                repo_details["pending"].append(repo)
            elif status == "synced":
                progress["synced"] += 1
                repo_details["synced"].append(repo)
            elif status == "summary_ready":
                progress["summary_ready"] += 1
                progress["completed"] += 1
                repo_details["summary_ready"].append(repo)
            elif status == "failed":
                progress["failed"] += 1
                progress["completed"] += 1
                repo_details["failed"].append(repo)
        
        if progress["total"] > 0:
            progress["percentage"] = int((progress["completed"] / progress["total"]) * 100)

    payload = {
        "job_id": job_id,
        "username": username,
        "status": job_status,
        "metadata_ready": job_status in ("metadata_ready", "caching_started", "completed"),
        "summary_ready": job_status == "completed",
        "created_at": job.get("created_at"),
        "progress": progress,
        "repo_details": repo_details,
    }

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

    ctx = _prepare_candidate_context(req, username)

    if ctx.job_id and ctx.username and ctx.status in ("metadata_ready", "caching_started", "completed"):
        job = ctx.job or {}
        
        # Fetch repo metadata for this job (filters by job_id)
        entries = _get_repos_entries(ctx)
        
        if entries:
            payload = {
                "username": ctx.username,
                "job_id": job.get("job_id") or ctx.job_id,
                "last_modified": _restore_iso_timestamp(job.get("updated_at")) or job.get("updated_at"),
                "status": job.get("status"),
                "data": entries,
            }

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


@bp.route(route="candidate/{username}/{repo}/metadata", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_candidate_repo_metadata(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve metadata for a single repository.

    Uses normalized table storage and returns one repository bundle entry so the
    UI can render project metadata immediately while README summary generation
    continues asynchronously.
    """
    username = req.route_params.get("username")
    repo = req.route_params.get("repo")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400, error_code="VALIDATION_ERROR")
    if not repo:
        return _create_error_response("Repository name required", 400, error_code="VALIDATION_ERROR")
    
    ctx = _prepare_candidate_context(req, username)

    if ctx.job_id and ctx.username and ctx.status in ("metadata_ready", "caching_started", "completed"):
        job = ctx.job or {}
        repo_entry = _get_single_repo_entry(repo, ctx=ctx)

        if repo_entry:
            payload = {
                "username": ctx.username,
                "repo": repo,
                "job_id": job.get("job_id") or ctx.job_id,
                "last_modified": _restore_iso_timestamp(job.get("updated_at")) or job.get("updated_at"),
                "status": job.get("status"),
                "repo_entry": repo_entry,
                "data": repo_entry,
            }
            return _create_success_response(payload, request_id=ctx.trace.get("request_id"))
        return _create_error_response(
            f"Repository '{repo}' for candidate '{username}' not ready (job in progress)",
            404,
            error_code="NOT_READY",
            request_id=ctx.trace.get("request_id"),
        )
    return _create_error_response(
        f"No candidate found for '{username}' or job not ready. Trigger refresh first.",
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
    profile = _get_or_refresh_user_profile(username, ctx.job_id)

    payload = {
        "username": username,
        "github_profile": profile,
        "job_metadata": ctx.job,
        "statistics": bundle["statistics"],
    }
    return _create_success_response(payload, cache_control="no-cache", request_id=ctx.trace.get("request_id"))


@bp.route(route="candidate/{username}/{repo}/readme-summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_summary(req: func.HttpRequest) -> func.HttpResponse:
    """Generate an AI summary for a repository by expanding cached micro-summary.
    
    This endpoint uses the cached micro-summary to generate a detailed HTML summary
    for the single-repo detail view. No raw file blobs are fetched.
    """
    started_at = perf_counter()
    username = req.route_params.get("username")
    repo = req.route_params.get("repo")
    logger.info("[LATENCY_START] fn=get_repo_summary username=%s repo=%s", username, repo)
    try:
        if not username:
            return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400, error_code="VALIDATION_ERROR")
        if not repo:
            return _create_error_response("Repository name required", 400, error_code="VALIDATION_ERROR")

        ctx = _prepare_candidate_context(req, username)
        if not ctx.job_id or not ctx.username or not ctx.status in ("completed"):
            return _create_error_response(
                f"Candidate '{username}' or repository '{repo}' not ready. Job is in progress.",
                404,
                error_code="NOT_READY",
                request_id=ctx.trace.get("request_id"),
            )
        
        # Query for the specific repo
        manager = SummaryManager(username=username)

        result = manager.expand_repo_micro_summary(
            repo_name=repo,
            job_id=ctx.job_id,
        )
        if not result:
            logger.error("Failed to expand micro-summary for %s/%s", username, repo)
            return _create_error_response(
                "Failed to generate detailed summary",
                status_code=500,
                error_code="EXPANSION_FAILED",
                request_id=ctx.trace.get("request_id"),
            )

        payload = {
            "username": username,
            "repo": repo,
            "job_id": ctx.job_id,
            "readme_summary_markdown": result["summary_markdown"],
            "cache_metadata": result.get("metadata", {}),
            "cache_hit": result.get("cache_hit", False),
        }

        return _create_success_response(payload
                                        , cache_control="no-cache")
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "[LATENCY_FINISH] fn=get_repo_summary username=%s repo=%s elapsed_ms=%.2f",
            username,
            repo,
            elapsed_ms,
        )


@bp.route(route="candidate/{username}/summary", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_profile_summary(req: func.HttpRequest) -> func.HttpResponse:
    """Generate an AI summary for a candidate profile (HTML output)."""
    started_at = perf_counter()
    username = req.route_params.get("username")
    logger.info("[LATENCY_START] fn=get_profile_summary username=%s", username)
    try:
        if not username:
            return _create_error_response(
                USERNAME_REQUIRED_MESSAGE,
                status_code=400,
                error_code="VALIDATION_ERROR",
                request_id=_get_trace_context(req).get("request_id")
            )

        ctx = _prepare_candidate_context(req, username)
        if not ctx.job_id or not ctx.username or not ctx.status in ("completed"):
            return _create_error_response(
                f"Candidate '{username}' not ready. Job is in progress.",
                404,
                error_code="NOT_READY",
                request_id=ctx.trace.get("request_id"),
            )

        profile = _get_or_refresh_user_profile(username, ctx.job_id)
        manager = SummaryManager(username=username)

        response = manager.get_or_generate_profile_summary(profile=profile or {}, job_id=ctx.job_id)

        payload = {
            "username": username,
            "job_id": ctx.job_id,
            "summary_markdown": response.get("summary_markdown") if response else None,
            "metadata": response.get("metadata") if response else {},
        }

        return _create_success_response(payload, cache_control="no-cache", request_id=ctx.trace.get("request_id"))
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "[LATENCY_FINISH] fn=get_profile_summary username=%s elapsed_ms=%.2f",
            username,
            elapsed_ms,
        )


@bp.route(route="ai", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def portfolio_query(req: func.HttpRequest) -> func.HttpResponse:
    """Process AI query against candidate's portfolio using cached micro-summaries."""
    started_at = perf_counter()
    username = req.params.get("username")
    logger.info("[LATENCY_START] fn=portfolio_query username=%s", username)
    try:
        query = _parse_json(req.params.get("query"))
        if not username:
            return _create_error_response(
                USERNAME_REQUIRED_MESSAGE,
                status_code=400,
                error_code="VALIDATION_ERROR",
                request_id=_get_trace_context(req).get("request_id"),
            )

        if not query or not query.strip():
            return _create_error_response(
                "'Query' is required in the request body",
                400,
                error_code="VALIDATION_ERROR",
                request_id=_get_trace_context(req).get("request_id"),
            )
        
        ctx = _prepare_candidate_context(req, username)
        if not ctx.job_id or not ctx.username or not ctx.status in ("completed"):
            return _create_error_response(
                f"Candidate '{username}' not ready. Job is in progress.",
                404,
                error_code="NOT_READY",
                request_id=ctx.trace.get("request_id"),
            )

        profile = _get_or_refresh_user_profile(username, ctx.job_id)
        manager = SummaryManager(username=username)

        response = manager.get_or_generate_query_response(
            job_id=ctx.job_id,
            query=query,
            profile=profile or {},
        )

        payload = {
            "username": username,
            "job_id": ctx.job_id,
            "query_summary": response.get("query_summary") if response else None,
            "metadata": response.get("metadata") if response else {},
        }
        return _create_success_response(payload, cache_control="no-cache", request_id=ctx.trace.get("request_id"))
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "[LATENCY_FINISH] fn=portfolio_query username=%s elapsed_ms=%.2f",
            username,
            elapsed_ms,
        )
