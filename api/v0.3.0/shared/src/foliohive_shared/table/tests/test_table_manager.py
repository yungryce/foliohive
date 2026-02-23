"""Specification-aligned tests for TableManager.

These tests cover the current normalized table schema + the MVP additions used
by the candidate profile page (UserProfile).
"""

from __future__ import annotations

import pytest

from azure.core.exceptions import ResourceNotFoundError

from foliohive_shared.table.table_manager import (
    JobMetadataRow,
    RepoAPIUsageRow,
    RepoDiscoveredPathsRow,
    RepoGitHubMetadataRow,
    RepoLanguagesRow,
    RepoSyncStatusRow,
    TableManager,
    TableNames,
    UserProfileRow,
    _azure_safe_timestamp,
    _restore_iso_timestamp,
    _safe_json_dump_limited,
)


class _FakeTableClient:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}

    # azure.data.tables compatibility -------------------------------------------------
    def create_table(self) -> None:  # pragma: no cover
        return None

    def upsert_entity(self, entity: dict, mode=None) -> None:
        key = (entity["PartitionKey"], entity["RowKey"])
        stored = dict(self.entities.get(key, {}))
        stored.update(entity)
        self.entities[key] = stored

    def get_entity(self, partition_key: str, row_key: str) -> dict:
        key = (partition_key, row_key)
        if key not in self.entities:
            raise ResourceNotFoundError("entity not found")
        return dict(self.entities[key])

    def delete_entity(self, partition_key: str, row_key: str) -> None:
        key = (partition_key, row_key)
        if key not in self.entities:
            raise ResourceNotFoundError("entity not found")
        del self.entities[key]

    def list_entities(self, *args, **kwargs):  # type: ignore[override]
        filter_str = kwargs.get("filter")
        if filter_str is None and args:
            filter_str = args[0]
        select = kwargs.get("select")

        for entity in self.entities.values():
            if self._matches_filter(entity, filter_str or ""):
                if select:
                    yield {k: entity.get(k) for k in select}
                else:
                    yield dict(entity)

    def query_entities(self, query_filter: str, *, _results_per_page=None, **_kwargs):
        yield from self.list_entities(query_filter)

    # filtering helpers ------------------------------------------------------------
    @staticmethod
    def _extract_eq_values(filter_str: str, field: str) -> set[str]:
        token = f"{field} eq '"
        start = 0
        values: set[str] = set()
        while True:
            idx = filter_str.find(token, start)
            if idx == -1:
                break
            start_val = idx + len(token)
            end_val = filter_str.find("'", start_val)
            if end_val == -1:
                break
            values.add(filter_str[start_val:end_val])
            start = end_val + 1
        return values

    @staticmethod
    def _extract_lt_value(filter_str: str, field: str) -> str | None:
        token = f"{field} lt '"
        idx = filter_str.find(token)
        if idx == -1:
            return None
        start_val = idx + len(token)
        end_val = filter_str.find("'", start_val)
        if end_val == -1:
            return None
        return filter_str[start_val:end_val]

    @staticmethod
    def _extract_ge_value(filter_str: str, field: str) -> str | None:
        token = f"{field} ge '"
        idx = filter_str.find(token)
        if idx == -1:
            return None
        start_val = idx + len(token)
        end_val = filter_str.find("'", start_val)
        if end_val == -1:
            return None
        return filter_str[start_val:end_val]

    def _matches_filter(self, entity: dict, filter_str: str) -> bool:
        if not filter_str:
            return True

        partition_values = self._extract_eq_values(filter_str, "PartitionKey")
        if partition_values and entity.get("PartitionKey") not in partition_values:
            return False

        partition_ge = self._extract_ge_value(filter_str, "PartitionKey")
        if partition_ge and str(entity.get("PartitionKey") or "") < partition_ge:
            return False

        partition_lt = self._extract_lt_value(filter_str, "PartitionKey")
        if partition_lt and str(entity.get("PartitionKey") or "") >= partition_lt:
            return False

        row_key_values = self._extract_eq_values(filter_str, "RowKey")
        if row_key_values and entity.get("RowKey") not in row_key_values:
            return False

        for field in ("repo_name", "job_id", "operation"):
            values = self._extract_eq_values(filter_str, field)
            if values and entity.get(field) not in values:
                return False

        status_values = self._extract_eq_values(filter_str, "status")
        if status_values and entity.get("status") not in status_values:
            return False

        updated_before = self._extract_lt_value(filter_str, "updated_at")
        if updated_before:
            updated_at = entity.get("updated_at")
            if not updated_at or str(updated_at) >= updated_before:
                return False

        created_before = self._extract_lt_value(filter_str, "created_at")
        if created_before:
            created_at = entity.get("created_at")
            if not created_at or str(created_at) >= created_before:
                return False

        return True


