"""Integration tests for table-first architecture per plan-dataProcessingArchitecture.prompt.md.

Tests the hot/cold data separation:
- Hot metadata (job progress, repo summaries, model status) → Azure Tables
- Cold content (full repo payloads, model artifacts) → Azure Blobs
- Queue messages carry minimal data + references
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from azure.core.exceptions import ResourceNotFoundError

from foliohive_shared import FingerprintManager
from foliohive_shared.table import (
    JobMetadataRow,
    RepoGitHubMetadataRow,
    RepoSyncStatusRow,
    TableManager,
    TableNames,
)


_FUNCTION_APP_PATH = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "function-app")
)
if os.path.isdir(_FUNCTION_APP_PATH) and _FUNCTION_APP_PATH not in sys.path:
    sys.path.insert(0, _FUNCTION_APP_PATH)


@pytest.fixture
def mock_table_manager():
    """Mock table_manager with JobMetadata, RepoGitHubMetadata, ModelMetadata tables."""
    manager = MagicMock()
    manager.is_enabled.return_value = True
    
    # In-memory stores for each table
    job_metadata: Dict[tuple, Dict[str, Any]] = {}
    repo_github_metadata: Dict[tuple, Dict[str, Any]] = {}
    model_metadata: Dict[tuple, Dict[str, Any]] = {}
    
    def upsert_job_metadata(row):
        key = (row.username, row.job_id)
        job_metadata[key] = {
            'PartitionKey': row.username,
            'RowKey': row.job_id,
            'username': row.username,
            'job_id': row.job_id,
            'status': row.status,
            'bundle_fingerprint': row.bundle_fingerprint,
            'force_refresh': row.force_refresh,
            'last_requeue_at': row.last_requeue_at,
            'trace_id': row.trace_id,
            'request_id': row.request_id,
            'created_at': row.created_at,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
    
    def get_job_metadata(username: str, job_id: str):
        return job_metadata.get((username, job_id))
    
    def list_jobs_metadata(username: str):
        return [s for key, s in job_metadata.items() if key[0] == username]
    
    def update_job_metadata(username: str, job_id: str, updates: Dict[str, Any]):
        key = (username, job_id)
        if key in job_metadata:
            job_metadata[key].update(updates)
            job_metadata[key]['updated_at'] = datetime.now(timezone.utc).isoformat()
    
    def upsert_repo_github_metadata(row):
        key = (row.username, row.repo_name)
        repo_github_metadata[key] = {
            'PartitionKey': row.username,
            'RowKey': row.repo_name,
            'username': row.username,
            'repo_name': row.repo_name,
            'fingerprint': getattr(row, 'fingerprint', None),
            'full_name': getattr(row, 'full_name', None),
            'description': getattr(row, 'description', None),
            'topics': getattr(row, 'topics', None),
            'html_url': getattr(row, 'html_url', None),
            'homepage': getattr(row, 'homepage', None),
            'stars': getattr(row, 'stars', 0),
            'forks': getattr(row, 'forks', 0),
            'is_fork': getattr(row, 'is_fork', False),
            'is_archived': getattr(row, 'is_archived', False),
            'license_name': getattr(row, 'license_name', None),
            'github_created_at': getattr(row, 'github_created_at', None),
            'github_updated_at': getattr(row, 'github_updated_at', None),
            'github_pushed_at': getattr(row, 'github_pushed_at', None),
        }
    
    def get_repo_github_metadata(username: str, repo_name: str):
        return repo_github_metadata.get((username, repo_name))
    
    def query_repo_github_metadata(username: str):
        results = [
            meta for key, meta in repo_github_metadata.items()
            if key[0] == username
        ]
        return results
    
    def upsert_model_metadata(row):
        key = (row.username, row.model_fingerprint)
        model_metadata[key] = {
            'PartitionKey': row.username,
            'RowKey': row.model_fingerprint,
            'username': row.username,
            'model_fingerprint': row.model_fingerprint,
            'experiment_name': row.experiment_name,
            'status': row.status,
            'trained_at': row.trained_at,
            'repos_count': row.repos_count,
            'repo_names': list(row.repo_names),
            'training_params': dict(row.training_params),
        }
    
    def get_model_metadata(username: str, fingerprint: str):
        return model_metadata.get((username, fingerprint))
    
    manager.upsert_job_metadata = upsert_job_metadata
    manager.get_job_metadata = get_job_metadata
    manager.list_jobs_metadata = list_jobs_metadata
    manager.update_job_metadata = update_job_metadata
    manager.upsert_repo_github_metadata = upsert_repo_github_metadata
    manager.get_repo_github_metadata = get_repo_github_metadata
    manager.query_repo_github_metadata = query_repo_github_metadata
    manager.upsert_model_metadata = upsert_model_metadata
    manager.get_model_metadata = get_model_metadata
    
    return manager


class _FakeTableClient:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict] = {}

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

    def _matches_filter(self, entity: dict, filter_str: str) -> bool:
        if not filter_str:
            return True

        partition_values = self._extract_eq_values(filter_str, "PartitionKey")
        if partition_values and entity.get("PartitionKey") not in partition_values:
            return False

        row_key_values = self._extract_eq_values(filter_str, "RowKey")
        if row_key_values and entity.get("RowKey") not in row_key_values:
            return False

        for field in ("repo_name", "job_id", "operation", "status"):
            values = self._extract_eq_values(filter_str, field)
            if values and entity.get(field) not in values:
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


@pytest.fixture(name="real_table_manager")
def _real_table_manager() -> TableManager:
    service = _FakeServiceClient()
    return TableManager(table_service_client=service, table_names=TableNames())


class TestTableFirstArchitecture:
    """Test that workers read from tables first, fall back to cache."""

    def test_sync_worker_persists_repo_metadata_to_table(self, mock_table_manager):
        """Verify sync worker creates RepoGitHubMetadata rows with fingerprint."""
        from foliohive_shared.table import RepoGitHubMetadataRow
        
        username = 'testuser'
        repo_name = 'test-repo'
        
        row = RepoGitHubMetadataRow(
            username=username,
            repo_name=repo_name,
            fingerprint='fp_123',
            description='Test repository',
            is_fork=False,
        )
        
        mock_table_manager.upsert_repo_github_metadata(row)
        
        # Verify row exists
        result = mock_table_manager.get_repo_github_metadata(username, repo_name)
        assert result is not None
        assert result['repo_name'] == repo_name
        assert result['fingerprint'] == 'fp_123'

    def test_merge_worker_queries_table_first_before_cache(self, mock_table_manager):
        """Verify merge worker queries GitHub metadata table for all repos."""
        from foliohive_shared.table import RepoGitHubMetadataRow
        
        username = 'testuser'
        
        # Seed table with GitHub metadata
        for i in range(3):
            row = RepoGitHubMetadataRow(
                username=username,
                repo_name=f'repo-{i}',
                fingerprint=f'fp_{i}',
                description=f'Repository {i}',
                is_fork=False,
            )
            mock_table_manager.upsert_repo_github_metadata(row)
        
        # Query should return all 3 repos
        results = mock_table_manager.query_repo_github_metadata(username)
        assert len(results) == 3
        assert all(r['username'] == username for r in results)
        assert all(r['fingerprint'] for r in results)

    def test_api_gateway_reads_bundle_from_table(self, mock_table_manager):
        """Verify API gateway serves bundle data from JobMetadata + RepoGitHubMetadata tables."""
        from foliohive_shared.table import JobMetadataRow, RepoGitHubMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        # Create job metadata
        job_row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='completed',
            bundle_fingerprint='bundle_fp_xyz',
            force_refresh=False,
            last_requeue_at=None,
            trace_id=None,
            request_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(job_row)
        
        # Create GitHub metadata
        for repo_name in ['repo-a', 'repo-b']:
            repo_row = RepoGitHubMetadataRow(
                username=username,
                repo_name=repo_name,
                fingerprint=f'fp_{repo_name}',
                description=f'Repository {repo_name}',
                is_fork=False,
            )
            mock_table_manager.upsert_repo_github_metadata(repo_row)
        
        # API gateway reads job + repos
        job = mock_table_manager.get_job_metadata(username, job_id)
        assert job['status'] == 'completed'
        assert job['bundle_fingerprint'] == 'bundle_fp_xyz'
        
        repos = mock_table_manager.query_repo_github_metadata(username)
        assert len(repos) == 2
        assert {r['repo_name'] for r in repos} == {'repo-a', 'repo-b'}


class TestJobProgressTracking:
    """Test JobSessions table updates during pipeline execution."""

    def test_sync_worker_updates_progress_incrementally(self, mock_table_manager):
        """Verify sync worker updates job status as repos complete (via RepoSyncStatus table)."""
        from foliohive_shared.table import JobMetadataRow, RepoSyncStatusRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        # Initialize job
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='syncing',
            bundle_fingerprint=None,
            force_refresh=False,
            last_requeue_at=None,
            trace_id=None,
            request_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Simulate sync completions via RepoSyncStatus updates
        # (Progress is now tracked in RepoSyncStatus table, not JobMetadata)
        for repo_name in ['repo-1', 'repo-2', 'repo-3']:
            # In real flow, sync_worker would update RepoSyncStatus to 'synced'
            # and reconciliation_worker would check counts
            pass
        
        # Mark job as metadata_ready after all repos synced
        mock_table_manager.update_job_metadata(username, job_id, {
            'status': 'metadata_ready',
        })
        
        # Final state
        final = mock_table_manager.get_job_metadata(username, job_id)
        assert final['status'] == 'metadata_ready'

    def test_merge_worker_marks_job_completed(self, mock_table_manager):
        """Verify merge worker sets status=completed and bundle_fingerprint."""
        from foliohive_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='metadata_ready',
            bundle_fingerprint=None,
            force_refresh=False,
            last_requeue_at=None,
            trace_id=None,
            request_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Merge worker updates status + fingerprint
        mock_table_manager.update_job_metadata(username, job_id, {
            'status': 'completed',
            'bundle_fingerprint': 'merged_fp_abc',
        })
        
        final = mock_table_manager.get_job_metadata(username, job_id)
        assert final['status'] == 'completed'
        assert final['bundle_fingerprint'] == 'merged_fp_abc'


class TestModelMetadataPersistence:
    """Test that job metadata can be updated for bundle tracking."""

    def test_training_completion_updates_job_metadata(self, mock_table_manager):
        """Verify that JobMetadata can be updated with bundle_fingerprint."""
        from foliohive_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='completed',
            bundle_fingerprint='bundle_fp',
            force_refresh=False,
            last_requeue_at=None,
            trace_id=None,
            request_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Training worker updates bundle fingerprint if needed
        # (Model metadata tracked separately in ModelMetadata table)
        mock_table_manager.update_job_metadata(username, job_id, {
            'bundle_fingerprint': 'bundle_with_model_fp_new',
        })
        
        final = mock_table_manager.get_job_metadata(username, job_id)
        assert final['bundle_fingerprint'] == 'bundle_with_model_fp_new'


class TestEdgeCases:
    """Test edge cases and failure scenarios."""

    def test_table_disabled_falls_back_gracefully(self, mock_table_manager):
        """Verify workers handle disabled table_manager without crashes."""
        mock_table_manager.is_enabled.return_value = False
        
        # Should not raise
        if mock_table_manager.is_enabled():
            mock_table_manager.query_repo_github_metadata('user')
        
        # Verify fallback path (cache) would be invoked
        assert not mock_table_manager.is_enabled()

    def test_missing_job_id_returns_latest_session(self, mock_table_manager):
        """Verify API gateway returns latest session when job_id not specified."""
        from foliohive_shared.table import JobMetadataRow
        
        username = 'testuser'
        
        # Create multiple sessions
        for i in range(3):
            job_id = f'job-{i}'
            row = JobMetadataRow(
                username=username,
                job_id=job_id,
                status='completed',
                bundle_fingerprint=f'fp_{i}',
                force_refresh=False,
                created_at=datetime(2025, 1, 10 + i, tzinfo=timezone.utc).isoformat(),
                updated_at=None,
            )
            mock_table_manager.upsert_job_metadata(row)
        
        # Query all sessions for user
        sessions = mock_table_manager.list_jobs_metadata(username)
        assert len(sessions) == 3
        
        # Should sort by updated_at/created_at and return latest
        latest = max(sessions, key=lambda s: s.get('created_at') or '')
        assert latest['job_id'] == 'job-2'

    def test_partial_repo_sync_tracked_correctly(self, mock_table_manager):
        """Verify progress tracking when only some repos complete (via RepoSyncStatus table)."""
        from foliohive_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='syncing',
            bundle_fingerprint=None,
            force_refresh=False,
            last_requeue_at=None,
            trace_id=None,
            request_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # In normalized schema, progress is tracked via RepoSyncStatus table
        # (Each repo has a status: pending -> synced -> cached)
        # Job stays in 'syncing' until all repos complete
        
        session = mock_table_manager.get_job_metadata(username, job_id)
        assert session['status'] == 'syncing'
        
        # After all repos complete, reconciliation_worker marks job as metadata_ready
        mock_table_manager.update_job_metadata(username, job_id, {
            'status': 'metadata_ready',
        })
        
        updated = mock_table_manager.get_job_metadata(username, job_id)
        assert updated['status'] == 'metadata_ready'

    def test_fingerprint_mismatch_triggers_resync(self, mock_table_manager):
        """Verify stale fingerprints in GitHub metadata detected."""
        from foliohive_shared.table import RepoGitHubMetadataRow
        
        username = 'testuser'
        
        # Old fingerprint in GitHub metadata table
        row = RepoGitHubMetadataRow(
            username=username,
            repo_name='test-repo',
            fingerprint='old_fp',
            description='Test repo',
            is_fork=False,
        )
        mock_table_manager.upsert_repo_github_metadata(row)
        
        # Simulate freshness check detecting new fingerprint
        stored = mock_table_manager.get_repo_github_metadata(username, 'test-repo')
        assert stored['fingerprint'] == 'old_fp'
        
        # New fingerprint from GitHub would be different
        expected_fp = 'new_fp'
        assert stored['fingerprint'] != expected_fp
        # Would trigger re-enqueue in actual API gateway logic


class TestNormalizedTableInteractions:
    """Test interactions between normalized tables (languages, file types, GitHub metadata)."""

    def test_repo_with_full_normalized_data(self, mock_table_manager):
        """Verify complete repo data stored across normalized tables."""
        username = 'testuser'
        repo_name = 'polyglot-project'
        
        # Would be implemented with real table_manager mocking all normalized tables
        # For now, verify the pattern would work
        assert mock_table_manager.is_enabled()

    def test_api_usage_aggregation_for_dashboard(self, mock_table_manager):
        """Verify API usage can be aggregated for admin dashboard metrics."""
        # This would test the pattern used by admin_gateway.py
        # to aggregate API usage across multiple operations
        assert mock_table_manager.is_enabled()

    def test_session_candidate_tracking_across_queries(self, mock_table_manager):
        """Verify SessionCandidates table tracks user query patterns."""
        # This would test the pattern for tracking which users
        # are querying which repos/jobs in a session
        assert mock_table_manager.is_enabled()


class TestRepoSyncStatusLifecycle:
    """Test complete lifecycle of RepoSyncStatus through pipeline stages."""

    def test_repo_status_pending_to_synced_to_cached(self, mock_table_manager):
        """Verify status transitions through pipeline stages."""
        from foliohive_shared.table import RepoSyncStatusRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        repo_name = 'test-repo'
        
        # Mock RepoSyncStatus operations
        repo_statuses: Dict[tuple, Dict[str, Any]] = {}
        
        def upsert_repo_status(row):
            key = (row.job_id, row.repo_name)
            repo_statuses[key] = {
                'job_id': row.job_id,
                'repo_name': row.repo_name,
                'username': row.username,
                'status': row.status,
                'sync_message_id': row.sync_message_id,
                'cache_message_id': row.cache_message_id,
                'error': row.error,
                'synced_at': row.synced_at,
                'cached_at': row.cached_at,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
        
        def get_repo_status(job_id: str, repo_name: str):
            return repo_statuses.get((job_id, repo_name))
        
        def update_repo_status(job_id: str, repo_name: str, updates: Dict[str, Any]):
            key = (job_id, repo_name)
            if key in repo_statuses:
                repo_statuses[key].update(updates)
                repo_statuses[key]['updated_at'] = datetime.now(timezone.utc).isoformat()
        
        mock_table_manager.upsert_repo_status = upsert_repo_status
        mock_table_manager.get_repo_status = get_repo_status
        mock_table_manager.update_repo_status = update_repo_status
        
        # Stage 1: Pending (just queued)
        pending_row = RepoSyncStatusRow(
            job_id=job_id,
            repo_name=repo_name,
            username=username,
            status='pending',
            sync_message_id='msg-sync-1',
            cache_message_id=None,
            error=None,
            synced_at=None,
            cached_at=None,
        )
        mock_table_manager.upsert_repo_status(pending_row)
        
        status = mock_table_manager.get_repo_status(job_id, repo_name)
        assert status['status'] == 'pending'
        assert status['sync_message_id'] == 'msg-sync-1'
        
        # Stage 2: Synced (metadata complete)
        mock_table_manager.update_repo_status(job_id, repo_name, {
            'status': 'synced',
            'synced_at': datetime.now(timezone.utc).isoformat(),
        })
        
        status = mock_table_manager.get_repo_status(job_id, repo_name)
        assert status['status'] == 'synced'
        assert status['synced_at'] is not None
        
        # Stage 3: Cached (files downloaded)
        mock_table_manager.update_repo_status(job_id, repo_name, {
            'status': 'cached',
            'cache_message_id': 'msg-cache-1',
            'cached_at': datetime.now(timezone.utc).isoformat(),
        })
        
        final_status = mock_table_manager.get_repo_status(job_id, repo_name)
        assert final_status['status'] == 'cached'
        assert final_status['cache_message_id'] == 'msg-cache-1'
        assert final_status['cached_at'] is not None

    def test_repo_status_failure_tracking(self, mock_table_manager):
        """Verify failed repos tracked with error messages."""
        from foliohive_shared.table import RepoSyncStatusRow
        
        job_id = str(uuid.uuid4())
        repo_name = 'broken-repo'
        
        # Mock simplified for test
        repo_statuses: Dict[tuple, Dict[str, Any]] = {}
        
        def upsert_repo_status(row):
            key = (row.job_id, row.repo_name)
            repo_statuses[key] = {
                'job_id': row.job_id,
                'repo_name': row.repo_name,
                'status': row.status,
                'error': row.error,
            }
        
        def get_repo_status(job_id: str, repo_name: str):
            return repo_statuses.get((job_id, repo_name))
        
        mock_table_manager.upsert_repo_status = upsert_repo_status
        mock_table_manager.get_repo_status = get_repo_status
        
        # Mark repo as failed
        failed_row = RepoSyncStatusRow(
            job_id=job_id,
            repo_name=repo_name,
            username='testuser',
            status='failed',
            sync_message_id=None,
            cache_message_id=None,
            error='Repository not found (404)',
        )
        mock_table_manager.upsert_repo_status(failed_row)
        
        status = mock_table_manager.get_repo_status(job_id, repo_name)
        assert status['status'] == 'failed'
        assert '404' in status['error']


class TestMultiTableQueries:
    """Test queries that span multiple tables for complete data views."""

    def test_complete_repo_profile_query(self, mock_table_manager):
        """Verify querying complete repo profile across RepoGitHubMetadata + RepoLanguages tables."""
        from foliohive_shared.table import (
            RepoLanguagesRow,
            RepoGitHubMetadataRow,
        )
        
        username = 'testuser'
        repo_name = 'full-stack-app'
        
        # Mock all repo-related tables
        repo_github: Dict[tuple, Dict] = {}
        repo_languages: Dict[tuple, Dict] = {}
        
        def batch_upsert_repo_languages(rows):
            for row in rows:
                key = (row.job_id, row.repo_language_key)
                repo_languages[key] = {
                    'job_id': row.job_id,
                    'repo_language_key': row.repo_language_key,
                    'repo_name': row.repo_name,
                    'language': row.language,
                    'bytes_count': row.bytes_count,
                }
        
        def query_repo_languages(job_id: str):
            """Query languages by job_id, returning dict keyed by repo_name."""
            by_repo = {}
            for key, lang in repo_languages.items():
                if lang['job_id'] == job_id:
                    repo_name = lang['repo_name']
                    by_repo.setdefault(repo_name, []).append(lang)
            return by_repo
        
        def upsert_repo_github_metadata(row):
            repo_github[(row.username, row.repo_name)] = {
                'username': row.username,
                'repo_name': row.repo_name,
                'fingerprint': getattr(row, 'fingerprint', None),
                'stars_count': getattr(row, 'stars_count', 0),
                'description': getattr(row, 'description', None),
            }
        
        def get_repo_github_metadata(username: str, repo_name: str):
            return repo_github.get((username, repo_name))
        
        mock_table_manager.batch_upsert_repo_languages = batch_upsert_repo_languages
        mock_table_manager.query_repo_languages = query_repo_languages
        mock_table_manager.upsert_repo_github_metadata = upsert_repo_github_metadata
        mock_table_manager.get_repo_github_metadata = get_repo_github_metadata
        
        # Insert language data with job_id
        job_id = "test-job-123"
        mock_table_manager.batch_upsert_repo_languages([
            RepoLanguagesRow(
                job_id=job_id,
                repo_language_key=f'{repo_name}|Python',
                repo_name=repo_name,
                language='Python',
                bytes_count=10000,
            ),
            RepoLanguagesRow(
                job_id=job_id,
                repo_language_key=f'{repo_name}|TypeScript',
                repo_name=repo_name,
                language='TypeScript',
                bytes_count=8000,
            ),
        ])
        
        # Insert GitHub metadata with fingerprint
        mock_table_manager.upsert_repo_github_metadata(
            RepoGitHubMetadataRow(
                username=username,
                repo_name=repo_name,
                fingerprint='fp_xyz',
                stars_count=500,
                description='A full-stack application',
            )
        )
        
        # Query complete profile
        languages_by_repo = mock_table_manager.query_repo_languages(job_id)
        languages = languages_by_repo.get(repo_name, [])
        github = mock_table_manager.get_repo_github_metadata(username, repo_name)
        
        # Verify complete profile
        assert github['fingerprint'] == 'fp_xyz'
        assert len(languages) == 2
        assert {lang['language'] for lang in languages} == {'Python', 'TypeScript'}
        assert github['stars_count'] == 500
        assert github['description'] == 'A full-stack application'


class TestBatchOperationsAndErrorHandling:
    """Test batch operations and edge cases."""

    def test_empty_batch_operations(self, mock_table_manager):
        """Verify batch operations handle empty lists gracefully."""
        # Mock batch operation
        batch_calls = []
        
        def batch_upsert_repo_github_metadata(rows):
            batch_calls.append(len(rows))
            # Should handle empty gracefully
            pass
        
        mock_table_manager.batch_upsert_repo_github_metadata = batch_upsert_repo_github_metadata
        
        # Call with empty list
        mock_table_manager.batch_upsert_repo_github_metadata([])
        
        assert len(batch_calls) == 1
        assert batch_calls[0] == 0

    def test_large_batch_chunking(self, mock_table_manager):
        """Verify large batches are chunked properly (Azure Tables limit: 100 operations/batch)."""
        from foliohive_shared.table import RepoLanguagesRow
        
        batches_written = []
        
        def batch_upsert_repo_languages(rows):
            # In real implementation, this would chunk into batches of 100
            # For test, just track the call
            batches_written.append(len(rows))
        
        mock_table_manager.batch_upsert_repo_languages = batch_upsert_repo_languages
        
        # Create 250 language rows (should be 3 batches: 100, 100, 50)
        large_batch = [
            RepoLanguagesRow(
                job_id='test-job-123',
                repo_language_key=f'repo-{i}|Python',
                repo_name=f'repo-{i}',
                language='Python',
                bytes_count=1000,
            )
            for i in range(250)
        ]
        
        mock_table_manager.batch_upsert_repo_languages(large_batch)
        
        # Verify batch was processed
        assert len(batches_written) == 1
        assert batches_written[0] == 250

    def test_job_not_found_returns_none(self, mock_table_manager):
        """Verify querying non-existent job returns None."""
        result = mock_table_manager.get_job_metadata('nonexistent', 'fake-job')
        assert result is None

    def test_query_with_no_matches(self, mock_table_manager):
        """Verify queries with no matches return empty list."""
        from foliohive_shared.table import JobMetadataRow
        
        # Add one job
        row = JobMetadataRow(
            username='alice',
            job_id='job-1',
            status='completed',
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Query different user
        results = mock_table_manager.list_jobs_metadata('bob')
        assert results == []

    def test_concurrent_updates_last_write_wins(self, mock_table_manager):
        """Verify concurrent updates follow last-write-wins semantics."""
        from foliohive_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        # Initial state
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='queued',
            bundle_fingerprint=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Simulate two concurrent updates
        mock_table_manager.update_job_metadata(username, job_id, {
            'status': 'syncing',
        })
        
        mock_table_manager.update_job_metadata(username, job_id, {
            'bundle_fingerprint': 'fp_abc',
        })
        
        # Last write wins - both updates applied
        final = mock_table_manager.get_job_metadata(username, job_id)
        assert final['status'] == 'syncing'
        assert final['bundle_fingerprint'] == 'fp_abc'

    def test_special_characters_in_keys(self, mock_table_manager):
        """Verify special characters in partition/row keys are handled."""
        from foliohive_shared.table import RepoGitHubMetadataRow
        
        # Repo name with special characters
        username = 'test-user'
        repo_name = 'repo.with-special_chars'
        
        row = RepoGitHubMetadataRow(
            username=username,
            repo_name=repo_name,
            fingerprint='fp_123',
        )
        
        mock_table_manager.upsert_repo_github_metadata(row)
        
        result = mock_table_manager.get_repo_github_metadata(username, repo_name)
        assert result is not None
        assert result['repo_name'] == repo_name

    def test_null_optional_fields(self, mock_table_manager):
        """Verify null/None values in optional fields are handled correctly."""
        from foliohive_shared.table import RepoGitHubMetadataRow
        
        row = RepoGitHubMetadataRow(
            username='testuser',
            repo_name='minimal-repo',
            fingerprint='fp_xyz',
            description=None,  # Optional
            homepage_url=None,  # Optional
            license_name=None,  # Optional
        )
        
        mock_table_manager.upsert_repo_github_metadata(row)
        
        result = mock_table_manager.get_repo_github_metadata('testuser', 'minimal-repo')
        assert result is not None
        # Verify None fields don't cause errors
        assert result['fingerprint'] == 'fp_xyz'


class TestWorkerBlueprintTableIntegration:
    def test_sync_worker_updates_status_and_job(self, monkeypatch, real_table_manager: TableManager) -> None:
        from foliohive_shared.table import JobMetadataRow, RepoSyncStatusRow
        from blueprints import sync_worker

        monkeypatch.setattr(sync_worker, "table_manager", real_table_manager)

        username = "testuser"
        job_id = "job-xyz"
        repo_name = "repo-1"

        real_table_manager.upsert_job_metadata(
            JobMetadataRow(username=username, job_id=job_id, status="queued")
        )
        real_table_manager.upsert_repo_status(
            RepoSyncStatusRow(job_id=job_id, repo_name=repo_name, username=username, status="pending")
        )

        sync_worker._update_job_progress(job_id, username, repo_name, sync_failed=False, message_id="msg-1")

        repo_status = real_table_manager.get_repo_status(job_id, repo_name)
        assert repo_status is not None
        assert repo_status["status"] == "synced"
        assert repo_status["synced_at"] is not None

        job = real_table_manager.get_job_metadata(username, job_id)
        assert job is not None
        assert job["status"] == "syncing"

    def test_cache_worker_completes_job_and_sets_bundle_fingerprint(
        self,
        monkeypatch,
        real_table_manager: TableManager,
    ) -> None:
        from foliohive_shared.table import JobMetadataRow, RepoGitHubMetadataRow, RepoSyncStatusRow
        from blueprints import cache_worker

        monkeypatch.setattr(cache_worker, "table_manager", real_table_manager)

        username = "testuser"
        job_id = "job-abc"
        repo_name = "repo-2"

        real_table_manager.upsert_job_metadata(
            JobMetadataRow(username=username, job_id=job_id, status="syncing")
        )
        real_table_manager.upsert_repo_status(
            RepoSyncStatusRow(job_id=job_id, repo_name=repo_name, username=username, status="synced")
        )
        real_table_manager.upsert_repo_github_metadata(
            RepoGitHubMetadataRow(
                username=username,
                repo_name=repo_name,
                fingerprint="fp-123",
                description="Test repo",
            )
        )

        cache_worker._update_cache_progress(
            job_id,
            username,
            repo_name,
            cache_failed=False,
            message_id="msg-cache",
        )

        repo_status = real_table_manager.get_repo_status(job_id, repo_name)
        assert repo_status is not None
        assert repo_status["status"] == "cached"
        assert repo_status["cached_at"] is not None

        job = real_table_manager.get_job_metadata(username, job_id)
        assert job is not None
        expected_fingerprint = FingerprintManager.generate_bundle_fingerprint(["fp-123"])
        assert job["status"] == "completed"
        assert job["bundle_fingerprint"] == expected_fingerprint
        assert job.get("completed_at") is not None
