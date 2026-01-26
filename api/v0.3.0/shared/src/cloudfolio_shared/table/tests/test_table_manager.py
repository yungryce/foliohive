from __future__ import annotations

import pytest  # type: ignore

from azure.core.exceptions import ResourceNotFoundError  # type: ignore

from cloudfolio_shared.table import (  # type: ignore
    JobMetadataRow,
    ModelMetadataRow,
    RepoAPIUsageRow,
    RepoFileTypesRow,
    RepoGitHubMetadataRow,
    RepoLanguagesRow,
    RepoMetadataRow,
    RepoSyncStatusRow,
    SessionCandidateRow,
    TableManager,
    TableNames,
)


class _FakeTableClient:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}

    # azure.data.tables compatibility -------------------------------------------------
    def create_table(self) -> None:  # pragma: no cover - nothing to do for fake
        return None

    def upsert_entity(self, entity: dict, mode=None) -> None:
        key = (entity["PartitionKey"], entity["RowKey"])
        stored = self.entities.get(key, {})
        stored.update(entity)
        self.entities[key] = stored

    def get_entity(self, partition_key: str, row_key: str) -> dict:
        key = (partition_key, row_key)
        if key not in self.entities:
            raise ResourceNotFoundError("entity not found")
        return dict(self.entities[key])

    def list_entities(self, filter: str | None = None):  # type: ignore[override]
        for entity in self.entities.values():
            if self._matches_filter(entity, filter or ""):
                yield dict(entity)

    def submit_transaction(self, operations):
        for verb, entity, _kwargs in operations:
            if verb != "upsert":  # pragma: no cover - safety
                continue
            self.upsert_entity(entity)

    # filtering helpers ------------------------------------------------------------
    @staticmethod
    def _extract_value(filter_str: str, field: str) -> str | None:
        token = f"{field} eq '"
        if token not in filter_str:
            return None
        start = filter_str.index(token) + len(token)
        end = filter_str.index("'", start)
        return filter_str[start:end]

    @classmethod
    def _extract_row_keys(cls, filter_str: str) -> set[str]:
        result: set[str] = set()
        token = "RowKey eq '"
        start = 0
        while True:
            idx = filter_str.find(token, start)
            if idx == -1:
                break
            start_val = idx + len(token)
            end = filter_str.index("'", start_val)
            result.add(filter_str[start_val:end])
            start = end + 1
        return result

    def _matches_filter(self, entity: dict, filter_str: str) -> bool:
        partition = self._extract_value(filter_str, "PartitionKey")
        if partition and entity.get("PartitionKey") != partition:
            return False
        job_id = self._extract_value(filter_str, "job_id")
        if job_id and entity.get("job_id") != job_id:
            return False
        row_keys = self._extract_row_keys(filter_str)
        if row_keys and entity.get("RowKey") not in row_keys:
            return False
        return True


class _FakeServiceClient:
    def __init__(self) -> None:
        self.tables: dict[str, _FakeTableClient] = {}

    def get_table_client(self, name: str) -> _FakeTableClient:
        if name not in self.tables:
            self.tables[name] = _FakeTableClient()
        return self.tables[name]


@pytest.fixture()
def table_manager() -> TableManager:
    service = _FakeServiceClient()
    names = TableNames(
        job_metadata="JobMetadata",
        session_candidates="SessionCandidates",
        repo_metadata="RepoMetadata",
        repo_languages="RepoLanguages",
        repo_file_types="RepoFileTypes",
        repo_github_metadata="RepoGitHubMetadata",
        model_metadata="ModelMetadata",
        repo_sync_status="RepoSyncStatus",
        repo_api_usage="RepoAPIUsage",
    )
    return TableManager(table_service_client=service, table_names=names)


