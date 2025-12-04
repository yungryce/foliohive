"""API Gateway Function App for Cloudfolio.

This app exposes HTTP endpoints for recruiters to trigger repo refresh jobs,
poll job status, fetch cached bundles, query the semantic assistant, and
retrieve survey media. It replaces the old Durable Functions monolith with a
queue-based architecture described in `.github/prompts/plan-azureStorageQueuesArchitecture.prompt.md`.
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
)

try:  # Azure SDK may be unavailable in local dev; ignore import failures gracefully
    from azure.core.exceptions import ResourceNotFoundError
except Exception:  # pragma: no cover - falls back in environments without azure core
    ResourceNotFoundError = None


logger = logging.getLogger('portfolio.api')
logger.setLevel(logging.INFO)
logger.propagate = True

app = func.FunctionApp()


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


def _persist_job_metadata(job_id: str, username: str, repo_names: List[str]) -> None:
    job_payload = {
        'job_id': job_id,
        'username': username,
        'repo_names': repo_names,
        'total_repos': len(repo_names),
        'completed_repos': 0,
        'status': 'queued',
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    cache_manager.save(_job_cache_key(job_id), job_payload, ttl=3600)


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
        return _create_error_response("Username required", 400)

    bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
    result = cache_manager.get(bundle_cache_key)
    if result.get('status') != 'valid' or result.get('data') is None:
        return _create_error_response(f"No valid bundle found for '{username}'", 404)

    payload = {
        "username": username,
        "fingerprint": result.get('fingerprint'),
        "last_modified": result.get('last_modified'),
        "size_bytes": result.get('size_bytes'),
        "data": result.get('data'),
    }
    return _create_success_response(payload)


@app.route(route="bundles/{username}/{repo}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_single_repo_bundle(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get('username')
    repo = req.route_params.get('repo')
    if not username or not repo:
        return _create_error_response("Username and repository name are required", 400)

    repo_cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo)
    result = cache_manager.get(repo_cache_key)
    if result.get('status') != 'valid' or result.get('data') is None:
        return _create_error_response(f"No valid repository data found for '{repo}' by user '{username}'", 404)

    payload = {
        "username": username,
        "repo": repo,
        "fingerprint": result.get('fingerprint'),
        "last_modified": result.get('last_modified'),
        "size_bytes": result.get('size_bytes'),
        "data": result.get('data'),
    }
    return _create_success_response(payload)


@app.route(route="bundles/{username}/refresh", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def trigger_bundle_refresh(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get('username')
    if not username:
        return _create_error_response("Username required", 400)

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
    if not stale_repos and not force_refresh:
        logger.info("No stale repos for %s; returning cached bundle status", username)
        return _create_success_response({
            "status": "cached",
            "repos_count": len(freshness['cached_bundle']),
        })

    job_id = str(uuid.uuid4())
    repo_names = [repo.get('name') for repo in stale_repos if repo.get('name')]

    if not repo_names and not force_refresh:
        return _create_success_response({
            "status": "cached",
            "repos_count": len(freshness['cached_bundle']),
        })

    enqueued = 0
    for repo_metadata in stale_repos:
        repo_name = repo_metadata.get('name')
        if not repo_name:
            continue
        if queue_manager.enqueue_sync_job(job_id, username, repo_metadata, repo_metadata.get('fingerprint')):
            enqueued += 1

    if enqueued == 0:
        return _create_error_response("Failed to enqueue sync jobs", 502)

    _persist_job_metadata(job_id, username, repo_names)
    response = {
        "status": "processing",
        "job_id": job_id,
        "repos_queued": enqueued,
        "status_url": f"/api/bundles/{username}/status?job_id={job_id}",
    }
    return _create_success_response(response, status_code=202, cache_control="no-cache")


@app.route(route="bundles/{username}/status", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    job_id = req.params.get('job_id')
    if not job_id:
        return _create_error_response("job_id query parameter required", 400)

    job_data = cache_manager.get(_job_cache_key(job_id))
    if job_data.get('status') != 'valid' or not job_data.get('data'):
        return _create_error_response("Job not found or expired", 404)

    info = job_data['data']
    total = info.get('total_repos', 0)
    completed = info.get('completed_repos', 0)
    status = info.get('status', 'unknown')
    if total and completed >= total and status != 'completed':
        status = 'completed'
    payload = {
        "job_id": job_id,
        "username": info.get('username'),
        "status": status,
        "progress": {
            "total": total,
            "completed": completed,
            "percentage": int((completed / total * 100) if total else 0),
        },
        "created_at": info.get('created_at'),
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

