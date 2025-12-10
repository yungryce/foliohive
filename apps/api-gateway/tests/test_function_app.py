import json
import uuid

import function_app as gateway

from .conftest import FakeRequest

def test_trigger_bundle_refresh_returns_cached_when_no_stale(monkeypatch):
    monkeypatch.setattr(gateway, "_queue_mode_enabled", lambda: True)
    monkeypatch.setattr(
        gateway,
        "_identify_repo_freshness",
        lambda username: {"stale_repos": [], "cached_bundle": [{}], "bundle_status": "valid"},
    )

    request = FakeRequest(route_params={'username': 'tester'})
    response = gateway.trigger_bundle_refresh(request)
    payload = json.loads(response.get_body())

    assert response.status_code == 200
    assert payload['status'] == 'cached'
    assert payload['repos_count'] == 1


def test_trigger_bundle_refresh_enqueues_jobs(monkeypatch):
    monkeypatch.setattr(gateway, "_queue_mode_enabled", lambda: True)
    monkeypatch.setattr(
        gateway,
        "_identify_repo_freshness",
        lambda username: {
            "stale_repos": [
                {"name": "repo-one", "fingerprint": "a"},
                {"name": "repo-two", "fingerprint": "b"},
            ],
            "cached_bundle": [],
            "bundle_status": "missing",
        },
    )

    enqueued = []
    monkeypatch.setattr(
        gateway.queue_manager,
        "enqueue_sync_job",
        lambda job_id, username, repo_name, fingerprint=None: enqueued.append(repo_name) or True,
    )

    stored_sessions = {}

    def fake_is_enabled() -> bool:
        return True

    monkeypatch.setattr(gateway.table_manager, "is_enabled", fake_is_enabled)

    def fake_get_session(username: str, job_id: str):
        return stored_sessions.get((username, job_id))

    def fake_upsert(row):
        stored_sessions[(row.username, row.job_id)] = {
            'PartitionKey': row.username,
            'RowKey': row.job_id,
            'expected_repos': list(row.expected_repos),
            'queued_repos': list(row.queued_repos),
            'synced_repos': list(row.synced_repos),
            'status': row.status,
            'total_repos': row.total_repos,
            'completed_repos': row.completed_repos,
            'bundle_fingerprint': row.bundle_fingerprint,
            'force_refresh': row.force_refresh,
            'model_status': row.model_status,
            'model_fingerprint': row.model_fingerprint,
            'created_at': row.created_at,
            'updated_at': row.updated_at,
        }

    monkeypatch.setattr(gateway.table_manager, "get_candidate_session", fake_get_session)
    monkeypatch.setattr(gateway.table_manager, "upsert_candidate_session", fake_upsert)

    cache_records = {}

    def fake_cache_save(key, payload, ttl=None, fingerprint=None):
        cache_records['key'] = key
        cache_records['payload'] = payload
        cache_records['ttl'] = ttl
        cache_records['fingerprint'] = fingerprint
        return True

    monkeypatch.setattr(gateway.cache_manager, "save", fake_cache_save)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=1))

    request = FakeRequest(route_params={'username': 'tester'})
    response = gateway.trigger_bundle_refresh(request)
    payload = json.loads(response.get_body())

    job_id = str(uuid.UUID(int=1))
    assert response.status_code == 202
    assert set(enqueued) == {"repo-one", "repo-two"}
    assert cache_records['payload']['queued_repos'] == ["repo-one", "repo-two"]
    assert stored_sessions[("tester", job_id)]['queued_repos'] == ["repo-one", "repo-two"]
    assert payload['job_id'] == job_id


def test_get_job_status_handles_missing_job(monkeypatch):
    monkeypatch.setattr(gateway.table_manager, "is_enabled", lambda: True)
    monkeypatch.setattr(gateway.table_manager, "get_candidate_session", lambda username, job_id: None)
    monkeypatch.setattr(gateway.cache_manager, "get", lambda key: {"status": "missing", "data": None})

    request = FakeRequest(route_params={'username': 'tester'}, params={'job_id': 'abc'})
    response = gateway.get_job_status(request)
    payload = json.loads(response.get_body())

    assert response.status_code == 404
    assert payload['error'] == "Job not found or expired"


def test_get_job_status_returns_progress(monkeypatch):
    session = {
        'PartitionKey': 'tester',
        'RowKey': 'abc',
        'expected_repos': ['repo-one', 'repo-two'],
        'queued_repos': ['repo-one', 'repo-two'],
        'synced_repos': ['repo-one'],
        'status': 'queued',
        'total_repos': 2,
        'completed_repos': 1,
        'created_at': '2025-11-24T00:00:00Z',
        'updated_at': '2025-11-24T01:00:00Z',
    }
    monkeypatch.setattr(gateway.table_manager, "is_enabled", lambda: True)
    monkeypatch.setattr(gateway.table_manager, "get_candidate_session", lambda username, job_id: session)
    monkeypatch.setattr(gateway.cache_manager, "get", lambda key: {"status": "missing", "data": None})

    request = FakeRequest(route_params={'username': 'tester'}, params={'job_id': 'abc'})
    response = gateway.get_job_status(request)
    payload = json.loads(response.get_body())

    assert response.status_code == 200
    assert payload['progress']['percentage'] == 50
    assert payload['status'] == 'queued'


def test_get_repo_bundle_prefers_table(monkeypatch):
    session = {
        'PartitionKey': 'tester',
        'RowKey': 'job-1',
        'status': 'completed',
        'bundle_fingerprint': 'bundle-fp',
        'expected_repos': ['repo-one'],
        'queued_repos': ['repo-one'],
        'synced_repos': ['repo-one'],
        'updated_at': '2025-12-01T00:00:00Z',
    }
    repo_row = {
        'PartitionKey': 'tester',
        'RowKey': 'repo-one',
        'document': {
            'name': 'repo-one',
            'metadata': {'name': 'repo-one'},
            'fingerprint': 'fp-1',
            'languages': {'Python': 3},
            'categorized_types': {'code': 3},
        },
        'fingerprint': 'fp-1',
        'languages': {'Python': 3},
        'categorized_types': {'code': 3},
        'has_documentation': True,
        'readme_excerpt': 'docs',
        'updated_at': '2025-12-01T00:00:00Z',
    }

    monkeypatch.setattr(gateway.table_manager, "is_enabled", lambda: True)
    monkeypatch.setattr(gateway.table_manager, "get_candidate_session", lambda username, job_id: None)
    monkeypatch.setattr(gateway.table_manager, "list_candidate_sessions", lambda username: [session])
    monkeypatch.setattr(gateway.table_manager, "query_repo_metadata", lambda username, job_id=None, repo_names=None: [repo_row])
    monkeypatch.setattr(gateway.cache_manager, "get", lambda key: {'status': 'missing', 'data': None})

    request = FakeRequest(route_params={'username': 'tester'})
    response = gateway.get_repo_bundle(request)
    payload = json.loads(response.get_body())

    assert response.status_code == 200
    assert payload['fingerprint'] == 'bundle-fp'
    assert payload['data'][0]['name'] == 'repo-one'