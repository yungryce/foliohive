"""Table abstractions for foliohive shared package."""

from .table_manager import (
    AIRequestUsageRow,
    JobMetadataRow,
    SessionCandidateRow,
    RepoSyncStatusRow,
    RepoLanguagesRow,
    RepoGitHubMetadataRow,
    RepoCacheSummaryRow,
    RepoAPIUsageRow,
    UserProfileRow,
    TableManager,
    TableNames,
    get_table_manager,
    table_manager,
)

__all__ = [
    "TableManager",
    "TableNames",
    "AIRequestUsageRow",
    "JobMetadataRow",
    "SessionCandidateRow",
    "RepoSyncStatusRow",
    "RepoLanguagesRow",
    "RepoGitHubMetadataRow",
    "RepoCacheSummaryRow",
    "RepoAPIUsageRow",
    "UserProfileRow",
    "table_manager",
    "get_table_manager",
]