def test_candidate_session_roundtrip(table_manager: TableManager) -> None:
    row = JobMetadataRow(
        username="alice",
        job_id="job-1",
        status="queued",
        force_refresh=False,
    )
    table_manager.upsert_job_metadata(row)

    table_manager.update_job_metadata(
        "alice",
        "job-1",
        {"status": "metadata_ready", "bundle_fingerprint": "fp_abc"},
    )

    stored = table_manager.get_job_metadata("alice", "job-1")
    assert stored is not None
    assert stored["status"] == "metadata_ready"
    assert stored["bundle_fingerprint"] == "fp_abc"
    assert stored["force_refresh"] is False


def test_repo_metadata_query(table_manager: TableManager) -> None:
    table_manager.batch_upsert_repo_metadata(
        [
            RepoMetadataRow(
                username="alice",
                repo_name="api",
                fingerprint="abc",
                has_documentation=True,
                readme_excerpt="# API",
            ),
            RepoMetadataRow(
                username="alice",
                repo_name="web",
                fingerprint="def",
                has_documentation=False,
            ),
        ]
    )

    results = table_manager.query_repo_metadata("alice")
    assert len(results) == 2
    api_repo = [r for r in results if r["repo_name"] == "api"][0]
    assert api_repo["fingerprint"] == "abc"
    assert api_repo["has_documentation"] is True

    filtered = table_manager.query_repo_metadata("alice", repo_names=["web"])
    assert len(filtered) == 1
    assert filtered[0]["repo_name"] == "web"


def test_model_metadata(table_manager: TableManager) -> None:
    table_manager.upsert_model_metadata(
        ModelMetadataRow(
            username="alice",
            model_fingerprint="fp-123",
            status="completed",
            training_params={"batch_size": 16},
            repos_count=3,
            repo_names=["repo-a", "repo-b", "repo-c"],
        )
    )

    stored = table_manager.get_model_metadata("alice", "fp-123")
    assert stored is not None
    assert stored["training_params"]["batch_size"] == 16
    assert stored["repos_count"] == 3

    all_rows = table_manager.list_model_metadata("alice")
    assert len(all_rows) == 1
    assert all_rows[0]["model_fingerprint"] == "fp-123"


def test_repo_sync_status_roundtrip(table_manager: TableManager) -> None:
    row = RepoSyncStatusRow(
        job_id="job-1",
        repo_name="api",
        username="alice",
        status="synced",
        sync_message_id="m-1",
        cache_message_id="m-2",
        error=None,
    )

    table_manager.upsert_repo_status(row)

    fetched = table_manager.get_repo_status("job-1", "api")
    assert fetched is not None
    assert fetched["status"] == "synced"
    assert fetched["sync_message_id"] == "m-1"
    assert fetched["cache_message_id"] == "m-2"

    listed = table_manager.list_repo_statuses("job-1")
    assert len(listed) == 1
    assert listed[0]["repo_name"] == "api"


def test_session_candidates_roundtrip(table_manager: TableManager) -> None:
    """Test SessionCandidates table upsert and list operations."""
    session_id = "session-123"
    
    # Upsert first candidate
    table_manager.upsert_session_candidate(session_id, "alice", "job-1")
    
    # Upsert second candidate
    table_manager.upsert_session_candidate(session_id, "bob", "job-2")
    
    # List candidates for session
    candidates = table_manager.list_session_candidates(session_id)
    assert len(candidates) == 2
    
    # Verify structure
    alice_candidate = [c for c in candidates if c["username"] == "alice"][0]
    assert alice_candidate["latest_job_id"] == "job-1"
    assert alice_candidate["query_count"] == 1
    assert alice_candidate["session_id"] == session_id
    
    # Update same user with different job
    table_manager.upsert_session_candidate(session_id, "alice", "job-3")
    
    updated_candidates = table_manager.list_session_candidates(session_id)
    alice_updated = [c for c in updated_candidates if c["username"] == "alice"][0]
    assert alice_updated["latest_job_id"] == "job-3"
    assert alice_updated["query_count"] == 2  # Incremented


