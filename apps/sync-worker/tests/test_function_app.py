import json

import function_app as sync_app


class FakeMessage:
    def __init__(self, body):
        self._body = body

    def get_body(self):
        return self._body


def test_deserialize_message_handles_bytes():
    payload = {"job_id": "job-1", "repo_name": "demo", "username": "tester"}
    message = FakeMessage(json.dumps(payload).encode("utf-8"))

    result = sync_app._deserialize_message(message)

    assert result["job_id"] == "job-1"
    assert result["username"] == "tester"


def test_fetch_repo_bundle_caches_and_returns_expected(monkeypatch):
    repo_metadata = {"name": "demo", "id": 123, "languages": {"Python": 100}}

    class FakeRepoManager:
        def get_file_content(self, username, repo, path):
            mapping = {
                ".repo-context.json": json.dumps({"context": "data"}),
                "README.md": "# Demo",
                "SKILLS-INDEX.md": "Python",
                "ARCHITECTURE.md": "Layers",
            }
            return mapping.get(path, "")

        def get_all_file_types(self, repo_name, username):
            return {".py": 3}

    monkeypatch.setattr(sync_app, "_get_repo_manager", lambda username: FakeRepoManager())
    monkeypatch.setattr(
        sync_app.FingerprintManager,
        "generate_metadata_fingerprint",
        staticmethod(lambda metadata: "fingerprint-value"),
    )
    monkeypatch.setattr(
        sync_app.FileTypeAnalyzer,
        "analyze_repository_files",
        lambda self, file_types: {"detected": list(file_types.keys())},
    )

    captured = {}

    def fake_save(cache_key, data, ttl=None, fingerprint=None):
        captured["cache_key"] = cache_key
        captured["data"] = data
        captured["ttl"] = ttl
        captured["fingerprint"] = fingerprint
        return True

    monkeypatch.setattr(sync_app.cache_manager, "generate_cache_key", lambda **kwargs: "repo:demo")
    monkeypatch.setattr(sync_app.cache_manager, "save", fake_save)

    result = sync_app._fetch_repo_bundle("tester", repo_metadata, None)

    assert result["name"] == "demo"
    assert result["fingerprint"] == "fingerprint-value"
    assert result["repoContext"] == {"context": "data"}
    assert result["categorized_types"] == {"detected": [".py"]}
    assert captured["cache_key"] == "repo:demo"
    assert captured["fingerprint"] == "fingerprint-value"


def test_update_job_progress_tracks_completion(monkeypatch):
    job_state = {
        "status": "valid",
        "data": {
            "synced_repos": [],
            "completed_repos": 0,
            "total_repos": 1,
        },
    }

    monkeypatch.setattr(sync_app.cache_manager, "get", lambda key: job_state)

    saves = []

    def fake_save(cache_key, data, ttl=None, fingerprint=None):
        saves.append((cache_key, data.copy()))
        return True

    monkeypatch.setattr(sync_app.cache_manager, "save", fake_save)
    monkeypatch.setattr(sync_app.queue_manager, "is_enabled", lambda: True)

    merge_jobs = []
    monkeypatch.setattr(
        sync_app.queue_manager,
        "enqueue_merge_job",
        lambda job_id, username, repos: merge_jobs.append((job_id, username, list(repos))),
    )

    sync_app._update_job_progress("job-123", "tester", "demo")

    assert len(saves) == 2
    assert merge_jobs == [("job-123", "tester", ["demo"])]
    assert job_state["data"]["status"] == "synced"


def test_process_sync_job_invokes_handlers(monkeypatch):
    payload = {
        "job_id": "job-456",
        "username": "tester",
        "metadata": {"name": "demo"},
        "fingerprint": "abc",
    }
    message = FakeMessage(json.dumps(payload).encode("utf-8"))

    calls = {}
    monkeypatch.setattr(
        sync_app,
        "_fetch_repo_bundle",
        lambda username, repo_metadata, fingerprint: calls.setdefault(
            "fetch", (username, repo_metadata.copy(), fingerprint)
        ),
    )
    monkeypatch.setattr(
        sync_app,
        "_update_job_progress",
        lambda job_id, username, repo_name: calls.setdefault("progress", (job_id, username, repo_name)),
    )

    sync_app.process_sync_job(message)

    assert calls["fetch"] == ("tester", {"name": "demo"}, "abc")
    assert calls["progress"] == ("job-456", "tester", "demo")