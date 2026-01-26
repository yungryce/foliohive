"""Table abstractions for Cloudfolio shared package."""

from .table_manager import (
    JobMetadataRow,
    SessionCandidateRow,
    ModelMetadataRow,
    RepoSyncStatusRow,
    RepoLanguagesRow,
    RepoFileTypesRow,
    RepoGitHubMetadataRow,
    RepoAPIUsageRow,
    TableManager,
    TableNames,
    table_manager,
)

__all__ = [
    "TableManager",
    "TableNames",
    "JobMetadataRow",
    "SessionCandidateRow",
    "RepoSyncStatusRow",
    "RepoLanguagesRow",
    "RepoFileTypesRow",
    "RepoGitHubMetadataRow",
    "RepoAPIUsageRow",
    "ModelMetadataRow",
    "table_manager",
]
