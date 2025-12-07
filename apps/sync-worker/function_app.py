"""Sync worker Function App.

Consumes messages from the ``github-sync`` queue, fetches repository context
using shared managers, and persists results to the cache. Once all repositories
for a job are processed the worker enqueues a merge job.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional

try:
    import azure.functions as func  # type: ignore
except ImportError:  # pragma: no cover - local/unit-test fallback
    class _QueueMessage:
        """Minimal stub mimicking azure.functions.QueueMessage."""

        def __init__(self, body: Any):
            self._body = body

        def get_body(self):  # type: ignore[override]
            return self._body

    class _FunctionApp:
        """No-op decorator provider for local runs/tests."""

        def queue_trigger(self, *_, **__):
            def _decorator(fn):
                return fn

            return _decorator

    func = type("func", (), {"QueueMessage": _QueueMessage, "FunctionApp": _FunctionApp})()  # type: ignore


if TYPE_CHECKING:  # pragma: no cover - typing helper
    from azure.functions import QueueMessage as AzureQueueMessage  # type: ignore
else:
    # At runtime, use the real type if available, otherwise fall back to Any
    try:
        from azure.functions import QueueMessage as AzureQueueMessage  # type: ignore
    except ImportError:
        AzureQueueMessage = Any

# Clean imports from installed cloudfolio-shared package
from cloudfolio_shared import (
    cache_manager,
    FingerprintManager,
    GitHubAPI,
    GitHubRepoManager,
    queue_manager,
    FileTypeAnalyzer,
    table_manager,
)
from cloudfolio_shared.table import RepoMetadataRow


logger = logging.getLogger('portfolio.api')
logger.setLevel(logging.INFO)
logger.propagate = True

app = func.FunctionApp()

JOB_METADATA_TTL_SECONDS = 4 * 3600
READ_ME_EXCERPT_MAX_CHARS = 4096


def _get_repo_manager(username: str) -> GitHubRepoManager:
    if not username:
        raise ValueError("Username required")
    token = os.getenv('GITHUB_TOKEN')
    api = GitHubAPI(token=token, username=username)
    return GitHubRepoManager(api, username=username)


def _deserialize_message(msg: func.QueueMessage) -> Dict[str, Any]:
    body_bytes = msg.get_body()
    body_str = body_bytes.decode('utf-8') if isinstance(body_bytes, (bytes, bytearray)) else str(body_bytes)
    payload = json.loads(body_str)
    logger.info("Sync worker received payload: job=%s repo=%s", payload.get('job_id'), payload.get('repo_name'))
    return payload


def _fetch_repo_bundle(job_id: str, username: str, repo_metadata: Dict[str, Any], fingerprint: Optional[str]) -> Dict[str, Any]:
    repo_manager = _get_repo_manager(username)
    repo_name = repo_metadata.get('name')
    if not repo_name:
        raise ValueError("Repository name missing in message")

    resolved_fingerprint = fingerprint or FingerprintManager.generate_metadata_fingerprint(repo_metadata)

    # Fetch standard content artifacts (tolerate missing files and network hiccups)
    try:
        readme_content = repo_manager.get_file_content(username=username, repo=repo_name, path='README.md') or ""
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Failed to fetch README for %s/%s: %s", username, repo_name, exc)
        readme_content = ""

    try:
        file_types = repo_manager.get_all_file_types(repo_name, username)
        analyzer = FileTypeAnalyzer()
        categorized_types = analyzer.analyze_repository_files(file_types)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Failed to fetch file types for %s/%s: %s", username, repo_name, exc)
        file_types = []
        categorized_types = {}

    result = {
        "name": repo_name,
        "metadata": repo_metadata,
        "readme": readme_content,
        "file_types": file_types,
        "categorized_types": categorized_types,
        "fingerprint": resolved_fingerprint,
        "languages": repo_metadata.get("languages", {}),
        "has_documentation": bool(readme_content),
    }

    cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo_name)
    cache_manager.save(cache_key, result, ttl=None, fingerprint=resolved_fingerprint)
    _persist_repo_metadata(job_id, username, result, cache_key)
    logger.info("Cached repo %s/%s fingerprint=%s", username, repo_name, resolved_fingerprint)
    return result


def _load_job_snapshot(job_id: str, username: str) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    if table_manager.is_enabled():
        table_row = table_manager.get_candidate_session(username, job_id)
        if table_row:
            snapshot = dict(table_row)
    if snapshot:
        return snapshot

    job_key = f"job:{job_id}"
    job_entry = cache_manager.get(job_key)
    if job_entry.get('status') == 'valid' and isinstance(job_entry.get('data'), dict):
        return dict(job_entry['data'])

    logger.warning("Job metadata missing for %s; initializing defaults", job_id)
    return {
        'job_id': job_id,
        'username': username,
        'synced_repos': [],
        'expected_repos': [],
        'queued_repos': [],
        'completed_repos': 0,
        'total_repos': 0,
        'status': 'queued',
    }


def _persist_repo_metadata(job_id: str, username: str, repo_payload: Dict[str, Any], content_blob: str) -> None:
    if not table_manager.is_enabled():
        return

    repo_name = repo_payload.get('name')
    if not repo_name:
        return

    document = {
        'name': repo_name,
        'metadata': repo_payload.get('metadata'),
        'file_types': repo_payload.get('file_types'),
        'categorized_types': repo_payload.get('categorized_types'),
        'fingerprint': repo_payload.get('fingerprint'),
        'has_documentation': repo_payload.get('has_documentation'),
    }

    row = RepoMetadataRow(
        username=username,
        repo_name=repo_name,
        fingerprint=repo_payload.get('fingerprint'),
        job_id=job_id,
        document=document,
        metadata=repo_payload.get('metadata', {}),
        content_blob=content_blob,
        languages=repo_payload.get('languages', {}),
        categorized_types=repo_payload.get('categorized_types', {}),
        has_documentation=repo_payload.get('has_documentation'),
        readme_excerpt=(repo_payload.get('readme') or '')[:READ_ME_EXCERPT_MAX_CHARS],
        last_synced_at=repo_payload.get('metadata', {}).get('updated_at') if isinstance(repo_payload.get('metadata'), dict) else None,
    )
    table_manager.upsert_repo_metadata(row)


def _update_job_progress(job_id: str, username: str, repo_name: str) -> None:
    job_info = _load_job_snapshot(job_id, username)
    synced = set(job_info.get('synced_repos', []))
    if repo_name:
        synced.add(repo_name)

    queued_repos = job_info.get('queued_repos') or []
    expected_repos = job_info.get('expected_repos') or []
    synced_list = sorted(synced)
    completed = len(synced_list)
    inferred_total = len(queued_repos) or len(expected_repos) or completed
    total_target = max(job_info.get('total_repos', 0), inferred_total, completed)

    updates = {
        'synced_repos': synced_list,
        'completed_repos': completed,
        'total_repos': total_target,
    }

    queued_set = set(queued_repos)
    should_merge = bool(queued_set) and queued_set.issubset(synced)
    if should_merge:
        updates['status'] = 'synced'

    if table_manager.is_enabled():
        table_manager.update_candidate_session(username, job_id, updates)

    job_info.update(updates)
    cache_manager.save(f"job:{job_id}", job_info, ttl=JOB_METADATA_TTL_SECONDS)
    logger.info("Job %s progress: %s/%s", job_id, job_info['completed_repos'], job_info['total_repos'])

    if should_merge:
        if queue_manager.is_enabled():
            queue_manager.enqueue_merge_job(job_id, username, synced_list)
            logger.info("Job %s enqueued for merge", job_id)
        else:
            logger.warning("Queue manager disabled; merge job not enqueued for %s", job_id)


@app.queue_trigger(arg_name="msg", queue_name="github-sync", connection="AzureWebJobsStorage")
def process_sync_job(msg: func.QueueMessage) -> None:
    """Process a single repository sync message."""
    logger.info("Sync worker processing message from github-sync queue")
    try:
        payload = _deserialize_message(msg)
        username = payload.get('username')
        job_id = payload.get('job_id')
        repo_metadata = payload.get('metadata') or {}
        repo_name = repo_metadata.get('name')

        if not username or not job_id or not repo_name:
            raise ValueError("username, job_id, and repo metadata are required")

        _fetch_repo_bundle(job_id, username, repo_metadata, payload.get('fingerprint'))
        _update_job_progress(job_id, username, repo_name)
    except Exception as exc:
        logger.error("Sync worker failure: %s", exc, exc_info=True)
        raise