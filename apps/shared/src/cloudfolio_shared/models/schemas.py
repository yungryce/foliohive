from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class SyncJobMessage(BaseModel):
    """Message for github-sync queue"""
    job_id: str = Field(..., description="Unique job identifier")
    username: str = Field(..., min_length=1, description="GitHub username")
    force_refresh: bool = Field(default=False)
    requested_at: str  # ISO timestamp

class MergeJobMessage(BaseModel):
    """Message for merge-results queue"""
    job_id: str
    username: str
    fresh_repos: List[Dict]
    cached_bundle: List[Dict]

class TrainingJobMessage(BaseModel):
    """Message for model-training queue"""
    job_id: str
    username: str
    repos_bundle: List[Dict]
    training_params: Dict = Field(default_factory=dict)
    experiment_name: str = Field(default='default')