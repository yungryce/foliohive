import logging
import json
import os
import requests
import time
from typing import Dict, Any, List, Optional
from .github_api import GitHubAPI
from .fingerprint_manager import FingerprintManager
from .cache_manager import cache_manager
from fa_helpers import trim_processed_repo


logger = logging.getLogger('portfolio.api')

class GitHubRepoManager:
    def __init__(self, api: GitHubAPI, username: Optional[str] = None):
        """Initialize the GitHubRepoManager with API, cache, and file manager."""
        self.api = api
        self.username = username

    @cache_manager.cache_decorator(cache_key_func=lambda username, repo, **kwargs: f"repo_metadata:{username}:{repo}", ttl=3600)
    def get_repo_metadata(self, username: Optional[str]=None, repo: Optional[str]=None, include_languages: bool=False) -> Dict[str, Any]:
        """Get metadata for a specific repository.

        Args:
            username (str, optional): GitHub username. Defaults to None.
            repo (str, optional): Repository name. Defaults to None.
            include_languages (bool, optional): Whether to include language statistics. Defaults to False.

        Raises:
            ValueError: If repository name is not provided.

        Returns:
            dict: Repository metadata or None if not found.
        """
        username = username or self.username
        if not repo:
            raise ValueError("Repository name is required")
        endpoint = f"repos/{username}/{repo}"
        repo_data = self.api.make_request('GET', endpoint)
        if not isinstance(repo_data, dict):
            raise ValueError("Invalid response format for repository metadata")
        if include_languages:
            languages = self.api.make_request('GET', f"{endpoint}/languages")
            if isinstance(languages, dict):
                repo_data['languages'] = languages
        return repo_data

    @cache_manager.cache_decorator(cache_key_func=lambda username=None, **kwargs: f"repos_metadata:{username}:all", ttl=3600)
    def get_all_repos_metadata(self, username: Optional[str]=None, per_page=100, include_languages: bool=False) -> List[Dict[str, Any]]:
        """Get metadata for all repositories.

        Args:
            username (str, optional): GitHub username. Defaults to None.
            per_page (int, optional): Number of repositories per page. Defaults to 100.
            include_languages (bool, optional): Whether to include language statistics. Defaults to False.

        Returns:
            list: List of repository metadata dictionaries.
        """
        username = username or self.username
        if not username:
            raise ValueError("Username is required")
        endpoint = f"users/{username}/repos"
        
        repos = self.api.make_request('GET', endpoint, params={'per_page': per_page})
        if not isinstance(repos, list):
            raise ValueError("Invalid response format for repositories metadata")
        if include_languages:
            for repo in repos:
                if isinstance(repo, dict) and 'name' in repo:
                    languages = self.api.make_request('GET', f"repos/{username}/{repo['name']}/languages")
                    if isinstance(languages, dict):
                        repo['languages'] = languages
        return repos

    @cache_manager.cache_decorator(cache_key_func=lambda username, repo, path, **kwargs: f"file_content:{username}:{repo}:{path}", ttl=3600)
    def get_file_content(self, username: Optional[str], repo: str, path: str) -> Optional[str]:
        """
        Fetch the content of a file from a repository using the underlying file_manager.
        Args:
            repo_name: Name of the repository
            path: Path to the file (e.g., 'README.md')
            username: GitHub username (optional, defaults to self.api.username)
        Returns:
            File content as a string, or None if not found.
        """
        if not username:
            raise ValueError("Username is required")
        endpoint = f"repos/{username}/{repo}/contents/{path}"
        file_data = self.api.make_request('GET', endpoint)
        if isinstance(file_data, dict) and file_data.get('type') == 'file':
            return self.api.decode_file_content(file_data)
        return None

    def get_repository_tree(self, repo_name: str, username: Optional[str] = None, recursive: bool = False) -> List[str]:
        """
        Recursively fetch all file paths in a repository.
        Returns a flat list of file paths (including nested files).
        """
        username = username or self.username
        if not username:
            raise ValueError("Username is required")

        def _fetch_tree(path: str = "") -> List[str]:
            files: List[str] = []
            try:
                path_segment = path if path else ""
                endpoint = f"repos/{username}/{repo_name}/contents/{path_segment}"
                contents = self.api.make_request('GET', endpoint)

                # If directory: contents is a list of items
                if isinstance(contents, list):
                    for item in contents:
                        if not isinstance(item, dict):
                            continue
                        item_type = item.get('type')
                        item_path = item.get('path')
                        if item_type == 'file' and item_path:
                            files.append(item_path)
                        elif item_type == 'dir' and item_path and recursive:
                            files.extend(_fetch_tree(item_path))
                # If single file: contents is an object
                elif isinstance(contents, dict) and contents.get('type') == 'file':
                    file_path = contents.get('path')
                    if file_path:
                        files.append(file_path)
            except Exception as e:
                logger.warning(f"Error fetching tree for {repo_name} at '{path}': {str(e)}")
            return files

        return _fetch_tree("")

    def get_all_file_types(self, repo_name: str, username: str = None) -> Dict[str, int]:
        """
        Recursively retrieve all file types/extensions in a repository.
        """
        username = username or self.username
        file_types = {}
        files = self.get_repository_tree(repo_name, username=username, recursive=True)
        for file_path in files:
            ext = os.path.splitext(file_path)[1].lower()
            if ext:
                file_types[ext] = file_types.get(ext, 0) + 1
        return file_types