class _FakeServiceClient:
    def __init__(self) -> None:
        self.tables: dict[str, _FakeTableClient] = {}

    def get_table_client(self, name: str) -> _FakeTableClient:
        if name not in self.tables:
            self.tables[name] = _FakeTableClient()
        return self.tables[name]


@pytest.fixture(name="table_manager")
def _table_manager() -> TableManager:
    service = _FakeServiceClient()
    return TableManager(table_service_client=service, table_names=TableNames())


def test_timestamp_helpers_roundtrip() -> None:
    iso = "2026-01-25T10:05:00+00:00"
    safe = _azure_safe_timestamp(iso)
    assert ":" not in safe
    assert "+" not in safe
    assert _restore_iso_timestamp(safe) == iso


def test_safe_json_dump_limited_drops_oversized() -> None:
    big = {"x": "y" * 50000}
    assert _safe_json_dump_limited(big, max_chars=1000) == "{}"


def test_job_metadata_roundtrip_and_partial_update(table_manager: TableManager) -> None:
    table_manager.upsert_job_metadata(
        JobMetadataRow(username="alice", job_id="job-1", status="queued", force_refresh=False)
    )

    with pytest.raises(ValueError):
        table_manager.update_job_metadata("alice", "missing", {"status": "completed"})

    table_manager.update_job_metadata(
        "alice",
        "job-1",
        {"status": "metadata_ready", "bundle_fingerprint": "fp_abc", "trace_id": "t1"},
    )

    stored = table_manager.get_job_metadata("alice", "job-1")
    assert stored is not None
    assert stored["status"] == "metadata_ready"
    assert stored["bundle_fingerprint"] == "fp_abc"
    assert stored["trace_id"] == "t1"
    assert stored["force_refresh"] is False

    jobs = table_manager.list_jobs_metadata("alice")
    assert len(jobs) == 1


def test_job_metadata_timestamp_storage_and_restore(table_manager: TableManager) -> None:
    created_at = "2026-02-01T10:00:00+00:00"
    last_requeue_at = "2026-02-01T10:05:00+00:00"
    completed_at = "2026-02-01T11:00:00+00:00"

    table_manager.upsert_job_metadata(
        JobMetadataRow(
            username="alice",
            job_id="job-2",
            status="queued",
            created_at=created_at,
            last_requeue_at=last_requeue_at,
        )
    )
    table_manager.update_job_metadata("alice", "job-2", {"completed_at": completed_at})

    raw = table_manager._get_table_client(table_manager.table_names.job_metadata).entities[("alice", "job-2")]
    assert ":" not in raw["created_at"]
    assert "+" not in raw["created_at"]
    assert ":" not in raw["last_requeue_at"]
    assert "+" not in raw["last_requeue_at"]
    assert ":" not in raw["completed_at"]
    assert "+" not in raw["completed_at"]

    fetched = table_manager.get_job_metadata("alice", "job-2")
    assert fetched is not None
    assert fetched["created_at"] == created_at
    assert fetched["last_requeue_at"] == last_requeue_at
    assert fetched["completed_at"] == completed_at


def test_session_candidate_fk_validation_and_counter(table_manager: TableManager) -> None:
    table_manager.upsert_job_metadata(JobMetadataRow(username="alice", job_id="job-1"))

    with pytest.raises(ValueError):
        table_manager.upsert_session_candidate("s-1", "alice", "missing-job")

    table_manager.upsert_session_candidate("s-1", "alice", "job-1")
    table_manager.upsert_session_candidate("s-1", "alice", "job-1")

    listed = table_manager.list_session_candidates("s-1")
    assert len(listed) == 1
    assert listed[0]["session_id"] == "s-1"
    assert listed[0]["username"] == "alice"
    assert listed[0]["latest_job_id"] == "job-1"
    assert listed[0]["query_count"] == 2