def test_repo_languages_batch_upsert(table_manager: TableManager) -> None:
    """Test RepoLanguages batch operations."""
    from cloudfolio_shared.table import RepoLanguagesRow
    
    languages = [
        RepoLanguagesRow(
            username="alice",
            repo_language_key="api#Python",
            repo_name="api",
            language="Python",
            bytes_count=5000,
            percentage=75.0,
        ),
        RepoLanguagesRow(
            username="alice",
            repo_language_key="api#JavaScript",
            repo_name="api",
            language="JavaScript",
            bytes_count=1500,
            percentage=22.5,
        ),
        RepoLanguagesRow(
            username="alice",
            repo_language_key="api#Shell",
            repo_name="api",
            language="Shell",
            bytes_count=166,
            percentage=2.5,
        ),
    ]
    
    table_manager.batch_upsert_repo_languages(languages)
    
    # Query languages for repo
    results = table_manager.query_repo_languages("alice", "api")
    assert len(results) == 3
    
    # Verify Python language
    python_lang = [r for r in results if r["language"] == "Python"][0]
    assert python_lang["bytes"] == 5000
    assert python_lang["percentage"] == 75.0


def test_repo_file_types_batch_upsert(table_manager: TableManager) -> None:
    """Test RepoFileTypes batch operations."""
    from cloudfolio_shared.table import RepoFileTypesRow
    
    file_types = [
        RepoFileTypesRow(
            username="alice",
            repo_type_key="web#programming#0",
            repo_name="web",
            category="programming",
            file_path="src/main.py",
            file_type=".py",
        ),
        RepoFileTypesRow(
            username="alice",
            repo_type_key="web#programming#1",
            repo_name="web",
            category="programming",
            file_path="src/utils.py",
            file_type=".py",
        ),
        RepoFileTypesRow(
            username="alice",
            repo_type_key="web#documentation#0",
            repo_name="web",
            category="documentation",
            file_path="README.md",
            file_type=".md",
        ),
    ]
    
    table_manager.batch_upsert_repo_file_types(file_types)
    
    # Query file types for repo
    results = table_manager.query_repo_file_types("alice", "web")
    assert len(results) == 3
    
    # Verify programming category
    programming_files = [r for r in results if r["category"] == "programming"]
    assert len(programming_files) == 2
    assert all(r["file_type"] == ".py" for r in programming_files)


def test_repo_github_metadata_roundtrip(table_manager: TableManager) -> None:
    """Test RepoGitHubMetadata upsert and retrieval."""
    from cloudfolio_shared.table import RepoGitHubMetadataRow
    
    row = RepoGitHubMetadataRow(
        username="alice",
        repo_name="awesome-project",
        full_name="alice/awesome-project",
        description="An awesome project",
        html_url="https://github.com/alice/awesome-project",
        homepage="https://awesome-project.dev",
        stars=1234,
        forks=56,
        open_issues=12,
        watchers=890,
        default_branch="main",
        is_private=False,
        is_fork=False,
        is_archived=False,
        license_name="MIT",
        github_created_at="2023-01-15T10:00:00Z",
        github_updated_at="2026-01-20T15:30:00Z",
        github_pushed_at="2026-01-25T08:45:00Z",
    )
    
    table_manager.upsert_repo_github_metadata(row)
    
    # Retrieve metadata
    result = table_manager.get_repo_github_metadata("alice", "awesome-project")
    assert result is not None
    assert result["stars"] == 1234
    assert result["description"] == "An awesome project"
    assert result["license_name"] == "MIT"
    assert result["is_private"] is False


