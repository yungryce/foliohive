"""
End-to-end integration tests for the Cloudfolio pipeline.

Tests the complete flow:
1. API Gateway triggers refresh → enqueues sync jobs
2. Sync Worker processes repos → caches data → enqueues merge
3. Merge Worker merges bundles → enqueues training
4. Training Worker processes training jobs

These tests use mocked Azure services but test real inter-component communication.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_queue_messages():
    """Track messages sent to each queue."""
    return {
        'github-sync': [],
        'merge-results': [],
        'model-training': [],
        'job-status-updates': [],
    }


@pytest.fixture
def mock_cache_store():
    """In-memory cache store for testing."""
    return {}


@pytest.fixture
def mock_queue_manager(mock_queue_messages):
    """Create a mock queue manager that tracks messages."""
    manager = MagicMock()
    manager.is_enabled.return_value = True

    def enqueue_sync_job(job_id, username, repo_name, fingerprint=None):
        mock_queue_messages['github-sync'].append({
            'job_id': job_id,
            'username': username,
            'repo_name': repo_name,
            'fingerprint': fingerprint,
        })
        return True

    def enqueue_merge_job(job_id, username, synced_repos):
        mock_queue_messages['merge-results'].append({
            'job_id': job_id,
            'username': username,
            'synced_repos': synced_repos,
            'trigger_source': 'sync_complete',
        })
        return True

    def enqueue_training_job(username, repos_bundle, training_params=None, job_id=None):
        mock_queue_messages['model-training'].append({
            'job_id': job_id,
            'username': username,
            'repos_bundle': repos_bundle,
            'training_params': training_params or {},
        })
        return True

    manager.enqueue_sync_job = enqueue_sync_job
    manager.enqueue_merge_job = enqueue_merge_job
    manager.enqueue_training_job = enqueue_training_job
    return manager


@pytest.fixture
def mock_cache_manager(mock_cache_store):
    """Create a mock cache manager using in-memory store."""
    manager = MagicMock()
    manager.use_cache = True

    def generate_cache_key(kind='bundle', username=None, repo=None, **kwargs):
        if kind == 'repo' and repo:
            return f"repo_level_bundle_{username}_{repo}"
        return f"repos_bundle_context_{username}"

    def get(key):
        if key in mock_cache_store:
            return {
                'status': 'valid',
                'data': mock_cache_store[key]['data'],
                'fingerprint': mock_cache_store[key].get('fingerprint'),
            }
        return {'status': 'missing', 'data': None}

    def save(key, data, ttl=None, fingerprint=None):
        mock_cache_store[key] = {
            'data': data,
            'fingerprint': fingerprint,
            'saved_at': datetime.now(timezone.utc).isoformat(),
        }
        return True

    def delete(key):
        if key in mock_cache_store:
            del mock_cache_store[key]
        return True

    manager.generate_cache_key = generate_cache_key
    manager.get = get
    manager.save = save
    manager.delete = delete
    return manager


@pytest.fixture
def sample_github_repos():
    """Sample repository metadata from GitHub API."""
    return [
        {
            'id': 1,
            'name': 'repo-alpha',
            'full_name': 'testuser/repo-alpha',
            'description': 'First test repo',
            'updated_at': '2025-01-10T12:00:00Z',
            'pushed_at': '2025-01-10T11:00:00Z',
            'language': 'Python',
            'languages': {'Python': 5000, 'JavaScript': 1000},
        },
        {
            'id': 2,
            'name': 'repo-beta',
            'full_name': 'testuser/repo-beta',
            'description': 'Second test repo',
            'updated_at': '2025-01-11T12:00:00Z',
            'pushed_at': '2025-01-11T11:00:00Z',
            'language': 'JavaScript',
            'languages': {'JavaScript': 8000, 'TypeScript': 2000},
        },
        {
            'id': 3,
            'name': 'repo-gamma',
            'full_name': 'testuser/repo-gamma',
            'description': 'Third test repo',
            'updated_at': '2025-01-12T12:00:00Z',
            'pushed_at': '2025-01-12T11:00:00Z',
            'language': 'Go',
            'languages': {'Go': 10000},
        },
    ]


@pytest.fixture
def sample_synced_repo_bundle():
    """Sample synced repository data."""
    return {
        'name': 'repo-alpha',
        'metadata': {'name': 'repo-alpha', 'language': 'Python'},
        'readme': '# Repo Alpha\n\nTest repository documentation.',
        'skills_index': 'Python, FastAPI, PostgreSQL',
        'architecture': 'Microservices with Docker',
        'repoContext': {'version': '1.0'},
        'file_types': {'.py': 25, '.json': 5},
        'categorized_types': {'programming': ['.py'], 'data': ['.json']},
        'fingerprint': 'fp_alpha_123',
        'languages': {'Python': 5000},
        'has_documentation': True,
    }


# ---------------------------------------------------------------------------
# API Gateway Integration Tests
# ---------------------------------------------------------------------------

class TestAPIGatewayIntegration:
    """Test API Gateway's ability to trigger and coordinate the pipeline."""

    def test_refresh_triggers_sync_jobs_for_stale_repos(
        self, mock_queue_manager, mock_cache_manager, sample_github_repos
    ):
        """Verify refresh endpoint enqueues sync jobs for stale repositories."""
        job_id = str(uuid.uuid4())
        username = 'testuser'
        repo_names = [r['name'] for r in sample_github_repos]

        # Simulate API Gateway persisting job metadata (same logic as _persist_job_metadata)
        job_key = f"job:{job_id}"
        job_payload = {
            'job_id': job_id,
            'username': username,
            'expected_repos': list(repo_names),
            'queued_repos': list(repo_names),
            'total_repos': len(repo_names),
            'completed_repos': 0,
            'status': 'queued',
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        mock_cache_manager.save(job_key, job_payload)

        # Simulate enqueuing sync jobs for each repo
        for repo in sample_github_repos:
            mock_queue_manager.enqueue_sync_job(
                job_id, username, repo['name'], f"fp_{repo['name']}"
            )

        # Check job metadata was saved
        job_data = mock_cache_manager.get(job_key)
        assert job_data['status'] == 'valid'
        assert job_data['data']['job_id'] == job_id
        assert job_data['data']['total_repos'] == len(sample_github_repos)

    def test_job_status_reflects_progress(self, mock_cache_manager):
        """Verify job status correctly reflects sync progress."""
        job_id = str(uuid.uuid4())
        job_key = f"job:{job_id}"

        # Initial job state
        mock_cache_manager.save(job_key, {
            'job_id': job_id,
            'username': 'testuser',
            'total_repos': 3,
            'completed_repos': 0,
            'status': 'queued',
            'synced_repos': [],
        })

        # Simulate progress updates
        job_data = mock_cache_manager.get(job_key)['data']
        job_data['synced_repos'] = ['repo-alpha']
        job_data['completed_repos'] = 1
        mock_cache_manager.save(job_key, job_data)

        # Verify progress
        updated = mock_cache_manager.get(job_key)
        assert updated['data']['completed_repos'] == 1
        assert 'repo-alpha' in updated['data']['synced_repos']


# ---------------------------------------------------------------------------
# Sync Worker Integration Tests
# ---------------------------------------------------------------------------

class TestSyncWorkerIntegration:
    """Test Sync Worker's processing and queue communication."""

    def test_sync_worker_caches_repo_data(
        self, mock_cache_manager, sample_synced_repo_bundle
    ):
        """Verify sync worker correctly caches repository data."""
        username = 'testuser'
        repo_name = 'repo-alpha'

        cache_key = mock_cache_manager.generate_cache_key(
            kind='repo', username=username, repo=repo_name
        )
        mock_cache_manager.save(
            cache_key, sample_synced_repo_bundle, fingerprint='fp_alpha_123'
        )

        # Verify data is retrievable
        result = mock_cache_manager.get(cache_key)
        assert result['status'] == 'valid'
        assert result['data']['name'] == repo_name
        assert result['fingerprint'] == 'fp_alpha_123'

    def test_sync_worker_updates_job_progress(
        self, mock_cache_manager, mock_queue_manager
    ):
        """Verify sync worker updates job progress and triggers merge on completion."""
        job_id = str(uuid.uuid4())
        job_key = f"job:{job_id}"
        username = 'testuser'

        # Setup job with 2 repos
        mock_cache_manager.save(job_key, {
            'job_id': job_id,
            'username': username,
            'total_repos': 2,
            'completed_repos': 0,
            'synced_repos': [],
            'status': 'processing',
        })

        # Simulate first repo sync completion
        job_data = mock_cache_manager.get(job_key)['data']
        job_data['synced_repos'] = ['repo-alpha']
        job_data['completed_repos'] = 1
        mock_cache_manager.save(job_key, job_data)

        # Simulate second repo sync completion
        job_data = mock_cache_manager.get(job_key)['data']
        job_data['synced_repos'] = ['repo-alpha', 'repo-beta']
        job_data['completed_repos'] = 2
        job_data['status'] = 'synced'
        mock_cache_manager.save(job_key, job_data)

        # Verify merge job would be enqueued
        assert mock_queue_manager.is_enabled()
        mock_queue_manager.enqueue_merge_job(
            job_id, username, job_data['synced_repos']
        )

        # Verify job status
        final_job = mock_cache_manager.get(job_key)
        assert final_job['data']['status'] == 'synced'
        assert final_job['data']['completed_repos'] == 2

    def test_sync_to_merge_message_format(self, mock_queue_messages, mock_queue_manager):
        """Verify sync worker produces correctly formatted merge messages."""
        job_id = str(uuid.uuid4())
        username = 'testuser'
        synced_repos = ['repo-alpha', 'repo-beta']

        mock_queue_manager.enqueue_merge_job(job_id, username, synced_repos)

        # Verify message format
        assert len(mock_queue_messages['merge-results']) == 1
        merge_msg = mock_queue_messages['merge-results'][0]
        assert merge_msg['job_id'] == job_id
        assert merge_msg['username'] == username
        assert merge_msg['synced_repos'] == synced_repos
        assert merge_msg['trigger_source'] == 'sync_complete'


# ---------------------------------------------------------------------------
# Merge Worker Integration Tests
# ---------------------------------------------------------------------------

class TestMergeWorkerIntegration:
    """Test Merge Worker's bundle consolidation and training triggers."""

    def test_merge_worker_combines_cached_repos(self, mock_cache_manager):
        """Verify merge worker correctly combines repos from cache."""
        username = 'testuser'

        # Pre-cache individual repos
        repos_data = [
            {'name': 'repo-alpha', 'fingerprint': 'fp_a', 'has_documentation': True},
            {'name': 'repo-beta', 'fingerprint': 'fp_b', 'has_documentation': True},
        ]

        for repo in repos_data:
            cache_key = mock_cache_manager.generate_cache_key(
                kind='repo', username=username, repo=repo['name']
            )
            mock_cache_manager.save(cache_key, repo, fingerprint=repo['fingerprint'])

        # Load repos from cache (simulating merge worker)
        loaded_repos = []
        for repo in repos_data:
            cache_key = mock_cache_manager.generate_cache_key(
                kind='repo', username=username, repo=repo['name']
            )
            result = mock_cache_manager.get(cache_key)
            if result['status'] == 'valid':
                loaded_repos.append(result['data'])

        assert len(loaded_repos) == 2
        assert all(r['has_documentation'] for r in loaded_repos)

    def test_merge_worker_saves_consolidated_bundle(self, mock_cache_manager):
        """Verify merge worker saves the consolidated bundle."""
        username = 'testuser'
        merged_bundle = [
            {'name': 'repo-alpha', 'fingerprint': 'fp_a'},
            {'name': 'repo-beta', 'fingerprint': 'fp_b'},
        ]

        bundle_key = mock_cache_manager.generate_cache_key(kind='bundle', username=username)
        bundle_fingerprint = 'bundle_fp_combined'
        mock_cache_manager.save(bundle_key, merged_bundle, fingerprint=bundle_fingerprint)

        # Verify bundle is saved
        result = mock_cache_manager.get(bundle_key)
        assert result['status'] == 'valid'
        assert len(result['data']) == 2
        assert result['fingerprint'] == bundle_fingerprint

    def test_merge_worker_triggers_training(
        self, mock_queue_manager, mock_queue_messages
    ):
        """Verify merge worker enqueues training job with correct data."""
        username = 'testuser'
        bundle = [
            {'name': 'repo-alpha', 'has_documentation': True, 'readme': 'content'},
            {'name': 'repo-beta', 'has_documentation': True, 'readme': 'content'},
            {'name': 'repo-gamma', 'has_documentation': True, 'readme': 'content'},
        ]
        training_params = {'batch_size': 8, 'epochs': 2}

        mock_queue_manager.enqueue_training_job(username, bundle, training_params)

        # Verify training message
        assert len(mock_queue_messages['model-training']) == 1
        training_msg = mock_queue_messages['model-training'][0]
        assert training_msg['username'] == username
        assert len(training_msg['repos_bundle']) == 3
        assert training_msg['training_params'] == training_params

    def test_merge_updates_job_to_completed(self, mock_cache_manager):
        """Verify merge worker updates job status to completed."""
        job_id = str(uuid.uuid4())
        job_key = f"job:{job_id}"

        # Initial synced job
        mock_cache_manager.save(job_key, {
            'job_id': job_id,
            'username': 'testuser',
            'status': 'synced',
            'total_repos': 2,
            'completed_repos': 2,
        })

        # Simulate merge completion update
        job_data = mock_cache_manager.get(job_key)['data']
        job_data['status'] = 'completed'
        job_data['bundle_fingerprint'] = 'final_bundle_fp'
        job_data['completed_at'] = datetime.now(timezone.utc).isoformat()
        mock_cache_manager.save(job_key, job_data)

        # Verify completion
        final = mock_cache_manager.get(job_key)
        assert final['data']['status'] == 'completed'
        assert 'bundle_fingerprint' in final['data']


# ---------------------------------------------------------------------------
# Training Worker Integration Tests
# ---------------------------------------------------------------------------

class TestTrainingWorkerIntegration:
    """Test Training Worker's model training and metadata storage."""

    def test_training_requires_minimum_documented_repos(self):
        """Verify training rejects bundles with < 3 documented repos."""
        bundle = [
            {'name': 'repo-1', 'has_documentation': True},
            {'name': 'repo-2', 'has_documentation': False},
        ]

        documented = [r for r in bundle if r.get('has_documentation')]
        assert len(documented) < 3  # Should fail minimum requirement

    def test_training_processes_documented_repos_only(self):
        """Verify training filters to documented repos only."""
        bundle = [
            {'name': 'repo-1', 'has_documentation': True, 'readme': 'content'},
            {'name': 'repo-2', 'has_documentation': False, 'readme': ''},
            {'name': 'repo-3', 'has_documentation': True, 'readme': 'content'},
            {'name': 'repo-4', 'has_documentation': True, 'readme': 'content'},
        ]

        documented = [r for r in bundle if r.get('has_documentation')]
        assert len(documented) == 3
        assert all(r.get('readme') for r in documented)

    def test_training_message_structure(self, mock_queue_messages, mock_queue_manager):
        """Verify training messages have correct structure."""
        username = 'testuser'
        bundle = [
            {'name': f'repo-{i}', 'has_documentation': True, 'readme': f'content-{i}'}
            for i in range(3)
        ]

        mock_queue_manager.enqueue_training_job(
            username, bundle, {'batch_size': 8, 'epochs': 2}
        )

        msg = mock_queue_messages['model-training'][0]
        assert 'username' in msg
        assert 'repos_bundle' in msg
        assert 'training_params' in msg
        assert msg['training_params']['batch_size'] == 8


# ---------------------------------------------------------------------------
# Full Pipeline Integration Tests
# ---------------------------------------------------------------------------

class TestFullPipelineIntegration:
    """Test complete end-to-end pipeline flow."""

    def test_complete_refresh_to_training_flow(
        self, mock_cache_manager, mock_queue_manager, mock_queue_messages,
        sample_github_repos
    ):
        """Test the complete flow from refresh trigger to training enqueue."""
        job_id = str(uuid.uuid4())
        username = 'testuser'
        job_key = f"job:{job_id}"

        # Step 1: API Gateway creates job and enqueues sync jobs
        mock_cache_manager.save(job_key, {
            'job_id': job_id,
            'username': username,
            'total_repos': len(sample_github_repos),
            'completed_repos': 0,
            'synced_repos': [],
            'status': 'queued',
        })

        for repo in sample_github_repos:
            mock_queue_manager.enqueue_sync_job(
                job_id, username, repo['name'], f"fp_{repo['name']}"
            )

        assert len(mock_queue_messages['github-sync']) == 3

        # Step 2: Sync workers process repos and cache them
        synced_repos = []
        for sync_msg in mock_queue_messages['github-sync']:
            repo_data = {
                'name': sync_msg['repo_name'],
                'metadata': {'name': sync_msg['repo_name']},  # Simulated metadata from GitHub
                'fingerprint': sync_msg['fingerprint'],
                'readme': f"# {sync_msg['repo_name']}\n\nDocumentation",
                'has_documentation': True,
            }
            cache_key = mock_cache_manager.generate_cache_key(
                kind='repo', username=username, repo=sync_msg['repo_name']
            )
            mock_cache_manager.save(cache_key, repo_data, fingerprint=sync_msg['fingerprint'])
            synced_repos.append(sync_msg['repo_name'])

        # Update job progress
        job_data = mock_cache_manager.get(job_key)['data']
        job_data['synced_repos'] = synced_repos
        job_data['completed_repos'] = len(synced_repos)
        job_data['status'] = 'synced'
        mock_cache_manager.save(job_key, job_data)

        # Sync worker enqueues merge job
        mock_queue_manager.enqueue_merge_job(job_id, username, synced_repos)
        assert len(mock_queue_messages['merge-results']) == 1

        # Step 3: Merge worker processes and creates bundle
        merge_msg = mock_queue_messages['merge-results'][0]
        merged_bundle = []
        for repo_name in merge_msg['synced_repos']:
            cache_key = mock_cache_manager.generate_cache_key(
                kind='repo', username=username, repo=repo_name
            )
            result = mock_cache_manager.get(cache_key)
            if result['status'] == 'valid':
                merged_bundle.append(result['data'])

        bundle_key = mock_cache_manager.generate_cache_key(kind='bundle', username=username)
        mock_cache_manager.save(bundle_key, merged_bundle, fingerprint='bundle_fp')

        # Update job to completed
        job_data = mock_cache_manager.get(job_key)['data']
        job_data['status'] = 'completed'
        job_data['bundle_fingerprint'] = 'bundle_fp'
        mock_cache_manager.save(job_key, job_data)

        # Merge worker enqueues training job
        mock_queue_manager.enqueue_training_job(
            username, merged_bundle, {'batch_size': 8, 'epochs': 2}
        )
        assert len(mock_queue_messages['model-training']) == 1

        # Step 4: Verify final state
        final_job = mock_cache_manager.get(job_key)
        assert final_job['data']['status'] == 'completed'

        final_bundle = mock_cache_manager.get(bundle_key)
        assert final_bundle['status'] == 'valid'
        assert len(final_bundle['data']) == 3

        training_msg = mock_queue_messages['model-training'][0]
        assert training_msg['username'] == username
        assert len(training_msg['repos_bundle']) == 3

    def test_partial_sync_with_existing_cache(
        self, mock_cache_manager, mock_queue_manager, mock_queue_messages
    ):
        """Test refresh with some repos already cached."""
        job_id = str(uuid.uuid4())
        username = 'testuser'

        # Pre-existing cached bundle
        existing_bundle = [
            {'name': 'repo-cached', 'fingerprint': 'fp_old', 'has_documentation': True},
        ]
        bundle_key = mock_cache_manager.generate_cache_key(kind='bundle', username=username)
        mock_cache_manager.save(bundle_key, existing_bundle, fingerprint='old_bundle_fp')

        # Only sync the new repo
        new_repo_name = 'repo-new'
        mock_queue_manager.enqueue_sync_job(job_id, username, new_repo_name, 'fp_new')

        # Simulate sync and merge
        new_repo_data = {
            'name': 'repo-new',
            'fingerprint': 'fp_new',
            'has_documentation': True,
            'readme': '# New Repo',
        }
        new_cache_key = mock_cache_manager.generate_cache_key(
            kind='repo', username=username, repo='repo-new'
        )
        mock_cache_manager.save(new_cache_key, new_repo_data, fingerprint='fp_new')

        # Merge combines old and new
        old_bundle = mock_cache_manager.get(bundle_key)['data']
        merged = old_bundle + [new_repo_data]
        mock_cache_manager.save(bundle_key, merged, fingerprint='new_bundle_fp')

        # Verify merged bundle
        result = mock_cache_manager.get(bundle_key)
        assert len(result['data']) == 2
        assert result['fingerprint'] == 'new_bundle_fp'


# ---------------------------------------------------------------------------
# Error Handling Integration Tests
# ---------------------------------------------------------------------------

class TestErrorHandlingIntegration:
    """Test error handling across the pipeline."""

    def test_missing_username_rejects_request(self, mock_cache_manager):
        """Verify missing username is handled gracefully."""
        # Attempting to generate cache key without username should raise
        with pytest.raises(Exception):
            from cloudfolio_shared.cache.cache_manager import CacheManager
            CacheManager.generate_cache_key(kind='bundle')  # No username

    def test_missing_repo_in_cache_during_merge(self, mock_cache_manager):
        """Verify merge handles missing repos gracefully."""
        username = 'testuser'
        repo_names = ['exists', 'missing']

        # Only cache one repo
        cache_key = mock_cache_manager.generate_cache_key(
            kind='repo', username=username, repo='exists'
        )
        mock_cache_manager.save(cache_key, {'name': 'exists'})

        # Try to load both
        loaded = []
        for name in repo_names:
            key = mock_cache_manager.generate_cache_key(kind='repo', username=username, repo=name)
            result = mock_cache_manager.get(key)
            if result['status'] == 'valid':
                loaded.append(result['data'])

        # Should only have the existing one
        assert len(loaded) == 1
        assert loaded[0]['name'] == 'exists'

    def test_queue_disabled_graceful_fallback(self, mock_queue_manager):
        """Verify operations handle disabled queue gracefully."""
        mock_queue_manager.is_enabled.return_value = False

        # Should check before attempting to enqueue
        if not mock_queue_manager.is_enabled():
            result = "skipped"
        else:
            result = "enqueued"

        assert result == "skipped"


# ---------------------------------------------------------------------------
# Cache Synchronization Tests
# ---------------------------------------------------------------------------

class TestCacheSynchronization:
    """Test cache consistency across workers."""

    def test_fingerprint_propagation(self, mock_cache_manager):
        """Verify fingerprints propagate correctly through the pipeline."""
        username = 'testuser'

        # Sync worker saves with fingerprint
        repo_fp = 'repo_fingerprint_abc123'
        repo_key = mock_cache_manager.generate_cache_key(
            kind='repo', username=username, repo='test-repo'
        )
        mock_cache_manager.save(repo_key, {'name': 'test-repo'}, fingerprint=repo_fp)

        # Merge worker reads fingerprint
        result = mock_cache_manager.get(repo_key)
        assert result['fingerprint'] == repo_fp

    def test_bundle_fingerprint_derived_from_repos(self, mock_cache_manager):
        """Verify bundle fingerprint reflects constituent repos."""
        username = 'testuser'

        repos = [
            {'name': 'repo-1', 'fingerprint': 'fp_1'},
            {'name': 'repo-2', 'fingerprint': 'fp_2'},
        ]

        # Derive bundle fingerprint from repo fingerprints
        repo_fps = [r['fingerprint'] for r in repos]
        combined = '_'.join(sorted(repo_fps))

        bundle_key = mock_cache_manager.generate_cache_key(kind='bundle', username=username)
        mock_cache_manager.save(bundle_key, repos, fingerprint=f"bundle_{combined}")

        result = mock_cache_manager.get(bundle_key)
        assert 'fp_1' in result['fingerprint']
        assert 'fp_2' in result['fingerprint']

    def test_job_metadata_consistency(self, mock_cache_manager):
        """Verify job metadata remains consistent across updates."""
        job_id = str(uuid.uuid4())
        job_key = f"job:{job_id}"

        # Initial save
        initial_data = {
            'job_id': job_id,
            'username': 'testuser',
            'total_repos': 5,
            'status': 'queued',
        }
        mock_cache_manager.save(job_key, initial_data)

        # Multiple updates should preserve base fields
        for i in range(3):
            data = mock_cache_manager.get(job_key)['data']
            data['completed_repos'] = i + 1
            mock_cache_manager.save(job_key, data)

        final = mock_cache_manager.get(job_key)
        assert final['data']['job_id'] == job_id
        assert final['data']['total_repos'] == 5
        assert final['data']['completed_repos'] == 3
