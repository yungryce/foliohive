"""Cloudfolio shared modules.

The v0.3.0 backend is a single Azure Functions app with multiple blueprints.
This package contains the reusable logic those blueprints depend on.

Usage:
    from cloudfolio_shared import cache_manager, GitHubAPI, queue_manager
    from cloudfolio_shared import AIAssistant, RepoScoringService
"""

__version__ = "1.0.0"

# Lazy imports to avoid heavy dependencies at import time
# Users should import from submodules for specific functionality

__all__ = [
    # Submodule names for explicit imports
    "ai",
    "cache",
    "github",
    "journal",
    "queue",
    "table",
    # Version
    "__version__",
]


def __getattr__(name: str):
    """Lazy loading of commonly used classes for convenience imports."""

    # Cache utilities
    if name == "cache_manager":
        from cloudfolio_shared.cache.cache_manager import cache_manager
        return cache_manager
    if name == "CacheManager":
        from cloudfolio_shared.cache.cache_manager import CacheManager
        return CacheManager
    if name == "FingerprintManager":
        from cloudfolio_shared.cache.fingerprint_manager import FingerprintManager
        return FingerprintManager
    
    # GitHub utilities
    if name == "GitHubAPI":
        from cloudfolio_shared.github.github_api import GitHubAPI
        return GitHubAPI
    if name == "GitHubRepoManager":
        from cloudfolio_shared.github.github_repo_manager import GitHubRepoManager
        return GitHubRepoManager
    
    # Queue utilities
    if name == "queue_manager":
        from cloudfolio_shared.queue.queue_manager import queue_manager
        return queue_manager
    if name == "QueueManager":
        from cloudfolio_shared.queue.queue_manager import QueueManager
        return QueueManager
    if name == "table_manager":
        from cloudfolio_shared.table import table_manager
        return table_manager
    if name == "TableManager":
        from cloudfolio_shared.table import TableManager
        return TableManager
    if name == "get_job_journal":
        from cloudfolio_shared.journal import get_job_journal
        return get_job_journal
    if name == "JobJournal":
        from cloudfolio_shared.journal import JobJournal
        return JobJournal
    
    # AI utilities
    if name == "AIAssistant":
        from cloudfolio_shared.ai.ai_assistant import AIAssistant
        return AIAssistant
    if name == "RepoScoringService":
        from cloudfolio_shared.ai.repo_scoring_service import RepoScoringService
        return RepoScoringService
    if name == "FileTypeAnalyzer":
        from cloudfolio_shared.ai.type_analyzer import FileTypeAnalyzer
        return FileTypeAnalyzer
    
    raise AttributeError(f"module 'cloudfolio_shared' has no attribute '{name}'")
