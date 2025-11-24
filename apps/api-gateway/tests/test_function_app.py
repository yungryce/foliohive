import uuid

import pytest

import function_app as gateway

from .conftest import FakeRequest


@pytest.fixture
def capture_success(monkeypatch):
    payload = {}

    def _capture(data, status_code=200, cache_control=None):
        payload['data'] = data
        payload['status_code'] = status_code
        payload['cache_control'] = cache_control
        return payload

    monkeypatch.setattr(gateway, "_create_success_response", _capture)
    return payload


@pytest.fixture
def capture_error(monkeypatch):
    payload = {}

    def _capture(message, status_code=500, details=None):
        payload['message'] = message
        payload['status_code'] = status_code
        payload['details'] = details
        return payload

    monkeypatch.setattr(gateway, "_create_error_response", _capture)
    return payload


def test_trigger_bundle_refresh_returns_cached_when_no_stale(monkeypatch, capture_success):
    monkeypatch.setattr(gateway, "_queue_mode_enabled", lambda: True)
    monkeypatch.setattr(
        gateway,
        "_identify_repo_freshness",
        lambda username: {"stale_repos": [], "cached_bundle": [{}], "bundle_status": "valid"},
    )

    request = FakeRequest(route_params={'username': 'tester'})
    response = gateway.trigger_bundle_refresh(request)

    assert response['status_code'] == 200
    assert capture_success['data']['status'] == 'cached'
    assert capture_success['data']['repos_count'] == 1


def test_trigger_bundle_refresh_enqueues_jobs(monkeypatch, capture_success):
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
        lambda job_id, username, repo_metadata, fingerprint=None: enqueued.append(repo_metadata['name']) or True,
    )

    job_records = {}

    def fake_persist(job_id, username, repo_names):
        job_records['job_id'] = job_id
        job_records['username'] = username
        job_records['repo_names'] = list(repo_names)

    monkeypatch.setattr(gateway, "_persist_job_metadata", fake_persist)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=1))

    request = FakeRequest(route_params={'username': 'tester'})
    response = gateway.trigger_bundle_refresh(request)

    assert response['status_code'] == 202
    assert set(enqueued) == {"repo-one", "repo-two"}
    assert job_records['repo_names'] == ["repo-one", "repo-two"]
    assert capture_success['data']['job_id'] == str(uuid.UUID(int=1))


def test_get_job_status_handles_missing_job(monkeypatch, capture_error):
    monkeypatch.setattr(gateway.cache_manager, "get", lambda key: {"status": "missing", "data": None})

    request = FakeRequest(params={'job_id': 'abc'})
    response = gateway.get_job_status(request)

    assert response['status_code'] == 404
    assert capture_error['message'] == "Job not found or expired"


def test_get_job_status_returns_progress(monkeypatch, capture_success):
    job_payload = {
        'status': 'valid',
        'data': {
            'job_id': 'abc',
            'username': 'tester',
            'total_repos': 4,
            'completed_repos': 2,
            'status': 'queued',
            'created_at': '2025-11-24T00:00:00Z',
        },
    }
    monkeypatch.setattr(gateway.cache_manager, "get", lambda key: job_payload)

    request = FakeRequest(params={'job_id': 'abc'})
    response = gateway.get_job_status(request)

    assert response['status_code'] == 200
    assert capture_success['data']['progress']['percentage'] == 50
    assert capture_success['data']['status'] == 'queued'