import logging
import os
from typing import Any, Dict, List, Optional, Set

from ..ai.data_filter import get_standard_config_file_candidates
from ..cache.cache_manager import cache_manager
from .github_api import GitHubAPI


logger = logging.getLogger(__name__)

USERNAME_REQUIRED_ERROR = "Username is required"

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
            raise ValueError(USERNAME_REQUIRED_ERROR)
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
            raise ValueError(USERNAME_REQUIRED_ERROR)
        endpoint = f"repos/{username}/{repo}/contents/{path}"
        file_data = self.api.make_request('GET', endpoint)
        if isinstance(file_data, dict) and file_data.get('type') == 'file':
            return self.api.decode_file_content(file_data)
        return None

    def get_standard_config_files(
        self,
        username: Optional[str],
        repo: str,
        *,
        limit: int = 20,
        max_chars: int = 4000,
    ) -> Dict[str, str]:
        """Return a bounded set of standard config/build files for the repo."""
        username = username or self.username
        if not username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        try:
            candidates = get_standard_config_file_candidates(limit=limit)
        except Exception as exc:
            logger.warning(
                "Failed to build config file candidate list for %s/%s: %s",
                username,
                repo,
                exc,
            )
            return {}

        result: Dict[str, str] = {}
        for path in candidates:
            if path.lower() == "readme.md":
                continue
            try:
                content = self.get_file_content(username=username, repo=repo, path=path)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.debug("Skipping config fetch for %s/%s path=%s: %s", username, repo, path, exc)
                continue
            if not content:
                continue
            result[path] = content[:max_chars]
        return result

    def get_repository_tree(self, repo_name: str, username: Optional[str] = None, recursive: bool = False) -> List[str]:
        """
        Recursively fetch all file paths in a repository.
        Returns a flat list of file paths (including nested files).
        """
        username = username or self.username
        if not username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        files: List[str] = []
        paths_to_visit: List[str] = [""]
        visited_dirs: Set[str] = set()

        while paths_to_visit:
            current_path = paths_to_visit.pop()
            contents = self._fetch_repo_contents(username, repo_name, current_path)
            self._handle_contents(contents, files, recursive, paths_to_visit, visited_dirs)

        return files

    def get_all_file_types(self, repo_name: str, username: str = None) -> Dict[str, int]:
        """
        Recursively retrieve all file types/extensions in a repository.

        This helper is primarily intended for analysis and scoring (for
        example, feeding into FileTypeAnalyzer). Core data-retrieval flows
        should prefer standard GitHub metadata (README, topics, languages)
        rather than walking the full repository tree.
        """
        username = username or self.username
        file_types = {}
        files = self.get_repository_tree(repo_name, username=username, recursive=True)
        for file_path in files:
            ext = os.path.splitext(file_path)[1].lower()
            if ext:
                file_types[ext] = file_types.get(ext, 0) + 1
        return file_types

    def _fetch_repo_contents(self, username: str, repo_name: str, path: str):
        base_endpoint = f"repos/{username}/{repo_name}/contents"
        endpoint = f"{base_endpoint}/{path}" if path else base_endpoint
        try:
            return self.api.make_request('GET', endpoint)
        except Exception as error:
            logger.warning("Error fetching tree for %s at '%s': %s", repo_name, path, error)
            return None

    def _handle_contents(
        self,
        contents: Any,
        files: List[str],
        recursive: bool,
        paths_to_visit: List[str],
        visited_dirs: Set[str],
    ) -> None:
        if contents is None:
            return
        if isinstance(contents, dict):
            self._record_file(contents, files)
            return
        if not isinstance(contents, list):
            return
        for item in contents:
            self._process_content_item(item, files, recursive, paths_to_visit, visited_dirs)

    @staticmethod
    def _record_file(item: Dict[str, Any], files: List[str]) -> None:
        if item.get('type') == 'file':
            file_path = item.get('path')
            if file_path:
                files.append(file_path)

    def _process_content_item(
        self,
        item: Any,
        files: List[str],
        recursive: bool,
        paths_to_visit: List[str],
        visited_dirs: Set[str],
    ) -> None:
        if not isinstance(item, dict):
            return
        item_type = item.get('type')
        if item_type == 'file':
            self._record_file(item, files)
            return
        if not recursive or item_type != 'dir':
            return
        item_path = item.get('path')
        if item_path and item_path not in visited_dirs:
            paths_to_visit.append(item_path)
            visited_dirs.add(item_path)