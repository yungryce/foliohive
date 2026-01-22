"""Table abstractions for Cloudfolio shared package."""

from .table_manager import (
    JobMetadataRow,
    SessionCandidateRow,
    ModelMetadataRow,
    RepoMetadataRow,
    RepoSyncStatusRow,
    RepoLanguagesRow,
    RepoFileTypesRow,
    RepoGitHubMetadataRow,
    TableManager,
    TableNames,
    table_manager,
)

__all__ = [
    "TableManager",
    "TableNames",
    "JobMetadataRow",
    "SessionCandidateRow",
    "RepoMetadataRow",
    "RepoSyncStatusRow",
    "RepoLanguagesRow",
    "RepoFileTypesRow",
    "RepoGitHubMetadataRow",
    "ModelMetadataRow",
    "table_manager",
]
