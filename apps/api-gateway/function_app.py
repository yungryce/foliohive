"""API Gateway Function App for Cloudfolio.

This app exposes HTTP endpoints for recruiters to trigger repo refresh jobs,
poll job status, fetch cached bundles, and query the semantic assistant.
It implements the queue-based architecture described in 
`.github/prompts/plan-dataProcessingArchitecture.prompt.md` with table-first
data access per `.github/prompts/plan-sharedArchitecture.prompt.md`.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import azure.functions as func

# Clean imports from installed cloudfolio-shared package
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

from cloudfolio_shared.table import CandidateSessionRow

try:  # Azure SDK may be unavailable in local dev; ignore import failures gracefully
    from azure.core.exceptions import ResourceNotFoundError
except Exception:  # pragma: no cover - falls back in environments without azure core
    ResourceNotFoundError = None


logger = logging.getLogger("cloudfolio.api_gateway")
logger.setLevel(logging.INFO)
logger.propagate = True

app = func.FunctionApp()

USERNAME_REQUIRED_MESSAGE = "Username required"


# ---------------------------------------------------------------------------
# Helper responses
# ---------------------------------------------------------------------------

def _create_success_response(data: Dict[str, Any], status_code: int = 200, cache_control: str = "public, max-age=900") -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, indent=2),
        status_code=status_code,
        mimetype="application/json",
        headers={
            "Cache-Control": cache_control,
            "Content-Type": "application/json; charset=utf-8",
        },
    )


def _create_error_response(message: str, status_code: int = 500, details: Optional[str] = None) -> func.HttpResponse:
    payload = {"error": message}
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
    token = os.getenv('GITHUB_TOKEN')
    if not username:
        raise ValueError("Username is required")
    api = GitHubAPI(token=token, username=username)
    return GitHubRepoManager(api, username=username)


def _job_cache_key(job_id: str) -> str:
    return f"job:{job_id}"


def _table_enabled() -> bool:
    try:
        return table_manager.is_enabled()
    except Exception:  # pragma: no cover - defensive fallback
        return False


def _parse_iso(timestamp: Optional[str]) -> datetime:
    if not timestamp:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        if timestamp.endswith('Z'):
            timestamp = timestamp[:-1] + '+00:00'
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _normalize_candidate_session(payload: Optional[Dict[str, Any]], username: str, job_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not payload:
        return None
    session = dict(payload)
    session['username'] = username or session.get('PartitionKey')
    session['job_id'] = job_id or session.get('RowKey') or session.get('job_id')
    session.pop('PartitionKey', None)
    session.pop('RowKey', None)
    return session


def _fetch_candidate_session(username: str, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not _table_enabled():
        return None
    if job_id:
        session = table_manager.get_candidate_session(username, job_id)
        return _normalize_candidate_session(session, username, job_id)

    sessions = table_manager.list_candidate_sessions(username)
    if not sessions:
        return None
    normalized = [
        _normalize_candidate_session(session, username, session.get('RowKey'))
        for session in sessions
    ]
    normalized = [session for session in normalized if session and session.get('job_id')]
    if not normalized:
        return None
    normalized.sort(key=lambda row: _parse_iso(row.get('updated_at')), reverse=True)
    return normalized[0]


def _repo_row_to_bundle_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    repo_name = row.get('RowKey') or row.get('repo_name')
    document = row.get('document') if isinstance(row.get('document'), dict) else {}
    entry: Dict[str, Any] = dict(document)
    entry.setdefault('name', repo_name)
    metadata_candidate = document.get('metadata') if isinstance(document.get('metadata'), dict) else row.get('metadata')
    entry.setdefault('metadata', metadata_candidate or {})
    languages_candidate = document.get('languages') if isinstance(document.get('languages'), dict) else row.get('languages')
    entry.setdefault('languages', languages_candidate or {})
    categorized_candidate = document.get('categorized_types') if isinstance(document.get('categorized_types'), dict) else row.get('categorized_types')
    entry.setdefault('categorized_types', categorized_candidate or {})
    entry.setdefault('fingerprint', document.get('fingerprint') if isinstance(document.get('fingerprint'), str) else row.get('fingerprint'))
    if 'has_documentation' not in entry and row.get('has_documentation') is not None:
        entry['has_documentation'] = bool(row.get('has_documentation'))
    if 'readme_excerpt' not in entry and row.get('readme_excerpt'):
        entry['readme_excerpt'] = row.get('readme_excerpt')
    if 'content_blob' not in entry and row.get('content_blob'):
        entry['content_blob'] = row.get('content_blob')
    return entry


def _query_repo_rows(username: str, session: Optional[Dict[str, Any]], repo_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if not (_table_enabled() and session):
        return []
    job_id = session.get('job_id')
    try:
        rows = table_manager.query_repo_metadata(
            username,
            job_id=job_id,
            repo_names=repo_names,
        )
    except Exception:  # pragma: no cover - degraded path
        return []
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        normalized_row = dict(row)
        normalized_row.setdefault('repo_name', normalized_row.get('RowKey'))
        normalized_row.pop('PartitionKey', None)
        normalized.append(normalized_row)
    return normalized


def _bundle_from_table(username: str, session: Dict[str, Any], repo_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    entries = [_repo_row_to_bundle_entry(row) for row in repo_rows]
    return {
        'username': username,
        'job_id': session.get('job_id'),
        'fingerprint': session.get('bundle_fingerprint'),
        'last_modified': session.get('updated_at'),
        'size_bytes': None,
        'status': session.get('status'),
        'expected_repos': session.get('expected_repos', []),
        'queued_repos': session.get('queued_repos', []),
        'synced_repos': session.get('synced_repos', []),
        'data': entries,
    }


def _bundle_from_cache(username: str) -> Optional[Dict[str, Any]]:
    bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
    result = cache_manager.get(bundle_cache_key)
    if result.get('status') != 'valid' or result.get('data') is None:
        return None
    return {
        'username': username,
        'fingerprint': result.get('fingerprint'),
        'last_modified': result.get('last_modified'),
        'size_bytes': result.get('size_bytes'),
        'data': result.get('data'),
    }


def _repo_bundle_from_cache(username: str, repo: str) -> Optional[Dict[str, Any]]:
    repo_cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo)
    result = cache_manager.get(repo_cache_key)
    if result.get('status') != 'valid' or result.get('data') is None:
        return None
    return {
        'username': username,
        'repo': repo,
        'fingerprint': result.get('fingerprint'),
        'last_modified': result.get('last_modified'),
        'size_bytes': result.get('size_bytes'),
        'data': result.get('data'),
    }


def _merge_session_with_cache(session: Dict[str, Any], cache_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not cache_payload:
        return session
    merged = dict(session)
    merged['completed_repos'] = cache_payload.get('completed_repos', merged.get('completed_repos', 0))
    merged['total_repos'] = cache_payload.get('total_repos', merged.get('total_repos', 0))
    merged['synced_repos'] = cache_payload.get('synced_repos', merged.get('synced_repos', []))
    merged['queued_repos'] = cache_payload.get('queued_repos', merged.get('queued_repos', []))
    merged['expected_repos'] = cache_payload.get('expected_repos', merged.get('expected_repos', []))
    merged['status'] = cache_payload.get('status', merged.get('status'))
    merged['created_at'] = cache_payload.get('created_at', merged.get('created_at'))
    return merged


def _identify_repo_freshness(username: str) -> Dict[str, Any]:
    repo_manager = _get_github_repo_manager(username)
    bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
    cached_bundle = cache_manager.get(bundle_cache_key)

    all_repos = repo_manager.get_all_repos_metadata(username=username, include_languages=True)
    current_fingerprints = {
        repo.get('name'): FingerprintManager.generate_metadata_fingerprint(repo)
        for repo in all_repos if repo.get('name')
    }

    cached_fingerprints = _extract_cached_fingerprints(cached_bundle)

    stale_repos: List[Dict[str, Any]] = []
    hydrated_valid_repos: List[Dict[str, Any]] = []

    for repo_metadata in all_repos:
        repo_name = repo_metadata.get('name')
        if not repo_name:
            continue

        current_fingerprint = current_fingerprints.get(repo_name)
        cached_fingerprint = cached_fingerprints.get(repo_name)
        hydrated_repo = _try_hydrate_repo(username, repo_name, current_fingerprint)
        if hydrated_repo and (current_fingerprint == cached_fingerprint or hydrated_repo.get('fingerprint') == current_fingerprint):
            hydrated_valid_repos.append(hydrated_repo)
            continue

        stale_repos.append({**repo_metadata, 'fingerprint': current_fingerprint})

    logger.info("Repo freshness for %s: %s stale / %s valid", username, len(stale_repos), len(hydrated_valid_repos))
    return {
        'stale_repos': stale_repos,
        'cached_bundle': hydrated_valid_repos,
        'bundle_status': cached_bundle.get('status'),
    }


def _extract_cached_fingerprints(cached_bundle: Dict[str, Any]) -> Dict[str, str]:
    if cached_bundle.get('status') != 'valid' or not isinstance(cached_bundle.get('data'), list):
        return {}
    fingerprints: Dict[str, str] = {}
    for repo in cached_bundle.get('data') or []:
        repo_name = repo.get('metadata', {}).get('name')
        fingerprint = repo.get('fingerprint')
        if repo_name and fingerprint:
            fingerprints[repo_name] = fingerprint
    return fingerprints


def _try_hydrate_repo(username: str, repo_name: str, expected_fingerprint: Optional[str]) -> Optional[Dict[str, Any]]:
    repo_cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo_name)
    repo_entry = cache_manager.get(repo_cache_key)
    if repo_entry.get('status') != 'valid':
        return None
    stored_fp = repo_entry.get('fingerprint')
    if expected_fingerprint and stored_fp != expected_fingerprint:
        return None
    data = repo_entry.get('data')
    return data if isinstance(data, dict) else None


def _queue_mode_enabled() -> bool:
    env_flag = os.getenv('ENABLE_QUEUE_MODE', 'true').lower() == 'true'
    return env_flag and queue_manager.is_enabled()


def _upsert_candidate_session_row(
    job_id: str,
    username: str,
    expected_repo_names: List[str],
    queued: List[str],
    resolved_total: int,
    *,
    status: str,
    force_refresh: bool,
    synced_repos: Optional[List[str]],
    created_at: str,
) -> None:
    if not _table_enabled():
        return

    existing = table_manager.get_candidate_session(username, job_id)
    existing_synced = existing.get('synced_repos', []) if existing else []
    merged_synced = list(synced_repos or existing_synced)
    row = CandidateSessionRow(
        username=username,
        job_id=job_id,
        status=status,
        total_repos=resolved_total,
        completed_repos=len(merged_synced),
        expected_repos=list(expected_repo_names),
        queued_repos=list(queued),
        synced_repos=merged_synced,
        bundle_fingerprint=(existing.get('bundle_fingerprint') if existing else None),
        force_refresh=force_refresh if not existing else bool(existing.get('force_refresh') or force_refresh),
        model_status=(existing.get('model_status') if existing else None),
        model_fingerprint=(existing.get('model_fingerprint') if existing else None),
        created_at=(existing.get('created_at') if existing else created_at) or created_at,
        updated_at=existing.get('updated_at') if existing else None,
    )
    table_manager.upsert_candidate_session(row)


def _persist_job_metadata(
    job_id: str,
    username: str,
    expected_repo_names: List[str],
    *,
    queued_repo_names: Optional[List[str]] = None,
    total_repos: Optional[int] = None,
    status: str = 'queued',
    force_refresh: bool = False,
    synced_repos: Optional[List[str]] = None,
) -> None:
    """Persist job metadata so workers can safely update progress.

    expected_repo_names
        All repos the API *intended* to process for this job (stale set).
    queued_repo_names
        Subset that were actually enqueued to the sync queue. If omitted,
        we assume all expected repos were queued.
    total_repos
        Optional explicit total used for progress; when not provided we
        default to ``len(queued_repo_names or expected_repo_names)`` so
        workers base completion on what was really queued.
    """
    queued = queued_repo_names if queued_repo_names is not None else list(expected_repo_names)
    resolved_total = total_repos if total_repos is not None else len(queued)
    created_at = datetime.now(timezone.utc).isoformat()
    cache_payload = {
        'job_id': job_id,
        'username': username,
        'expected_repos': list(expected_repo_names),
        'queued_repos': list(queued),
        'total_repos': resolved_total,
        'completed_repos': 0 if synced_repos is None else len(synced_repos),
        'synced_repos': list(synced_repos) if synced_repos is not None else [],
        'status': status,
        'created_at': created_at,
        'force_refresh': force_refresh,
    }

    _upsert_candidate_session_row(
        job_id,
        username,
        expected_repo_names,
        list(queued),
        resolved_total,
        status=status,
        force_refresh=force_refresh,
        synced_repos=list(synced_repos) if synced_repos is not None else None,
        created_at=created_at,
    )

    cache_manager.save(_job_cache_key(job_id), cache_payload, ttl=3600)


def _validate_blob_path(blob_path: str) -> None:
    if not blob_path:
        raise ValueError("Missing 'path'")
    if '..' in blob_path or blob_path.startswith('/') or '\\' in blob_path or len(blob_path) > 200:
        raise ValueError("Invalid path")


def _build_image_response(blob_client, req: func.HttpRequest) -> func.HttpResponse:
    downloader = blob_client.download_blob()
    props = downloader.properties
    etag = (props.etag or '').strip('"') if props and props.etag else ''

    inm = (req.headers.get('If-None-Match') or '').strip('"')
    if inm and etag and inm == etag:
        return func.HttpResponse(status_code=304)

    content = downloader.readall()
    ctype = (
        props.content_settings.content_type
        if props and props.content_settings and props.content_settings.content_type
        else (mimetypes.guess_type(blob_client.blob_name)[0] or 'application/octet-stream')
    )
    if not ctype.startswith('image/'):
        raise ValueError("Requested blob is not an image")

    resp = func.HttpResponse(body=content, mimetype=ctype, status_code=200)
    resp.headers['Cache-Control'] = 'public, max-age=600'
    if etag:
        resp.headers['ETag'] = f'"{etag}"'
    if props and props.last_modified:
        resp.headers['Last-Modified'] = props.last_modified.strftime('%a, %d %b %Y %H:%M:%S GMT')
    if props and props.size:
        resp.headers['Content-Length'] = str(props.size)
    return resp


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    status = {
        "status": "ok",
        "queue_mode": _queue_mode_enabled(),
        "cache": cache_manager.use_cache,
        "version": os.getenv('BUILD_BUILDNUMBER', 'dev'),
    }
    return _create_success_response(status, cache_control="no-cache")


@app.route(route="bundles/{username}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_repo_bundle(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get('username')
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)

    requested_job_id = req.params.get('job_id')
    session = _fetch_candidate_session(username, requested_job_id)
    repo_rows = _query_repo_rows(username, session)

    if session and repo_rows:
        payload = _bundle_from_table(username, session, repo_rows)
        return _create_success_response(payload)

    cache_payload = _bundle_from_cache(username)
    if cache_payload:
        return _create_success_response(cache_payload)

    if session:
        return _create_error_response(f"Bundle for '{username}' not ready", 404)

    return _create_error_response(f"No valid bundle found for '{username}'", 404)


@app.route(route="bundles/{username}/{repo}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_single_repo_bundle(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get('username')
    repo = req.route_params.get('repo')
    if not username or not repo:
        return _create_error_response("Username and repository name are required", 400)

    requested_job_id = req.params.get('job_id')
    session = _fetch_candidate_session(username, requested_job_id)
    repo_rows = _query_repo_rows(username, session, repo_names=[repo])

    if session and repo_rows:
        entry = _repo_row_to_bundle_entry(repo_rows[0])
        payload = {
            'username': username,
            'repo': entry.get('name') or repo,
            'fingerprint': entry.get('fingerprint'),
            'last_modified': repo_rows[0].get('updated_at'),
            'size_bytes': None,
            'data': entry,
        }
        return _create_success_response(payload)

    cache_payload = _repo_bundle_from_cache(username, repo)
    if cache_payload:
        return _create_success_response(cache_payload)

    if session:
        return _create_error_response(f"No table metadata ready for '{repo}'", 404)

    return _create_error_response(f"No valid repository data found for '{repo}' by user '{username}'", 404)


@app.route(route="bundles/{username}/refresh", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def trigger_bundle_refresh(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get('username')
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)

    body = _parse_json(req)
    force_refresh = bool(body.get('force_refresh', False))

    if not _queue_mode_enabled():
        return _create_error_response("Queue mode disabled or queue manager unavailable", 503)

    try:
        freshness = _identify_repo_freshness(username)
    except Exception as exc:
        logger.error("Failed to analyze repo freshness: %s", exc, exc_info=True)
        return _create_error_response("Failed to analyze repositories", 500)

    stale_repos = freshness['stale_repos']
    cached_repos = freshness['cached_bundle']

    # Normal flow: only sync stale repos. Forced refresh: resync all repos (stale + cached).
    if not stale_repos and not force_refresh:
        logger.info("No stale repos for %s; returning cached bundle status", username)
        return _create_success_response({
            "status": "cached",
            "repos_count": len(cached_repos),
        })

    if force_refresh:
        logger.info(
            "Force-refresh for %s: queueing %s stale repos + %s cached repos",
            username,
            len(stale_repos),
            len(cached_repos),
        )
        repos_to_queue = stale_repos + cached_repos
    else:
        logger.info(
            "Incremental refresh for %s: queueing %s stale repos",
            username,
            len(stale_repos),
        )
        repos_to_queue = stale_repos
    
    if not repos_to_queue:
        return _create_success_response({
            "status": "cached",
            "repos_count": 0,
        })

    job_id = str(uuid.uuid4())
    expected_repo_names = [repo.get('name') for repo in repos_to_queue if repo.get('name')]

    # Seed job metadata before enqueue so workers never see a missing job record.
    # At this point we only know the *expected* repos; queued set may shrink.
    _persist_job_metadata(job_id, username, expected_repo_names, force_refresh=force_refresh)

    enqueued = 0
    enqueued_names: List[str] = []
    for idx, repo_metadata in enumerate(repos_to_queue, 1):
        repo_name = repo_metadata.get('name')
        if not repo_name:
            continue
        
        # Extract only essential identifiers for queue message
        repo_fingerprint = repo_metadata.get('fingerprint')
        
        logger.info(
            "[TRIGGER] Queue repo %d/%d: name=%s, fingerprint=%s, job_id=%s",
            idx,
            len(repos_to_queue),
            repo_name,
            repo_fingerprint,
            job_id
        )
        
        if queue_manager.enqueue_sync_job(job_id, username, repo_name, repo_fingerprint):
            enqueued += 1
            enqueued_names.append(repo_name)

    logger.info("--------------Enqueued %s/%s repos for job %s", enqueued, len(expected_repo_names), job_id)

    if enqueued == 0:
        _persist_job_metadata(job_id, username, [], queued_repo_names=[], total_repos=0)
        return _create_error_response("Failed to enqueue sync jobs", 502)

    if enqueued != len(expected_repo_names):
        logger.warning("Queued %s/%s repos for job %s", enqueued, len(expected_repo_names), job_id)

    # Update job metadata to reflect the actual enqueue count.
    # expected_repos stays as the full stale set; queued_repos captures
    # what the worker should eventually process and drive merge off.
    _persist_job_metadata(
        job_id,
        username,
        expected_repo_names,
        queued_repo_names=enqueued_names,
        total_repos=enqueued,
        force_refresh=force_refresh,
    )
    response = {
        "status": "processing",
        "job_id": job_id,
        "repos_queued": enqueued,
        "status_url": f"/api/bundles/{username}/status?job_id={job_id}",
    }
    return _create_success_response(response, status_code=202, cache_control="no-cache")


@app.route(route="bundles/{username}/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get('username')
    job_id = req.params.get('job_id')
    if not username:
        return _create_error_response(USERNAME_REQUIRED_MESSAGE, 400)
    if not job_id:
        return _create_error_response("job_id query parameter required", 400)

    session = _fetch_candidate_session(username, job_id)
    cache_result = cache_manager.get(_job_cache_key(job_id))
    cache_data = cache_result.get('data') if cache_result.get('status') == 'valid' else None

    if not session and not cache_data:
        return _create_error_response("Job not found or expired", 404)

    info = session or {}
    if session and cache_data:
        info = _merge_session_with_cache(session, cache_data)
    elif not session and cache_data:
        info = cache_data

    total = info.get('total_repos', 0)
    completed = info.get('completed_repos', 0)
    status = info.get('status', 'unknown')
    if total and completed >= total and status != 'completed':
        status = 'completed'
    payload = {
        'job_id': job_id,
        'username': username,
        'status': status,
        'progress': {
            'total': total,
            'completed': completed,
            'percentage': int((completed / total * 100) if total else 0),
        },
        'created_at': info.get('created_at'),
        'expected_repos': info.get('expected_repos', []),
        'queued_repos': info.get('queued_repos', []),
        'synced_repos': info.get('synced_repos', []),
    }
    return _create_success_response(payload, cache_control="no-cache")


@app.route(route="ai", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def portfolio_query(req: func.HttpRequest) -> func.HttpResponse:
    body = _parse_json(req)
    query = body.get('query')
    username = body.get('username')
    if not query or not username:
        return _create_error_response("Request body must contain 'query' and 'username'", 400)

    bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
    cached_results = cache_manager.get(bundle_cache_key)
    repos_bundle = cached_results.get('data') if cached_results.get('status') == 'valid' else None

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

