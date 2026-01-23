"""API Gateway blueprint for Cloudfolio.

Ported from `api/v0.3.0/api-gateway/function_app.py`.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
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

from cloudfolio_shared.table import JobMetadataRow

try:  # Azure SDK may be unavailable in local dev; ignore import failures gracefully
    from azure.core.exceptions import ResourceNotFoundError
except Exception:  # pragma: no cover - falls back in environments without azure core
    ResourceNotFoundError = None


logger = logging.getLogger("cloudfolio.api_gateway")
logger.setLevel(logging.INFO)
logger.propagate = True

bp = func.Blueprint()

USERNAME_REQUIRED_MESSAGE = "Username required"


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
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, indent=2),
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
) -> func.HttpResponse:
    payload: Dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def _parse_json(req: func.HttpRequest) -> Dict[str, Any]:
    try:
        return req.get_json() or {}
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def _get_github_repo_manager(username: str) -> GitHubRepoManager:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is not set")
    if not username:
        raise ValueError("Username is required")
    api = GitHubAPI(token=token, username=username)
    return GitHubRepoManager(api, username=username)


def _job_cache_key(job_id: str) -> str:
    return f"job:{job_id}"


def _parse_iso(timestamp: Optional[str]) -> datetime:
    if not timestamp:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


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


def _record_session_candidate(trace: Dict[str, str], username: str, job_id: Optional[str]) -> None:
    """Record session tracking for username/job_id pair.

    Should be called explicitly when session tracking is desired,
    not as a side effect of data retrieval operations.
    """
    session_id = trace.get("session_id") if trace else ""
    if not session_id or not username:
        return

    try:
        table_manager.upsert_session_candidate(session_id, username, job_id)
    except Exception as exc:  # pragma: no cover - safety net
        logger.warning(
            "Failed to persist session candidate (session_id=%s user=%s job_id=%s): %s",
            session_id,
            username,
            job_id or "<none>",
            exc,
        )

def _query_repo_rows(
    username: str,
    job_id: Optional[str] = None,
    repo_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Query repo metadata from table storage.

    Simplified wrapper that delegates to table_manager with error handling.
    Deserialization is fully handled by table_manager - no post-processing needed.

    Args:
        username: Required username filter
        job_id: Optional job_id filter
        repo_names: Optional list of specific repo names to retrieve

    Returns:
        List of fully deserialized repo metadata dicts (no Azure artifacts)
    """
    try:
        # job_id removed from normalized schema - use RepoSyncStatus for job-repo relationships
        return table_manager.query_repo_metadata(username, repo_names=repo_names)
    except Exception:  # pragma: no cover - degraded path
        logger.warning("Failed to query repo metadata for user=%s job_id=%s", username, job_id or "<none>", exc_info=True)
        return []


