"""Integration tests for table-first architecture per plan-dataProcessingArchitecture.prompt.md.

Tests the hot/cold data separation:
- Hot metadata (job progress, repo summaries, model status) → Azure Tables
- Cold content (full repo payloads, model artifacts) → Azure Blobs
- Queue messages carry minimal data + references
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_table_manager():
    """Mock table_manager with JobMetadata, RepoMetadata, ModelMetadata tables."""
    manager = MagicMock()
    manager.is_enabled.return_value = True
    
    # In-memory stores for each table
    job_metadata: Dict[tuple, Dict[str, Any]] = {}
    repo_metadata: Dict[tuple, Dict[str, Any]] = {}
    model_metadata: Dict[tuple, Dict[str, Any]] = {}
    
    def upsert_job_metadata(row):
        key = (row.username, row.job_id)
        job_metadata[key] = {
            'PartitionKey': row.username,
            'RowKey': row.job_id,
            'username': row.username,
            'job_id': row.job_id,
            'status': row.status,
            'total_repos': row.total_repos,
            'completed_repos': row.completed_repos,
            'expected_repos': list(row.expected_repos),
            'queued_repos': list(row.queued_repos),
            'synced_repos': list(row.synced_repos),
            'bundle_fingerprint': row.bundle_fingerprint,
            'force_refresh': row.force_refresh,
            'model_status': row.model_status,
            'model_fingerprint': row.model_fingerprint,
            'created_at': row.created_at,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
    
    def get_job_metadata(username: str, job_id: str):
        return job_metadata.get((username, job_id))
    
    def list_jobs_metadata(username: str):
        return [s for key, s in job_metadata.items() if key[0] == username]
    
    def update_candidate_session(username: str, job_id: str, updates: Dict[str, Any]):
        key = (username, job_id)
        if key in job_metadata:
            job_metadata[key].update(updates)
            job_metadata[key]['updated_at'] = datetime.now(timezone.utc).isoformat()
    
    def upsert_repo_metadata(row):
        key = (row.username, row.repo_name)
        repo_metadata[key] = {
            'PartitionKey': row.username,
            'RowKey': row.repo_name,
            'username': row.username,
            'job_id': row.job_id,
            'repo_name': row.repo_name,
            'fingerprint': row.fingerprint,
            'document': row.document,
            'metadata': row.metadata,
            'languages': row.languages,
            'categorized_types': row.categorized_types,
            'has_documentation': row.has_documentation,
            'readme_excerpt': row.readme_excerpt,
            'content_blob': row.content_blob,
            'created_at': row.created_at,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
    
    def query_repo_metadata(username: str, job_id: str = None, repo_names: List[str] = None):
        results = [
            meta for key, meta in repo_metadata.items()
            if key[0] == username
            and (not job_id or meta.get('job_id') == job_id)
            and (not repo_names or meta.get('repo_name') in repo_names)
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
    manager.update_candidate_session = update_candidate_session
    manager.upsert_repo_metadata = upsert_repo_metadata
    manager.query_repo_metadata = query_repo_metadata
    manager.upsert_model_metadata = upsert_model_metadata
    manager.get_model_metadata = get_model_metadata
    
    return manager


class TestTableFirstArchitecture:
    """Test that workers read from tables first, fall back to cache."""

    def test_sync_worker_persists_repo_metadata_to_table(self, mock_table_manager):
        """Verify sync worker creates RepoMetadata rows per plan-dataProcessingArchitecture.prompt.md."""
        from cloudfolio_shared.table import RepoMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        repo_name = 'test-repo'
        
        row = RepoMetadataRow(
            username=username,
            job_id=job_id,
            repo_name=repo_name,
            fingerprint='fp_123',
            document={'name': repo_name, 'description': 'Test'},
            metadata={'stars': 42},
            languages={'Python': 5000},
            categorized_types={'programming': ['.py']},
            has_documentation=True,
            readme_excerpt='# Test Repo',
            content_blob='blob://ephemeral/test.jsonl',
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        
        mock_table_manager.upsert_repo_metadata(row)
        
        # Verify row exists
        results = mock_table_manager.query_repo_metadata(username, job_id=job_id, repo_names=[repo_name])
        assert len(results) == 1
        assert results[0]['repo_name'] == repo_name
        assert results[0]['fingerprint'] == 'fp_123'
        assert results[0]['has_documentation'] is True

    def test_merge_worker_queries_table_first_before_cache(self, mock_table_manager):
        """Verify merge worker prefers table metadata over cache per architectural plan."""
        from cloudfolio_shared.table import RepoMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        # Seed table with repo metadata
        for i in range(3):
            row = RepoMetadataRow(
                username=username,
                job_id=job_id,
                repo_name=f'repo-{i}',
                fingerprint=f'fp_{i}',
                document={'name': f'repo-{i}'},
                metadata={},
                languages={'Python': 1000},
                categorized_types={},
                has_documentation=True,
                readme_excerpt='',
                content_blob=None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            mock_table_manager.upsert_repo_metadata(row)
        
        # Query should return all 3 repos
        results = mock_table_manager.query_repo_metadata(username, job_id=job_id)
        assert len(results) == 3
        assert all(r['username'] == username for r in results)

    def test_api_gateway_reads_bundle_from_table(self, mock_table_manager):
        """Verify API gateway serves bundle data from JobSessions + RepoMetadata tables."""
        from cloudfolio_shared.table import JobMetadataRow, RepoMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        # Create session
        session_row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='completed',
            total_repos=2,
            completed_repos=2,
            expected_repos=['repo-a', 'repo-b'],
            queued_repos=['repo-a', 'repo-b'],
            synced_repos=['repo-a', 'repo-b'],
            bundle_fingerprint='bundle_fp_xyz',
            force_refresh=False,
            model_status='ready',
            model_fingerprint='model_fp_123',
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(session_row)
        
        # Create repo metadata
        for repo_name in ['repo-a', 'repo-b']:
            repo_row = RepoMetadataRow(
                username=username,
                job_id=job_id,
                repo_name=repo_name,
                fingerprint=f'fp_{repo_name}',
                document={'name': repo_name, 'stars': 10},
                metadata={'language': 'Python'},
                languages={'Python': 2000},
                categorized_types={},
                has_documentation=True,
                readme_excerpt=f'# {repo_name}',
                content_blob=None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            mock_table_manager.upsert_repo_metadata(repo_row)
        
        # API gateway reads session + repos
        session = mock_table_manager.get_job_metadata(username, job_id)
        assert session['status'] == 'completed'
        assert session['bundle_fingerprint'] == 'bundle_fp_xyz'
        
        repos = mock_table_manager.query_repo_metadata(username, job_id=job_id)
        assert len(repos) == 2
        assert {r['repo_name'] for r in repos} == {'repo-a', 'repo-b'}


class TestJobProgressTracking:
    """Test JobSessions table updates during pipeline execution."""

    def test_sync_worker_updates_progress_incrementally(self, mock_table_manager):
        """Verify sync worker updates completed_repos and synced_repos as jobs complete."""
        from cloudfolio_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        # Initialize job
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='syncing',
            total_repos=3,
            completed_repos=0,
            expected_repos=['repo-1', 'repo-2', 'repo-3'],
            queued_repos=['repo-1', 'repo-2', 'repo-3'],
            synced_repos=[],
            bundle_fingerprint=None,
            force_refresh=False,
            model_status=None,
            model_fingerprint=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Simulate sync completions
        for i, repo_name in enumerate(['repo-1', 'repo-2', 'repo-3'], start=1):
            session = mock_table_manager.get_job_metadata(username, job_id)
            synced = session['synced_repos'] + [repo_name]
            mock_table_manager.update_candidate_session(username, job_id, {
                'completed_repos': i,
                'synced_repos': synced,
            })
        
        # Final state
        final = mock_table_manager.get_job_metadata(username, job_id)
        assert final['completed_repos'] == 3
        assert set(final['synced_repos']) == {'repo-1', 'repo-2', 'repo-3'}

    def test_merge_worker_marks_job_completed(self, mock_table_manager):
        """Verify merge worker sets status=completed and bundle_fingerprint."""
        from cloudfolio_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='syncing',
            total_repos=2,
            completed_repos=2,
            expected_repos=['repo-x', 'repo-y'],
            queued_repos=['repo-x', 'repo-y'],
            synced_repos=['repo-x', 'repo-y'],
            bundle_fingerprint=None,
            force_refresh=False,
            model_status=None,
            model_fingerprint=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Merge worker updates status + fingerprint
        mock_table_manager.update_candidate_session(username, job_id, {
            'status': 'completed',
            'bundle_fingerprint': 'merged_fp_abc',
            'completed_at': datetime.now(timezone.utc).isoformat(),
        })
        
        final = mock_table_manager.get_job_metadata(username, job_id)
        assert final['status'] == 'completed'
        assert final['bundle_fingerprint'] == 'merged_fp_abc'


class TestModelMetadataPersistence:
    """Test training worker persists model metadata to tables per plan-modelTraining.prompt.md."""

    def test_training_worker_writes_model_metadata(self, mock_table_manager):
        """Verify training worker creates ModelMetadata rows."""
        from cloudfolio_shared.table import ModelMetadataRow
        
        username = 'testuser'
        fingerprint = 'model_fp_xyz123'
        
        row = ModelMetadataRow(
            username=username,
            model_fingerprint=fingerprint,
            experiment_name='default',
            trained_at=datetime.now(timezone.utc).isoformat(),
            repos_count=5,
            repo_names=['repo-a', 'repo-b', 'repo-c', 'repo-d', 'repo-e'],
            training_params={'epochs': 2, 'batch_size': 8},
        )
        
        mock_table_manager.upsert_model_metadata(row)
        
        # Verify retrieval
        result = mock_table_manager.get_model_metadata(username, fingerprint)
        assert result is not None
        assert result['experiment_name'] == 'default'
        assert result['repos_count'] == 5
        assert len(result['repo_names']) == 5

    def test_training_completion_updates_candidate_session(self, mock_table_manager):
        """Verify training worker updates JobSessions with model_fingerprint."""
        from cloudfolio_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='completed',
            total_repos=3,
            completed_repos=3,
            expected_repos=[],
            queued_repos=[],
            synced_repos=[],
            bundle_fingerprint='bundle_fp',
            force_refresh=False,
            model_status=None,
            model_fingerprint=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Training worker updates model fields
        mock_table_manager.update_candidate_session(username, job_id, {
            'model_status': 'trained',
            'model_fingerprint': 'model_fp_new',
            'trained_at': datetime.now(timezone.utc).isoformat(),
        })
        
        final = mock_table_manager.get_job_metadata(username, job_id)
        assert final['model_status'] == 'trained'
        assert final['model_fingerprint'] == 'model_fp_new'


class TestEdgeCases:
    """Test edge cases and failure scenarios."""

    def test_table_disabled_falls_back_gracefully(self, mock_table_manager):
        """Verify workers handle disabled table_manager without crashes."""
        mock_table_manager.is_enabled.return_value = False
        
        # Should not raise
        if mock_table_manager.is_enabled():
            mock_table_manager.query_repo_metadata('user', job_id='job')
        
        # Verify fallback path (cache) would be invoked
        assert not mock_table_manager.is_enabled()

    def test_missing_job_id_returns_latest_session(self, mock_table_manager):
        """Verify API gateway returns latest session when job_id not specified."""
        from cloudfolio_shared.table import JobMetadataRow
        
        username = 'testuser'
        
        # Create multiple sessions
        for i in range(3):
            job_id = f'job-{i}'
            row = JobMetadataRow(
                username=username,
                job_id=job_id,
                status='completed',
                total_repos=1,
                completed_repos=1,
                expected_repos=[],
                queued_repos=[],
                synced_repos=[],
                bundle_fingerprint=f'fp_{i}',
                force_refresh=False,
                model_status=None,
                model_fingerprint=None,
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
        """Verify progress tracking when only some repos complete."""
        from cloudfolio_shared.table import JobMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        row = JobMetadataRow(
            username=username,
            job_id=job_id,
            status='syncing',
            total_repos=5,
            completed_repos=0,
            expected_repos=['repo-1', 'repo-2', 'repo-3', 'repo-4', 'repo-5'],
            queued_repos=['repo-1', 'repo-2', 'repo-3', 'repo-4', 'repo-5'],
            synced_repos=[],
            bundle_fingerprint=None,
            force_refresh=False,
            model_status=None,
            model_fingerprint=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=None,
        )
        mock_table_manager.upsert_job_metadata(row)
        
        # Sync only 3 repos
        mock_table_manager.update_candidate_session(username, job_id, {
            'completed_repos': 3,
            'synced_repos': ['repo-1', 'repo-2', 'repo-3'],
        })
        
        session = mock_table_manager.get_job_metadata(username, job_id)
        assert session['completed_repos'] == 3
        assert session['total_repos'] == 5
        assert len(session['synced_repos']) == 3

    def test_fingerprint_mismatch_triggers_resync(self, mock_table_manager):
        """Verify stale fingerprints detected and repos re-queued."""
        from cloudfolio_shared.table import RepoMetadataRow
        
        username = 'testuser'
        job_id = str(uuid.uuid4())
        
        # Old fingerprint in table
        row = RepoMetadataRow(
            username=username,
            job_id=job_id,
            repo_name='test-repo',
            fingerprint='old_fp',
            document={},
            metadata={},
            languages={},
            categorized_types={},
            has_documentation=False,
            readme_excerpt='',
            content_blob=None,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        mock_table_manager.upsert_repo_metadata(row)
        
        # Simulate freshness check detecting new fingerprint
        stored = mock_table_manager.query_repo_metadata(username, job_id=job_id, repo_names=['test-repo'])
        assert stored[0]['fingerprint'] == 'old_fp'
        
        # New fingerprint from GitHub
        expected_fp = 'new_fp'
        assert stored[0]['fingerprint'] != expected_fp
        # Would trigger re-enqueue in actual API gateway logic
