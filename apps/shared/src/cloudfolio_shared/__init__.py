"""Cloudfolio shared modules for microservices architecture.

This package provides reusable utilities for:
- AI/ML: Assistant, scoring, type analysis, fine-tuning
- Cache: Azure Blob storage management, fingerprinting
- GitHub: API client, repository management
- Queue: Azure Storage Queue operations
- Models: Pydantic schemas
- Linguist: Language detection based on GitHub Linguist

Usage:
    from cloudfolio_shared import cache_manager, GitHubAPI, queue_manager
    from cloudfolio_shared.ai import AIAssistant, RepoScoringService
"""

__version__ = "1.0.0"

# Lazy imports to avoid heavy dependencies at import time
# Users should import from submodules for specific functionality

__all__ = [
    # Submodule names for explicit imports
    "ai",
    "cache", 
    "github",
    "linguist",
    "models",
    "queue",
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
