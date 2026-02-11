"""Centralized repository file cache retrieval and key generation.

This module provides a unified interface for retrieving cached repository files
(readme, config) with consistent key generation across cache_worker and api_gateway.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Set

from .cache_manager import cache_manager

logger = logging.getLogger(__name__)


class RepoCacheRetrieval:
    """Manages cache key generation and retrieval for repository files.
    
    Handles three distinct file types with proper cache keys:
    - Primary readme: Main README.md at repo root
    - Readme files: README files discovered via path index (in subdirectories)
    - Config files: Configuration files discovered via path index
    
    This class ensures consistent key generation between:
    - cache_worker.py (file caching)
    - api_gateway.py (file retrieval)
    """
    
    def __init__(self):
        self.cache_manager = cache_manager

    
    def get_primary_readme(
        self,
        username: str,
        repo: str,
    ) -> Optional[str]:
        """Retrieve primary README content from cache.
        
        Args:
            username: GitHub username
            repo: Repository name
            
        Returns:
            README content if cached, None otherwise
        """
        key = cache_manager.generate_cache_key(
            username=username,
            repo=repo,
            file_type="readme",
            filename="PRIMARY",
        )
        result = self.cache_manager.get(key)
        
        if result.get("status") == "valid":
            return result.get("data")
        
        return None
    
    def get_readme_files(
        self,
        username: str,
        repo: str,
        paths: List[str],
    ) -> Dict[str, str]:
        """Retrieve multiple readme files from cache.
        
        Args:
            username: GitHub username
            repo: Repository name
            paths: List of full paths to readme files
            
        Returns:
            Dict mapping path to content for successfully retrieved files
        """
        results = {}
        
        for path in paths:
            key = cache_manager.generate_cache_key(
                username=username,
                repo=repo,
                file_type="readme",
                filename=path,
            )
            result = self.cache_manager.get(key)
            
            if result.get("status") == "valid":
                content = result.get("data")
                if content:
                    results[path] = content
        
        return results
    
    def get_config_files(
        self,
        username: str,
        repo: str,
        paths: List[str],
    ) -> Dict[str, str]:
        """Retrieve multiple config files from cache.
        
        Args:
            username: GitHub username
            repo: Repository name
            paths: List of full paths to config files
            
        Returns:
            Dict mapping filename to content for successfully retrieved files
        """
        results = {}
        
        for path in paths:
            key = cache_manager.generate_cache_key(
                username=username,
                repo=repo,
                file_type="config",
                filename=path,
            )
            result = self.cache_manager.get(key)
            
            if result.get("status") == "valid":
                content = result.get("data")
                if content:
                    display_name = path.split("/")[-1]
                    results[display_name] = content
                    logger.debug(
                        "Retrieved config from cache: repo=%s path=%s",
                        repo, path
                    )
        
        return results
    
    def get_repo_files(
        self,
        username: str,
        repo: str,
        *,
        discovered_paths: Optional[List[str]] = None,
        readme_candidates: Optional[List[str]] = None,
        max_readme_files: int = 3,
        max_config_files: int = 5,
        include_readme: bool = True,
    ) -> Dict[str, Any]:
        """Retrieve all cached files for a repository.
        
        This is the main retrieval method that combines primary readme,
        additional readme files, and config files.
        
        Args:
            username: GitHub username
            repo: Repository name
            discovered_paths: List of file paths discovered via path index.
                             If None, only primary readme is retrieved.
            readme_candidates: List of readme filenames to identify (e.g., ["README.md"])
            max_readme_files: Maximum number of additional readme files to retrieve
            max_config_files: Maximum number of config files to retrieve
            include_readme: Whether to retrieve primary readme content (default: True)
            
        Returns:
            Dict with keys:
            - repo_name: Repository name
            - readme_content: Primary readme content (or None) if include_readme=True
            - readme_files: Dict mapping paths to content for additional readmes
            - config_files: Dict mapping filenames to content for config files
        """
        result = {
            "repo_name": repo,
            "readme_content": None,
            "readme_files": {},
            "config_files": {},
        }
        
        # Retrieve primary readme only if requested
        if include_readme:
            result["readme_content"] = self.get_primary_readme(username, repo)
        
        # If no discovered paths provided, return with just primary readme
        if not discovered_paths:
            return result
        
        # Separate paths into readme vs config
        readme_set = set()
        if readme_candidates:
            readme_set = {name.lower() for name in readme_candidates}
        
        readme_paths = []
        config_paths = []
        
        for path in discovered_paths:
            filename = os.path.basename(path).lower()
            if filename in readme_set:
                readme_paths.append(path)
            else:
                config_paths.append(path)
        
        # Retrieve readme files (excluding primary) with limit
        if readme_paths:
            limited_readme_paths = readme_paths[:max_readme_files]
            result["readme_files"] = self.get_readme_files(
                username, repo, limited_readme_paths
            )
        
        # Retrieve config files with limit
        if config_paths:
            limited_config_paths = config_paths[:max_config_files]
            result["config_files"] = self.get_config_files(
                username, repo, limited_config_paths
            )
        
        return result
    
    def get_multiple_repos_files(
        self,
        username: str,
        repo_discovery_map: Dict[str, List[str]],
        *,
        readme_candidates: Optional[List[str]] = None,
        max_readme_files: int = 3,
        max_config_files: int = 5,
        include_readme: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """Retrieve cached files for multiple repositories.
        
        Args:
            username: GitHub username
            repo_discovery_map: Dict mapping repo name to list of discovered paths
            readme_candidates: List of readme filenames to identify
            max_readme_files: Maximum additional readme files per repo
            max_config_files: Maximum config files per repo
            include_readme: Whether to retrieve primary readme content (default: True)
            
        Returns:
            Dict mapping repo name to file retrieval results
        """
        results = {}
        
        for repo, paths in repo_discovery_map.items():
            results[repo] = self.get_repo_files(
                username=username,
                repo=repo,
                discovered_paths=paths,
                readme_candidates=readme_candidates,
                max_readme_files=max_readme_files,
                max_config_files=max_config_files,
                include_readme=include_readme,
            )
        
        return results


# Global singleton instance for convenience
repo_cache_retrieval = RepoCacheRetrieval()
