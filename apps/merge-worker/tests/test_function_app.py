import json

import function_app as merge_app


def test_process_merge_payload_merges_cached_and_fresh(monkeypatch):
    payload = {"job_id": "job-1", "username": "tester", "synced_repos": ["repo-a"]}

    monkeypatch.setattr(merge_app.table_manager, "is_enabled", lambda: False)

    def fake_generate_cache_key(kind, username, repo=None):
        if kind == "bundle":
            return f"bundle:{username}"
        if kind == "repo":
            return f"repo:{username}:{repo}"
        return f"unknown:{kind}"

    monkeypatch.setattr(merge_app.cache_manager, "generate_cache_key", fake_generate_cache_key)

    def fake_get(key):
        if key == "repo:tester:repo-a":
            return {"status": "valid", "data": {"name": "repo-a", "fingerprint": "fa"}}
        if key == "bundle:tester":
            return {"status": "valid", "data": [{"name": "repo-b", "fingerprint": "fb"}]}
        if key == "job:job-1":
            return {"status": "valid", "data": {"job_id": "job-1", "total_repos": 2, "completed_repos": 1}}
        return {"status": "missing", "data": None}

    monkeypatch.setattr(merge_app.cache_manager, "get", fake_get)

    saves = []

    def fake_save(cache_key, data, ttl=None, fingerprint=None):
        saves.append((cache_key, json.loads(json.dumps(data)), ttl, fingerprint))
        return True

    monkeypatch.setattr(merge_app.cache_manager, "save", fake_save)
    monkeypatch.setattr(merge_app.queue_manager, "is_enabled", lambda: True)

    enqueued = []
    monkeypatch.setattr(
        merge_app.queue_manager,
        "enqueue_training_job",
        lambda username, bundle, training_params=None: enqueued.append((username, bundle, training_params)) or True,
    )

    merged = merge_app._process_merge_payload(payload)

    assert [repo["name"] for repo in merged] == ["repo-a", "repo-b"]
    assert saves[0][0] == "bundle:tester"
    assert saves[0][3]  # fingerprint saved with bundle
    assert saves[1][0] == "job:job-1"
    assert saves[1][1]["synced_repos"] == ["repo-a", "repo-b"]
    assert enqueued and enqueued[0][0] == "tester"
    assert len(enqueued[0][1]) == 2


def test_resolve_fresh_repos_prefers_payload(monkeypatch):
    payload = {
        "username": "tester",
        "fresh_repos": [
            {"name": "repo-inline", "fingerprint": "inline"},
        ],
    }

    def explode(*_args, **_kwargs):  # pragma: no cover - ensures cache path unused
        raise AssertionError("should not call cache hydrator")

    monkeypatch.setattr(merge_app, "_load_repos_from_cache", explode)
    monkeypatch.setattr(merge_app.table_manager, "is_enabled", lambda: False)

    fresh = merge_app._resolve_fresh_repos(payload, "tester", "job-1")

    assert fresh == payload["fresh_repos"]


def test_resolve_cached_bundle_falls_back_to_cache(monkeypatch):
    monkeypatch.setattr(merge_app, "_load_cached_bundle", lambda username, job_id: [{"name": "cached"}])
    monkeypatch.setattr(merge_app.table_manager, "is_enabled", lambda: False)

    result = merge_app._resolve_cached_bundle({}, "tester", "job-1")

    assert result == [{"name": "cached"}]


def test_process_merge_payload_handles_empty_data(monkeypatch):
    monkeypatch.setattr(merge_app, "_resolve_fresh_repos", lambda payload, username, job_id: [])
    monkeypatch.setattr(merge_app, "_resolve_cached_bundle", lambda payload, username, job_id: [])
    monkeypatch.setattr(merge_app.table_manager, "is_enabled", lambda: False)

    merged = merge_app._process_merge_payload({"job_id": "job-2", "username": "tester"})

    assert merged == []
