"""
Unit tests for Pydantic message schemas.

Tests validation and serialization for:
- SyncJobMessage
- MergeJobMessage
- TrainingJobMessage
"""
import pytest
from datetime import datetime
from pydantic import ValidationError
from apps.shared.models.schemas import (
    SyncJobMessage,
    MergeJobMessage,
    TrainingJobMessage
)


class TestSyncJobMessage:
    """Test suite for SyncJobMessage schema."""

    def test_create_valid_sync_job_message(self):
        """Test creating a valid SyncJobMessage."""
        message = SyncJobMessage(
            job_id='job-123',
            username='testuser',
            force_refresh=True,
            requested_at='2025-01-15T12:00:00Z'
        )
        
        assert message.job_id == 'job-123'
        assert message.username == 'testuser'
        assert message.force_refresh is True
        assert message.requested_at == '2025-01-15T12:00:00Z'
        
    def test_sync_job_message_with_defaults(self):
        """Test SyncJobMessage with default values."""
        message = SyncJobMessage(
            job_id='job-123',
            username='testuser',
            requested_at='2025-01-15T12:00:00Z'
        )
        
        assert message.force_refresh is False
        
    def test_sync_job_message_missing_required_field(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            SyncJobMessage(
                job_id='job-123',
                # missing username
                requested_at='2025-01-15T12:00:00Z'
            )
        
        assert 'username' in str(exc_info.value)
        
    def test_sync_job_message_empty_username(self):
        """Test that empty username is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SyncJobMessage(
                job_id='job-123',
                username='',  # empty string
                requested_at='2025-01-15T12:00:00Z'
            )
        
        assert 'username' in str(exc_info.value)
        
    def test_sync_job_message_serialization(self):
        """Test serializing SyncJobMessage to dict."""
        message = SyncJobMessage(
            job_id='job-123',
            username='testuser',
            force_refresh=True,
            requested_at='2025-01-15T12:00:00Z'
        )
        
        data = message.model_dump()
        
        assert data['job_id'] == 'job-123'
        assert data['username'] == 'testuser'
        assert data['force_refresh'] is True
        assert data['requested_at'] == '2025-01-15T12:00:00Z'
        
    def test_sync_job_message_json_serialization(self):
        """Test serializing SyncJobMessage to JSON."""
        message = SyncJobMessage(
            job_id='job-123',
            username='testuser',
            force_refresh=False,
            requested_at='2025-01-15T12:00:00Z'
        )
        
        json_str = message.model_dump_json()
        
        assert 'job-123' in json_str
        assert 'testuser' in json_str
        assert '"force_refresh":false' in json_str


class TestMergeJobMessage:
    """Test suite for MergeJobMessage schema."""

    def test_create_valid_merge_job_message(self):
        """Test creating a valid MergeJobMessage."""
        message = MergeJobMessage(
            job_id='job-456',
            username='testuser',
            fresh_repos=[{'name': 'repo1', 'data': 'fresh'}],
            cached_bundle=[{'name': 'repo2', 'data': 'cached'}]
        )
        
        assert message.job_id == 'job-456'
        assert message.username == 'testuser'
        assert len(message.fresh_repos) == 1
        assert len(message.cached_bundle) == 1
        
    def test_merge_job_message_empty_lists(self):
        """Test MergeJobMessage with empty repo lists."""
        message = MergeJobMessage(
            job_id='job-456',
            username='testuser',
            fresh_repos=[],
            cached_bundle=[]
        )
        
        assert message.fresh_repos == []
        assert message.cached_bundle == []
        
    def test_merge_job_message_missing_required_field(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            MergeJobMessage(
                job_id='job-456',
                username='testuser',
                # missing fresh_repos
                cached_bundle=[]
            )
        
        assert 'fresh_repos' in str(exc_info.value)
        
    def test_merge_job_message_serialization(self):
        """Test serializing MergeJobMessage to dict."""
        message = MergeJobMessage(
            job_id='job-456',
            username='testuser',
            fresh_repos=[{'name': 'repo1'}],
            cached_bundle=[{'name': 'repo2'}]
        )
        
        data = message.model_dump()
        
        assert data['job_id'] == 'job-456'
        assert data['username'] == 'testuser'
        assert isinstance(data['fresh_repos'], list)
        assert isinstance(data['cached_bundle'], list)
        
    def test_merge_job_message_preserves_dict_structure(self):
        """Test that complex dict structures are preserved."""
        complex_repo = {
            'name': 'test-repo',
            'metadata': {
                'stars': 10,
                'topics': ['python', 'testing']
            },
            'files': ['README.md', 'setup.py']
        }
        
        message = MergeJobMessage(
            job_id='job-456',
            username='testuser',
            fresh_repos=[complex_repo],
            cached_bundle=[]
        )
        
        assert message.fresh_repos[0]['metadata']['stars'] == 10
        assert 'python' in message.fresh_repos[0]['metadata']['topics']


class TestTrainingJobMessage:
    """Test suite for TrainingJobMessage schema."""

    def test_create_valid_training_job_message(self):
        """Test creating a valid TrainingJobMessage."""
        message = TrainingJobMessage(
            job_id='job-789',
            username='testuser',
            repos_bundle=[{'name': 'repo1', 'readme': 'Content'}],
            training_params={'epochs': 3, 'batch_size': 32},
            experiment_name='test-experiment'
        )
        
        assert message.job_id == 'job-789'
        assert message.username == 'testuser'
        assert len(message.repos_bundle) == 1
        assert message.training_params['epochs'] == 3
        assert message.experiment_name == 'test-experiment'
        
    def test_training_job_message_with_defaults(self):
        """Test TrainingJobMessage with default values."""
        message = TrainingJobMessage(
            job_id='job-789',
            username='testuser',
            repos_bundle=[{'name': 'repo1'}]
        )
        
        assert message.training_params == {}
        assert message.experiment_name == 'default'
        
    def test_training_job_message_empty_bundle(self):
        """Test TrainingJobMessage with empty bundle."""
        message = TrainingJobMessage(
            job_id='job-789',
            username='testuser',
            repos_bundle=[]
        )
        
        assert message.repos_bundle == []
        
    def test_training_job_message_missing_required_field(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TrainingJobMessage(
                job_id='job-789',
                # missing username
                repos_bundle=[]
            )
        
        assert 'username' in str(exc_info.value)
        
    def test_training_job_message_custom_params(self):
        """Test TrainingJobMessage with custom training parameters."""
        custom_params = {
            'epochs': 5,
            'learning_rate': 0.001,
            'batch_size': 64,
            'model_name': 'custom-model'
        }
        
        message = TrainingJobMessage(
            job_id='job-789',
            username='testuser',
            repos_bundle=[],
            training_params=custom_params
        )
        
        assert message.training_params['epochs'] == 5
        assert message.training_params['learning_rate'] == 0.001
        
    def test_training_job_message_serialization(self):
        """Test serializing TrainingJobMessage to dict."""
        message = TrainingJobMessage(
            job_id='job-789',
            username='testuser',
            repos_bundle=[{'name': 'repo1'}],
            training_params={'epochs': 3},
            experiment_name='experiment-1'
        )
        
        data = message.model_dump()
        
        assert data['job_id'] == 'job-789'
        assert data['username'] == 'testuser'
        assert isinstance(data['repos_bundle'], list)
        assert isinstance(data['training_params'], dict)
        assert data['experiment_name'] == 'experiment-1'
        
    def test_training_job_message_json_serialization(self):
        """Test serializing TrainingJobMessage to JSON."""
        message = TrainingJobMessage(
            job_id='job-789',
            username='testuser',
            repos_bundle=[],
            training_params={'epochs': 3}
        )
        
        json_str = message.model_dump_json()
        
        assert 'job-789' in json_str
        assert 'testuser' in json_str
        assert '"epochs":3' in json_str


@pytest.mark.parametrize("message_class,required_fields", [
    (SyncJobMessage, {'job_id': 'test', 'username': 'user', 'requested_at': '2025-01-01T00:00:00Z'}),
    (MergeJobMessage, {'job_id': 'test', 'username': 'user', 'fresh_repos': [], 'cached_bundle': []}),
    (TrainingJobMessage, {'job_id': 'test', 'username': 'user', 'repos_bundle': []}),
])
def test_message_schemas_accept_valid_data(message_class, required_fields):
    """Parametrized test that all schemas accept valid data."""
    message = message_class(**required_fields)
    assert message.job_id == 'test'
    assert message.username == 'user'


def test_all_messages_can_roundtrip():
    """Test that all message types can serialize and deserialize."""
    sync_msg = SyncJobMessage(
        job_id='sync-1',
        username='user1',
        requested_at='2025-01-15T12:00:00Z'
    )
    
    merge_msg = MergeJobMessage(
        job_id='merge-1',
        username='user2',
        fresh_repos=[],
        cached_bundle=[]
    )
    
    training_msg = TrainingJobMessage(
        job_id='train-1',
        username='user3',
        repos_bundle=[]
    )
    
    # Serialize to JSON and back
    sync_json = sync_msg.model_dump_json()
    merge_json = merge_msg.model_dump_json()
    training_json = training_msg.model_dump_json()
    
    # Deserialize
    sync_restored = SyncJobMessage.model_validate_json(sync_json)
    merge_restored = MergeJobMessage.model_validate_json(merge_json)
    training_restored = TrainingJobMessage.model_validate_json(training_json)
    
    assert sync_restored.job_id == 'sync-1'
    assert merge_restored.job_id == 'merge-1'
    assert training_restored.job_id == 'train-1'