def _repo_row_to_bundle_entry(
    row: Dict[str, Any],
    *,
    languages: Optional[List[Dict[str, Any]]] = None,
    github_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert deserialized repo metadata row to bundle entry.

    Row is already cleaned by table_manager._deserialize_repo_entity,
    so Azure metadata fields are removed and PartitionKey/RowKey are mapped.
    """
    entry = {
        "name": row.get("repo_name"),
        "fingerprint": row.get("fingerprint"),
        "content_blob": row.get("content_blob"),
        "readme_excerpt": row.get("readme_excerpt"),
        "has_documentation": row.get("has_documentation"),
        "created_at": row.get("created_at"),
        "last_synced_at": row.get("last_synced_at"),
        "updated_at": row.get("updated_at"),
    }

    if languages:
        entry["languages"] = languages

    if github_metadata:
        entry["github"] = {
            "description": github_metadata.get("description"),
            "topics": github_metadata.get("topics", []),
            "homepage_url": github_metadata.get("homepage_url"),
            "stars_count": github_metadata.get("stars_count"),
            "forks_count": github_metadata.get("forks_count"),
            "watchers_count": github_metadata.get("watchers_count"),
            "primary_language": github_metadata.get("primary_language"),
            "license_name": github_metadata.get("license_name"),
            "html_url": github_metadata.get("html_url") or github_metadata.get("homepage_url"),
        }

    return entry


def _bundle_from_table(username: str, job: Dict[str, Any], repo_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build bundle from table data - list fields computed from RepoSyncStatus."""
    entries: List[Dict[str, Any]] = []
    job_id = job.get("job_id")

    # Compute list fields from RepoSyncStatus if job_id available
    synced_repos = []
    queued_repos = []
    expected_repos = []

    if job_id:
        status_rows = table_manager.list_repo_statuses(job_id)
        synced_repos = [row["repo_name"] for row in status_rows if row.get("status") == "synced"]
        # queued/expected not tracked in RepoSyncStatus - use empty lists

    # Enrich each repo with languages and GitHub metadata that recruiters expect
    for row in repo_rows:
        repo_name = row.get("repo_name")
        languages = table_manager.query_repo_languages(username, repo_name) if repo_name else []
        github_meta = table_manager.get_repo_github_metadata(username, repo_name) if repo_name else None
        entries.append(
            _repo_row_to_bundle_entry(
                row,
                languages=languages,
                github_metadata=github_meta,
            )
        )

    return {
        "username": username,
        "job_id": job_id,
        "fingerprint": job.get("bundle_fingerprint"),
        "last_modified": job.get("updated_at"),
        "size_bytes": None,
        "status": job.get("status"),
        "expected_repos": expected_repos,
        "queued_repos": queued_repos,
        "synced_repos": synced_repos,
        "data": entries,
    }


def _bundle_from_cache(username: str) -> Optional[Dict[str, Any]]:
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


def _repo_bundle_from_cache(username: str, repo: str) -> Optional[Dict[str, Any]]:
    repo_cache_key = cache_manager.generate_cache_key(kind="repo", username=username, repo=repo)
    result = cache_manager.get(repo_cache_key)
    if result.get("status") != "valid" or result.get("data") is None:
        return None
    return {
        "username": username,
        "repo": repo,
        "fingerprint": result.get("fingerprint"),
        "last_modified": result.get("last_modified"),
        "size_bytes": result.get("size_bytes"),
        "data": result.get("data"),
    }


def _merge_session_with_cache(session: Dict[str, Any], cache_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge session with cache - list fields removed in normalized schema."""
    if not cache_payload:
        return session
    merged = dict(session)
    # Only merge non-list fields (list fields removed from JobMetadataRow)
    merged["status"] = cache_payload.get("status", merged.get("status"))
    merged["created_at"] = cache_payload.get("created_at", merged.get("created_at"))
    return merged


def _identify_repo_freshness(username: str) -> Dict[str, Any]:
    """Identify stale repositories by comparing fingerprints.

    Raises:
        ValueError: If token is missing, invalid, or rate limit is exceeded.
    """

    repo_manager = _get_github_repo_manager(username)
    bundle_cache_key = cache_manager.generate_cache_key(kind="bundle", username=username)
    cached_bundle = cache_manager.get(bundle_cache_key)

    logger.info(
        "[API_GATEWAY_FRESHNESS] user=%s with cache-key=%s - Fetching all repos metadata with languages (1 + N API calls)",
        username,
        bundle_cache_key,
    )

    all_repos = repo_manager.get_all_repos_metadata(username=username, include_languages=False)
    current_fingerprints = {
        repo.get("name"): FingerprintManager.generate_metadata_fingerprint(repo) for repo in all_repos if repo.get("name")
    }

    cached_fingerprints = _extract_cached_fingerprints(cached_bundle)

    stale_repos: List[Dict[str, Any]] = []
    hydrated_valid_repos: List[Dict[str, Any]] = []

    for repo_metadata in all_repos:
        repo_name = repo_metadata.get("name")
        if not repo_name:
            continue

        current_fingerprint = current_fingerprints.get(repo_name)
        cached_fingerprint = cached_fingerprints.get(repo_name)
        hydrated_repo = _try_hydrate_repo(username, repo_name, current_fingerprint)
        if hydrated_repo and (
            current_fingerprint == cached_fingerprint or hydrated_repo.get("fingerprint") == current_fingerprint
        ):
            hydrated_valid_repos.append(hydrated_repo)
            continue

        stale_repos.append({**repo_metadata, "fingerprint": current_fingerprint})

    logger.info(
        "Repo freshness for user=%s: stale=%d, valid=%d",
        username,
        len(stale_repos),
        len(hydrated_valid_repos),
    )
    return {
        "stale_repos": stale_repos,
        "cached_bundle": hydrated_valid_repos,
        "bundle_status": cached_bundle.get("status"),
    }


def _extract_cached_fingerprints(cached_bundle: Dict[str, Any]) -> Dict[str, str]:
    if cached_bundle.get("status") != "valid" or not isinstance(cached_bundle.get("data"), list):
        return {}
    fingerprints: Dict[str, str] = {}
    for repo in cached_bundle.get("data") or []:
        repo_name = repo.get("metadata", {}).get("name")
        fingerprint = repo.get("fingerprint")
        if repo_name and fingerprint:
            fingerprints[repo_name] = fingerprint
    return fingerprints


def _try_hydrate_repo(username: str, repo_name: str, expected_fingerprint: Optional[str]) -> Optional[Dict[str, Any]]:
    repo_cache_key = cache_manager.generate_cache_key(kind="repo", username=username, repo=repo_name)
    repo_entry = cache_manager.get(repo_cache_key)
    if repo_entry.get("status") != "valid":
        return None
    stored_fp = repo_entry.get("fingerprint")
    if expected_fingerprint and stored_fp != expected_fingerprint:
        return None
    data = repo_entry.get("data")
    return data if isinstance(data, dict) else None


def _upsert_job_session_row(
    job_id: str,
    username: str,
    *,
    status: str,
    force_refresh: bool,
    created_at: str,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """Persist job metadata - list fields removed in normalized schema."""
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


def _persist_job_metadata(
    job_id: str,
    username: str,
    *,
    status: str = "queued",
    force_refresh: bool = False,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """Persist job metadata - list fields removed in normalized schema."""
    created_at = datetime.now(timezone.utc).isoformat()

    _upsert_job_session_row(
        job_id,
        username,
        status=status,
        force_refresh=force_refresh,
        created_at=created_at,
        trace_id=trace_id,
        request_id=request_id,
    )


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


@bp.route(route="bundles/{username}/{repo}/files", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_files(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve files (readme, config, etc.) for a specific repository.

    Query parameters:
        type: File type to retrieve (readme, config, all). Default: readme

    Returns full file content from blob cache without loading entire bundle.
    """
    username = req.route_params.get("username")
    repo = req.route_params.get("repo")
    if not username or not repo:
        return _create_error_response("Username and repository name are required", 400)

    file_type = req.params.get("type", "readme").lower()
    if file_type not in ("readme", "config", "all"):
        return _create_error_response("Invalid file type. Use: readme, config, or all", 400)

    trace = _get_trace_context(req)
    logger.info(
        "[REPO_FILES_REQUEST] request_id=%s username=%s repo=%s type=%s",
        trace["request_id"],
        username,
        repo,
        file_type,
    )

    # Retrieve full repo payload from blob cache (where files live)
    repo_cache_key = cache_manager.generate_cache_key(kind="repo", username=username, repo=repo)
    cached_result = cache_manager.get(repo_cache_key)

    if cached_result.get("status") != "valid" or not cached_result.get("data"):
        logger.info(
            "[REPO_FILES_RESPONSE] request_id=%s username=%s repo=%s status=not_found",
            trace["request_id"],
            username,
            repo,
        )
        return _create_error_response(
            f"No cached data for repository '{repo}'. Trigger refresh first.",
            404
        )

    repo_data = cached_result["data"]

    # Extract requested file types
    response_payload = {
        "username": username,
        "repo": repo,
        "fingerprint": repo_data.get("fingerprint"),
    }

    if file_type == "readme" or file_type == "all":
        response_payload["primary_readme"] = repo_data.get("readme", "")
        response_payload["readme_files"] = repo_data.get("readme_files", {})

    if file_type == "config" or file_type == "all":
        response_payload["config_files"] = repo_data.get("config_files", {})

    logger.info(
        "[REPO_FILES_RESPONSE] request_id=%s username=%s repo=%s type=%s status=success",
        trace["request_id"],
        username,
        repo,
        file_type,
    )

    return _create_success_response(response_payload, cache_control="public, max-age=3600")


@bp.route(route="bundles/{username}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_bundle(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve bundle for a username (specific job_id or latest).

    Pure data retrieval - no session tracking side effects.
    Use /session/bundle for session-aware retrieval with tracking.
    """
    username = req.route_params.get("username")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)

    trace = _get_trace_context(req)
    logger.info(
        "[BUNDLE_REQUEST] request_id=%s username=%s",
        trace["request_id"],
        username,
    )
    candidate_job = _fetch_candidate_jobs(username)
    job_id = candidate_job.get("job_id") if candidate_job else None

    # Try table storage first (most recent data)
    if job_id:
        repo_rows = _query_repo_rows(username, job_id=job_id)
        if repo_rows:
            logger.info(
                "[BUNDLE_RESPONSE] request_id=%s username=%s job_id=%s source=table repos=%d",
                trace["request_id"],
                username,
                job_id,
                len(repo_rows),
            )
            payload = _bundle_from_table(username, candidate_job, repo_rows)
            return _create_success_response(payload)
        
        # Job exists but no repo data yet
        return _create_error_response(f"Bundle for '{username}' not ready (job in progress)", 404)

    # No job found for username
    return _create_error_response(f"No bundle found for '{username}'. Trigger refresh first.", 404)


@bp.route(route="bundles/{username}/refresh", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def trigger_bundle_refresh(req: func.HttpRequest) -> func.HttpResponse:
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
        freshness = _identify_repo_freshness(username)
    except Exception as exc:
        logger.error("Failed to analyze repo freshness: %s", exc, exc_info=True)
        return _create_error_response("Failed to analyze repositories", 500)

    stale_repos = freshness["stale_repos"]
    cached_repos = freshness["cached_bundle"]

    if not stale_repos and not force_refresh:
        logger.info(
            "[REFRESH_RESPONSE] request_id=%s username=%s status=cached repos=%d",
            trace["request_id"],
            username,
            len(cached_repos),
        )
        return _create_success_response({"status": "cached", "repos_count": len(cached_repos)})

    repos_to_queue = stale_repos + cached_repos if force_refresh else stale_repos
    if not repos_to_queue:
        return _create_success_response({"status": "cached", "repos_count": 0})

    job_id = str(uuid.uuid4())
    expected_repo_names = [repo.get("name") for repo in repos_to_queue if repo.get("name")]

    logger.info(
        "[REFRESH_JOB_CREATED] request_id=%s username=%s job_id=%s expected_repos=%d",
        trace["request_id"],
        username,
        job_id,
        len(expected_repo_names),
    )

    _persist_job_metadata(
        job_id,
        username,
        force_refresh=force_refresh,
        trace_id=trace.get("trace_id"),
        request_id=trace.get("request_id"),
    )

    enqueued = 0
    enqueued_names: List[str] = []
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
            enqueued_names.append(repo_name)

    logger.info(
        "[REFRESH_ENQUEUED] request_id=%s username=%s job_id=%s enqueued=%d/%d",
        trace["request_id"],
        username,
        job_id,
        enqueued,
        len(expected_repo_names),
    )

    if enqueued == 0:
        _persist_job_metadata(
            job_id,
            username,
            trace_id=trace.get("trace_id"),
            request_id=trace.get("request_id"),
        )
        return _create_error_response("Failed to enqueue sync jobs", 502)

    if enqueued != len(expected_repo_names):
        logger.warning("Queued %s/%s repos for job %s", enqueued, len(expected_repo_names), job_id)

    _persist_job_metadata(
        job_id,
        username,
        force_refresh=force_refresh,
        trace_id=trace.get("trace_id"),
        request_id=trace.get("request_id"),
    )
    response = {
        "status": "processing",
        "job_id": job_id,
        "repos_queued": enqueued,
        "status_url": f"/api/bundles/{username}/status?job_id={job_id}",
    }
    return _create_success_response(response, status_code=202, cache_control="no-cache")


@bp.route(route="bundles/{username}/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get("username")
    job_id = req.params.get("job_id")
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)
    if not job_id:
        return _create_error_response("job_id query parameter required", 400)

    trace = _get_trace_context(req)
    _record_session_candidate(trace, username, job_id)
    logger.info(
        "[STATUS_REQUEST] request_id=%s session_id=%s username=%s job_id=%s",
        trace["request_id"],
        trace["session_id"],
        username,
        job_id,
    )

    session = _fetch_candidate_jobs(username, job_id)
    cache_data: Optional[Dict[str, Any]] = None

    info: Dict[str, Any] = session or {}
    if session and cache_data:
        info = _merge_session_with_cache(session, cache_data)
    elif not session:
        info = cache_data or {}

    if not info:
        return _create_error_response("Job not found or expired", 404)

    total = info.get("total_repos", 0)
    completed = info.get("completed_repos", 0)
    status = info.get("status", "unknown")
    if total and completed >= total and status != "completed":
        status = "completed"
    payload = {
        "job_id": job_id,
        "username": username,
        "status": status,
        "progress": {
            "total": total,
            "completed": completed,
            "percentage": int((completed / total * 100) if total else 0),
        },
        "created_at": info.get("created_at"),
        "expected_repos": info.get("expected_repos", []),
        "queued_repos": info.get("queued_repos", []),
        "synced_repos": info.get("synced_repos", []),
    }
    logger.info(
        "[STATUS_RESPONSE] request_id=%s username=%s job_id=%s status=%s completed=%s total=%s",
        trace["request_id"],
        username,
        job_id,
        payload.get("status"),
        completed,
        total,
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
    _record_session_candidate(trace, username, resolved_job_id)

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
        payload = _bundle_from_table(username, session, repo_rows)
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
    if not _table_enabled():
        return _create_success_response({"candidates": []}, cache_control="no-cache")

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
        table_bundle = _bundle_from_table(username, session, repo_rows)
        repos_bundle = table_bundle.get("data")

    if not repos_bundle:
        return _create_error_response("No repository bundle available. Trigger refresh first.", 400)

    try:
        scoring_service = RepoScoringService(username=username)
        scored = scoring_service.score_repositories(query, repos_bundle)
        assistant = AIAssistant(username=username)
        response = assistant.process_scored_repositories(query, scored)
        return _create_success_response(response)
    except Exception as exc:
        logger.error("AI query failed: %s", exc, exc_info=True)
        return _create_error_response("Failed to process query", 500)