def test_repo_github_metadata_topics_and_timestamp_restore(table_manager: TableManager) -> None:
    table_manager.upsert_repo_github_metadata(
        RepoGitHubMetadataRow(
            username="alice",
            repo_name="api",
            fingerprint="fp_1",
            description="API repo",
            topics=["azure", "functions"],
            stars_count=12,
            forks_count=3,
            github_updated_at="2026-01-25T08:45:00Z",
        )
    )

    fetched = table_manager.get_repo_github_metadata("alice", "api")
    assert fetched is not None
    assert fetched["fingerprint"] == "fp_1"
    assert fetched["topics"] == ["azure", "functions"]
    assert fetched["stars_count"] == 12
    assert fetched["forks_count"] == 3
    assert fetched["github_updated_at"] == "2026-01-25T08:45:00Z"

    all_rows = table_manager.query_repo_github_metadata("alice")
    assert len(all_rows) == 1


def test_repo_sync_status_validation_and_updates(table_manager: TableManager) -> None:
    with pytest.raises(ValueError):
        table_manager.upsert_repo_status(
            RepoSyncStatusRow(job_id="job-1", repo_name="api", username="alice", status="BOGUS")
        )

    table_manager.upsert_repo_status(
        RepoSyncStatusRow(job_id="job-1", repo_name="api", username="alice", status="pending")
    )

    with pytest.raises(ValueError):
        table_manager.update_repo_status("job-1", "missing", {"status": "synced"})

    with pytest.raises(ValueError):
        table_manager.update_repo_status("job-1", "api", {"status": "INVALID"})

    table_manager.update_repo_status("job-1", "api", {"status": "synced", "synced_at": "t"})
    fetched = table_manager.get_repo_status("job-1", "api")
    assert fetched is not None
    assert fetched["status"] == "synced"
    assert fetched["synced_at"] == "t"


def test_repo_sync_status_timestamp_storage_and_restore(table_manager: TableManager) -> None:
    synced_at = "2026-02-02T09:00:00+00:00"
    cached_at = "2026-02-02T10:00:00+00:00"

    table_manager.upsert_repo_status(
        RepoSyncStatusRow(job_id="job-2", repo_name="api", username="alice", status="pending")
    )
    table_manager.update_repo_status("job-2", "api", {"status": "synced", "synced_at": synced_at})
    table_manager.update_repo_status("job-2", "api", {"status": "cached", "cached_at": cached_at})

    raw = table_manager._get_table_client(table_manager.table_names.repo_sync_status).entities[("job-2", "api")]
    assert ":" not in raw["synced_at"]
    assert "+" not in raw["synced_at"]
    assert ":" not in raw["cached_at"]
    assert "+" not in raw["cached_at"]

    fetched = table_manager.get_repo_status("job-2", "api")
    assert fetched is not None
    assert fetched["synced_at"] == synced_at
    assert fetched["cached_at"] == cached_at