def test_repo_api_usage_tracking(table_manager: TableManager) -> None:
    """Test RepoAPIUsage upsert and list operations."""
    from cloudfolio_shared.table import RepoAPIUsageRow
    from datetime import datetime, timezone
    
    # Track freshness check
    freshness_row = RepoAPIUsageRow(
        username="alice",
        operation_key="freshness#2026-01-25T10:00:00#repo-a",
        operation="freshness_check",
        job_id=None,
        repo_name="repo-a",
        api_calls_rest=1,
        api_calls_graphql=0,
        cache_hits=0,
        rate_limit_remaining=4999,
        rate_limit_reset="2026-01-25T11:00:00Z",
        created_at="2026-01-25T10:00:00Z",
    )
    
    table_manager.upsert_api_usage(freshness_row)
    
    # Track metadata sync
    sync_row = RepoAPIUsageRow(
        username="alice",
        operation_key="metadata_sync#2026-01-25T10:05:00#repo-a",
        operation="metadata_sync",
        job_id="job-123",
        repo_name="repo-a",
        api_calls_rest=3,
        api_calls_graphql=1,
        cache_hits=2,
        rate_limit_remaining=4995,
        rate_limit_reset="2026-01-25T11:00:00Z",
        created_at="2026-01-25T10:05:00Z",
    )
    
    table_manager.upsert_api_usage(sync_row)
    
    # List all usage for user
    all_usage = table_manager.list_api_usage("alice")
    assert len(all_usage) == 2
    
    # Filter by job
    job_usage = table_manager.list_api_usage("alice", job_id="job-123")
    assert len(job_usage) == 1
    assert job_usage[0]["operation"] == "metadata_sync"
    assert job_usage[0]["api_calls_rest"] == 3
    
    # Filter by operation
    freshness_usage = table_manager.list_api_usage("alice", operation="freshness_check")
    assert len(freshness_usage) == 1
    assert freshness_usage[0]["cache_hits"] == 0


def test_update_repo_status(table_manager: TableManager) -> None:
    """Test updating RepoSyncStatus after initial insert."""
    row = RepoSyncStatusRow(
        job_id="job-1",
        repo_name="api",
        username="alice",
        status="pending",
        sync_message_id="msg-1",
        cache_message_id=None,
        error=None,
    )
    
    table_manager.upsert_repo_status(row)
    
    # Update to synced
    table_manager.update_repo_status(
        "job-1",
        "api",
        {"status": "synced", "synced_at": "2026-01-25T10:00:00Z"},
    )
    
    updated = table_manager.get_repo_status("job-1", "api")
    assert updated["status"] == "synced"
    assert updated["synced_at"] == "2026-01-25T10:00:00Z"
    
    # Update to cached
    table_manager.update_repo_status(
        "job-1",
        "api",
        {
            "status": "cached",
            "cache_message_id": "msg-2",
            "cached_at": "2026-01-25T10:05:00Z",
        },
    )
    
    final = table_manager.get_repo_status("job-1", "api")
    assert final["status"] == "cached"
    assert final["cache_message_id"] == "msg-2"
    assert final["cached_at"] == "2026-01-25T10:05:00Z"


def test_list_jobs_metadata_by_status(table_manager: TableManager) -> None:
    """Test filtering jobs by status."""
    # Create jobs with different statuses
    for i, status in enumerate(["queued", "syncing", "metadata_ready", "completed", "failed"]):
        row = JobMetadataRow(
            username=f"user-{i}",
            job_id=f"job-{i}",
            status=status,
        )
        table_manager.upsert_job_metadata(row)
    
    # Query active jobs
    active = table_manager.list_jobs_metadata_by_status(["queued", "syncing", "metadata_ready"])
    assert len(active) == 3
    assert all(j["status"] in ["queued", "syncing", "metadata_ready"] for j in active)
    
    # Query completed jobs
    completed = table_manager.list_jobs_metadata_by_status(["completed", "failed"])
    assert len(completed) == 2
    assert all(j["status"] in ["completed", "failed"] for j in completed)


def test_repo_metadata_with_blob_reference(table_manager: TableManager) -> None:
    """Test RepoMetadata storing blob references."""
    row = RepoMetadataRow(
        username="alice",
        repo_name="large-repo",
        fingerprint="fp_abc123",
        content_blob="blob://ephemeral/alice/large-repo.jsonl",
        has_documentation=True,
        readme_excerpt="# Large Repo\n\nThis repo has lots of content...",
    )
    
    table_manager.upsert_repo_metadata(row)
    
    results = table_manager.query_repo_metadata("alice", repo_names=["large-repo"])
    assert len(results) == 1
    assert results[0]["content_blob"] == "blob://ephemeral/alice/large-repo.jsonl"
    assert "Large Repo" in results[0]["readme_excerpt"]
