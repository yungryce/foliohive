from __future__ import annotations

import pytest  # type: ignore

from azure.core.exceptions import ResourceNotFoundError  # type: ignore

from cloudfolio_shared.table import (  # type: ignore
    JobSessionRow,
    ModelMetadataRow,
    RepoMetadataRow,
    RepoSyncStatusRow,
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
        candidate_sessions="CandidateSessions",
        repo_metadata="RepoMetadata",
        model_metadata="ModelMetadata",
        repo_sync_status="RepoSyncStatus",
    )
    return TableManager(table_service_client=service, table_names=names)


def test_candidate_session_roundtrip(table_manager: TableManager) -> None:
    row = JobSessionRow(
        username="alice",
        job_id="job-1",
        expected_repos=["api", "web"],
        queued_repos=["api"],
        status="queued",
    )
    table_manager.upsert_job_session(row)

    table_manager.update_candidate_session(
        "alice",
        "job-1",
        {"completed_repos": 1, "synced_repos": ["api"], "status": "synced"},
    )

    stored = table_manager.get_candidate_session("alice", "job-1")
    assert stored is not None
    assert stored["completed_repos"] == 1
    assert stored["status"] == "synced"
    assert stored["expected_repos"] == ["api", "web"]
    assert stored["synced_repos"] == ["api"]


def test_repo_metadata_query(table_manager: TableManager) -> None:
    table_manager.batch_upsert_repo_metadata(
        [
            RepoMetadataRow(
                username="alice",
                repo_name="api",
                fingerprint="abc",
                job_id="job-1",
                document={"name": "api"},
                categorized_types={"python": 10},
            ),
            RepoMetadataRow(
                username="alice",
                repo_name="web",
                fingerprint="def",
                job_id="job-2",
                document={"name": "web"},
            ),
        ]
    )

    results = table_manager.query_repo_metadata("alice", job_id="job-1")
    assert len(results) == 1
    assert results[0]["document"]["name"] == "api"
    assert results[0]["fingerprint"] == "abc"

    filtered = table_manager.query_repo_metadata("alice", repo_names=["web"])
    assert len(filtered) == 1
    assert filtered[0]["repo_name"] == "web"


def test_model_metadata(table_manager: TableManager) -> None:
    table_manager.upsert_model_metadata(
        ModelMetadataRow(
            username="alice",
            fingerprint="fp-123",
            status="completed",
            training_params={"batch_size": 16},
        )
    )

    stored = table_manager.get_model_metadata("alice", "fp-123")
    assert stored is not None
    assert stored["training_params"]["batch_size"] == 16

    all_rows = table_manager.list_model_metadata("alice")
    assert len(all_rows) == 1
    assert all_rows[0]["fingerprint"] == "fp-123"


def test_repo_sync_status_roundtrip(table_manager: TableManager) -> None:
    row = RepoSyncStatusRow(
        job_id="job-1",
        repo_name="api",
        username="alice",
        status="synced",
        message_id="m-1",
        error=None,
    )

    table_manager.upsert_repo_status(row)

    fetched = table_manager.get_repo_status("job-1", "api")
    assert fetched is not None
    assert fetched["status"] == "synced"
    assert fetched["message_id"] == "m-1"

    listed = table_manager.list_repo_statuses("job-1")
    assert len(listed) == 1
    assert listed[0]["repo_name"] == "api"