def test_repo_languages_query_delete_and_cleanup(table_manager: TableManager) -> None:
    job_id = "job-1"
    table_manager.upsert_repo_languages(
        RepoLanguagesRow(
            job_id=job_id,
            repo_name="api",
            language="Python",
            bytes_count=5000,
            percentage=75.0,
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    table_manager.upsert_repo_languages(
        RepoLanguagesRow(
            job_id=job_id,
            repo_name="api",
            language="TypeScript",
            bytes_count=1500,
            percentage=25.0,
            created_at="2026-02-01T00:00:00+00:00",
        )
    )

    by_repo = table_manager.query_repo_languages(job_id)
    assert "api" in by_repo
    assert len(by_repo["api"]) == 2

    single_repo = table_manager.get_repo_languages(job_id, "api")
    assert len(single_repo) == 2
    assert sorted(lang["language"] for lang in single_repo) == ["Python", "TypeScript"]

    table_manager.delete_repo_languages(job_id, "api")
    assert table_manager.query_repo_languages(job_id).get("api") is None

    # cleanup: insert 2 old, 1 new
    table_manager.upsert_repo_languages(
        RepoLanguagesRow(
            job_id=job_id,
            repo_name="web",
            language="Python",
            bytes_count=1,
            created_at="2020-01-01T00:00:00+00:00",
        )
    )
    table_manager.upsert_repo_languages(
        RepoLanguagesRow(
            job_id=job_id,
            repo_name="web",
            language="Go",
            bytes_count=1,
            created_at="2020-01-01T00:00:00+00:00",
        )
    )
    table_manager.upsert_repo_languages(
        RepoLanguagesRow(
            job_id=job_id,
            repo_name="web",
            language="Rust",
            bytes_count=1,
            created_at="2026-02-01T00:00:00+00:00",
        )
    )

    deleted = table_manager.cleanup_old_repo_languages("2021-01-01T00:00:00+00:00")
    assert deleted == 2


def test_repo_languages_timestamp_restore(table_manager: TableManager) -> None:
    created_at = "2026-02-03T12:00:00+00:00"

    table_manager.upsert_repo_languages(
        RepoLanguagesRow(
            job_id="job-3",
            repo_name="api",
            language="Python",
            bytes_count=100,
            created_at=created_at,
        )
    )

    raw = table_manager._get_table_client(table_manager.table_names.repo_languages).entities[("job-3|api", "Python")]
    assert ":" not in raw["created_at"]
    assert "+" not in raw["created_at"]

    fetched = table_manager.query_repo_languages("job-3")["api"][0]
    assert fetched["created_at"] == created_at


def test_repo_api_usage_filters(table_manager: TableManager) -> None:
    table_manager.upsert_api_usage(
        RepoAPIUsageRow(
            username="alice",
            operation_key="freshness|2026-01-01|repo-a",
            operation="freshness_check",
            repo_name="repo-a",
            api_calls_rest=1,
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    table_manager.upsert_api_usage(
        RepoAPIUsageRow(
            username="alice",
            operation_key="metadata|2026-01-02|repo-a",
            operation="metadata_sync",
            job_id="job-1",
            repo_name="repo-a",
            api_calls_rest=3,
            cache_hits=2,
            created_at="2026-01-02T00:00:00+00:00",
        )
    )

    all_usage = table_manager.list_api_usage("alice")
    assert len(all_usage) == 2

    job_usage = table_manager.list_api_usage("alice", job_id="job-1")
    assert len(job_usage) == 1
    assert job_usage[0]["operation"] == "metadata_sync"

    op_usage = table_manager.list_api_usage("alice", operation="freshness_check")
    assert len(op_usage) == 1
    assert op_usage[0]["api_calls_rest"] == 1


def test_repo_api_usage_timestamp_restore(table_manager: TableManager) -> None:
    created_at = "2026-02-04T07:30:00+00:00"
    rate_limit_reset = "2026-02-04T08:00:00+00:00"

    table_manager.upsert_api_usage(
        RepoAPIUsageRow(
            username="alice",
            operation_key="meta|2026-02-04|repo-a",
            operation="metadata_sync",
            job_id="job-1",
            repo_name="repo-a",
            api_calls_rest=2,
            created_at=created_at,
            rate_limit_reset=rate_limit_reset,
        )
    )

    raw = table_manager._get_table_client(table_manager.table_names.repo_api_usage).entities[("alice", "meta|2026-02-04|repo-a")]
    assert ":" not in raw["created_at"]
    assert "+" not in raw["created_at"]
    assert ":" not in raw["rate_limit_reset"]
    assert "+" not in raw["rate_limit_reset"]

    fetched = table_manager.list_api_usage("alice", operation="metadata_sync")
    assert fetched[0]["created_at"] == created_at
    assert fetched[0]["rate_limit_reset"] == rate_limit_reset


class TestRepoDiscoveredPaths:
    """Test RepoDiscoveredPathsRow CRUD and cleanup queries."""

    def test_upsert_discovered_path_with_extraction_metadata(self, table_manager: TableManager) -> None:
        table_manager.upsert_repo_discovered_paths(
            RepoDiscoveredPathsRow(
                username="alice",
                repo_name="api",
                fingerprint="fp-1",
                discovered_paths=["README.md", "package.json"],
                readme_paths=["README.md"],
                config_paths=["package.json"],
                extraction_metadata={
                    "package.json": {
                        "extractor_key": "_extract_package_json",
                        "extraction_status": "extracted",
                    }
                },
            )
        )

        fetched = table_manager.get_repo_discovered_paths("alice", "api")
        assert fetched is not None
        assert fetched["fingerprint"] == "fp-1"
        assert fetched["discovered_paths"] == ["README.md", "package.json"]
        assert fetched["readme_paths"] == ["README.md"]
        assert fetched["config_paths"] == ["package.json"]
        assert fetched["extraction_metadata"]["package.json"]["extraction_status"] == "extracted"

    def test_query_discovered_paths_by_repo_and_fingerprint(self, table_manager: TableManager) -> None:
        table_manager.upsert_repo_discovered_paths(
            RepoDiscoveredPathsRow(
                username="alice",
                repo_name="worker",
                fingerprint="fp-query",
                discovered_paths=["Dockerfile"],
                config_paths=["Dockerfile"],
            )
        )

        fetched = table_manager.get_repo_discovered_paths("alice", "worker")
        assert fetched is not None
        assert fetched["repo_name"] == "worker"
        assert fetched["fingerprint"] == "fp-query"

    def test_delete_stale_paths_for_repo(self, table_manager: TableManager) -> None:
        table_manager.upsert_repo_discovered_paths(
            RepoDiscoveredPathsRow(
                username="alice",
                repo_name="stale-repo",
                fingerprint="fp-old",
                discovered_paths=["README.md"],
            )
        )
        table_manager.upsert_repo_github_metadata(
            RepoGitHubMetadataRow(
                username="alice",
                repo_name="stale-repo",
                fingerprint="fp-new",
            )
        )

        deleted = table_manager.cleanup_old_discovered_paths("2100-01-01T00:00:00+00:00")
        assert deleted == 1
        assert table_manager.get_repo_discovered_paths("alice", "stale-repo") is None

    def test_extraction_status_transitions(self, table_manager: TableManager) -> None:
        table_manager.upsert_repo_discovered_paths(
            RepoDiscoveredPathsRow(
                username="alice",
                repo_name="etl",
                fingerprint="fp-status",
                discovered_paths=["pyproject.toml"],
                config_paths=["pyproject.toml"],
                extraction_metadata={
                    "pyproject.toml": {
                        "extractor_key": "_extract_pyproject_toml",
                        "extraction_status": "pending",
                    }
                },
            )
        )

        table_manager.update_repo_discovered_path_extraction_status(
            "alice",
            "etl",
            "pyproject.toml",
            extraction_status="extracted",
            extractor_key="_extract_pyproject_toml",
        )
        extracted = table_manager.get_repo_discovered_paths("alice", "etl")
        assert extracted is not None
        assert extracted["extraction_metadata"]["pyproject.toml"]["extraction_status"] == "extracted"

        table_manager.update_repo_discovered_path_extraction_status(
            "alice",
            "etl",
            "pyproject.toml",
            extraction_status="failed",
            extractor_key="_extract_pyproject_toml",
            error="invalid_toml",
        )
        failed = table_manager.get_repo_discovered_paths("alice", "etl")
        assert failed is not None
        entry = failed["extraction_metadata"]["pyproject.toml"]
        assert entry["extraction_status"] == "failed"
        assert entry["error"] == "invalid_toml"


def test_user_profile_roundtrip(table_manager: TableManager) -> None:
    table_manager.upsert_user_profile(
        UserProfileRow(
            username="octocat",
            github_id=1,
            name="The Octocat",
            bio="Hello",
            public_repos=8,
            followers=100,
            following=0,
            github_created_at="2020-01-01T00:00:00+00:00",
            github_updated_at="2026-01-01T00:00:00+00:00",
            fingerprint="fp1",
            cached_at="2026-02-01T00:00:00+00:00",
        )
    )

    fetched = table_manager.get_user_profile("octocat")
    assert fetched is not None
    assert fetched["username"] == "octocat"
    assert fetched["github_id"] == 1
    assert fetched["public_repos"] == 8
    assert fetched["followers"] == 100
    assert fetched["fingerprint"] == "fp1"
    assert fetched["github_created_at"] == "2020-01-01T00:00:00+00:00"
    assert fetched["cached_at"] == "2026-02-01T00:00:00+00:00"
