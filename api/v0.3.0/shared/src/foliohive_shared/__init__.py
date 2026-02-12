"""Cloudfolio shared modules.

The v0.3.0 backend is a single Azure Functions app with multiple blueprints.
This package contains the reusable logic those blueprints depend on.

Usage:
    from foliohive_shared import cache_manager, GitHubAPI, queue_manager
    from foliohive_shared import AIAssistant, SummaryManager
"""

__version__ = "1.0.0"

# Lazy imports to avoid heavy dependencies at import time
# Users should import from submodules for specific functionality

__all__ = [
    # Submodule names for explicit imports
    "ai",
    "cache",
    "github",
    "queue",
    "table",
    # Version
    "__version__",
]


def __getattr__(name: str):
    """Lazy loading of commonly used classes for convenience imports."""

    # Cache utilities
    if name == "cache_manager":
        from foliohive_shared.cache.cache_manager import cache_manager
        return cache_manager
    if name == "CacheManager":
        from foliohive_shared.cache.cache_manager import CacheManager
        return CacheManager
    if name == "FingerprintManager":
        from foliohive_shared.cache.fingerprint_manager import FingerprintManager
        return FingerprintManager
    
    # GitHub utilities
    if name == "GitHubAPI":
        from foliohive_shared.github.github_api import GitHubAPI
        return GitHubAPI
    if name == "GitHubRepoManager":
        from foliohive_shared.github.github_repo_manager import GitHubRepoManager
        return GitHubRepoManager
    if name == "ApiUsageTracker":
        from foliohive_shared.github.api_usage import ApiUsageTracker
        return ApiUsageTracker
    
    # Queue utilities
    if name == "queue_manager":
        from foliohive_shared.queue.queue_manager import queue_manager
        return queue_manager
    if name == "QueueManager":
        from foliohive_shared.queue.queue_manager import QueueManager
        return QueueManager
    
    # Table utilities
    if name == "table_manager":
        from foliohive_shared.table import table_manager
        return table_manager
    if name == "TableManager":
        from foliohive_shared.table import TableManager
        return TableManager
    if name == "JobMetadataRow":
        from foliohive_shared.table import JobMetadataRow
        return JobMetadataRow
    if name == "RepoSyncStatusRow":
        from foliohive_shared.table import RepoSyncStatusRow
        return RepoSyncStatusRow
    if name == "RepoAPIUsageRow":
        from foliohive_shared.table import RepoAPIUsageRow
        return RepoAPIUsageRow
    if name == "UserProfileRow":
        from foliohive_shared.table import UserProfileRow
        return UserProfileRow
    if name == "RepoDiscoveredPathsRow":
        from foliohive_shared.table import RepoDiscoveredPathsRow
        return RepoDiscoveredPathsRow
    
    # AI utilities
    if name == "AIAssistant":
        from foliohive_shared.ai.ai_assistant import AIAssistant
        return AIAssistant
    if name == "SummaryManager":
        from foliohive_shared.ai.summary_manager import SummaryManager
        return SummaryManager
    if name == "get_file_budget":
        from foliohive_shared.ai.summary_manager import get_file_budget
        return get_file_budget
    
    raise AttributeError(f"module 'foliohive_shared' has no attribute '{name}'")
