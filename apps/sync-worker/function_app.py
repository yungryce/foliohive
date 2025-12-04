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
)


logger = logging.getLogger('portfolio.api')
logger.setLevel(logging.INFO)
logger.propagate = True

app = func.FunctionApp()


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


def _fetch_repo_bundle(username: str, repo_metadata: Dict[str, Any], fingerprint: Optional[str]) -> Dict[str, Any]:
    repo_manager = _get_repo_manager(username)
    repo_name = repo_metadata.get('name')
    if not repo_name:
        raise ValueError("Repository name missing in message")

    resolved_fingerprint = fingerprint or FingerprintManager.generate_metadata_fingerprint(repo_metadata)

    # Fetch standard content artifacts
    repo_context_raw = repo_manager.get_file_content(username=username, repo=repo_name, path='.repo-context.json')
    repo_context = json.loads(repo_context_raw) if repo_context_raw else {}
    readme_content = repo_manager.get_file_content(username=username, repo=repo_name, path='README.md') or ""
    skills_index = repo_manager.get_file_content(username=username, repo=repo_name, path='SKILLS-INDEX.md') or ""
    architecture = repo_manager.get_file_content(username=username, repo=repo_name, path='ARCHITECTURE.md') or ""

    file_types = repo_manager.get_all_file_types(repo_name, username)
    analyzer = FileTypeAnalyzer()
    categorized_types = analyzer.analyze_repository_files(file_types)

    result = {
        "name": repo_name,
        "metadata": repo_metadata,
        "repoContext": repo_context,
        "readme": readme_content,
        "skills_index": skills_index,
        "architecture": architecture,
        "file_types": file_types,
        "categorized_types": categorized_types,
        "fingerprint": resolved_fingerprint,
        "languages": repo_metadata.get("languages", {}),
        "has_documentation": bool(repo_context) and bool(readme_content),
    }

    cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo_name)
    cache_manager.save(cache_key, result, ttl=None, fingerprint=resolved_fingerprint)
    logger.info("Cached repo %s/%s fingerprint=%s", username, repo_name, resolved_fingerprint)
    return result


def _update_job_progress(job_id: str, username: str, repo_name: str) -> None:
    job_key = f"job:{job_id}"
    job_entry = cache_manager.get(job_key)
    if job_entry.get('status') != 'valid' or not isinstance(job_entry.get('data'), dict):
        logger.warning("Job metadata not found for job %s", job_id)
        return

    job_info = job_entry['data']
    synced = set(job_info.get('synced_repos', []))
    if repo_name:
        synced.add(repo_name)
    job_info['synced_repos'] = sorted(synced)
    job_info['completed_repos'] = len(synced)

    total = job_info.get('total_repos', len(synced))
    job_info.setdefault('total_repos', total)

    cache_manager.save(job_key, job_info, ttl=3600)
    logger.info("Job %s progress: %s/%s", job_id, job_info['completed_repos'], job_info['total_repos'])

    should_merge = job_info['completed_repos'] >= job_info['total_repos'] > 0
    if should_merge:
        job_info['status'] = 'synced'
        cache_manager.save(job_key, job_info, ttl=3600)
        if queue_manager.is_enabled():
            queue_manager.enqueue_merge_job(job_id, username, job_info['synced_repos'])
            logger.info("Job %s enqueued for merge", job_id)
        else:
            logger.warning("Queue manager disabled; merge job not enqueued for %s", job_id)


@app.queue_trigger(arg_name="msg", queue_name="github-sync", connection="AzureWebJobsStorage")
def process_sync_job(msg: func.QueueMessage) -> None:
    """Process a single repository sync message."""
    import traceback
    print("[SYNC] Starting message processing...")
    
    try:
        # Step 1: Deserialize message
        print("[SYNC] Step 1: Deserializing message...")
        logger.info("Sync worker processing message from github-sync queue")
        payload = _deserialize_message(msg)
        username = payload.get('username')
        job_id = payload.get('job_id')
        repo_metadata = payload.get('metadata') or {}
        repo_name = repo_metadata.get('name')
        print(f"[SYNC] Parsed: username={username}, job_id={job_id}, repo={repo_name}")

        if not username or not job_id or not repo_name:
            raise ValueError("username, job_id, and repo metadata are required")

        # Step 2: Fetch repo bundle
        print(f"[SYNC] Step 2: Fetching repo bundle for {username}/{repo_name}...")
        _fetch_repo_bundle(username, repo_metadata, payload.get('fingerprint'))
        print(f"[SYNC] Step 2 complete: repo bundle fetched")
        
        # Step 3: Update job progress
        print(f"[SYNC] Step 3: Updating job progress for {job_id}...")
        _update_job_progress(job_id, username, repo_name)
        print(f"[SYNC] Step 3 complete: job progress updated")
        
        print(f"[SYNC] SUCCESS: Processed {username}/{repo_name}")
        
    except Exception as exc:
        error_tb = traceback.format_exc()
        print(f"[SYNC] ERROR: {type(exc).__name__}: {exc}")
        print(f"[SYNC] TRACEBACK:\n{error_tb}")
        logger.error("Sync worker failure: %s", exc, exc_info=True)
        raise