import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ..ai.data_filter import get_standard_config_file_candidates
from ..cache.cache_manager import cache_manager
from .api_usage import ApiUsageTracker
from .github_api import GitHubAPI
from .github_graphql_api import GitHubGraphQLAPI


logger = logging.getLogger(__name__)

USERNAME_REQUIRED_ERROR = "Username is required"

_NON_BUNDLE_CACHE_PREFIXES = (
    "repo_metadata:",
    "repos_metadata:",
    "file_content:",
    "repo_path_index:",
)


def get_non_bundle_cache_prefixes() -> List[str]:
    """Return cache key prefixes for non-bundled blob cleanup."""
    return list(_NON_BUNDLE_CACHE_PREFIXES)


def _record_file_content_cache_hit(bound: Dict[str, Any]) -> None:
    usage = bound.get("usage")
    if not isinstance(usage, ApiUsageTracker):
        return
    username = bound.get("username") or "unknown"
    repo = bound.get("repo") or "unknown"
    path = bound.get("path") or ""
    endpoint = f"repos/{username}/{repo}/contents/{path}"
    usage.record_request(
        method="GET",
        endpoint=endpoint,
        endpoint_kind="rest",
        purpose="content_fetch",
        target_key=path or None,
        status_code=None,
        rate_remaining=None,
        cache_hit=True,
    )


def _record_repo_tree_cache_hit(bound: Dict[str, Any]) -> None:
    usage = bound.get("usage")
    if not isinstance(usage, ApiUsageTracker):
        return
    username = bound.get("username") or "unknown"
    repo = bound.get("repo") or "unknown"
    ref = bound.get("ref") or "default"
    endpoint = f"repos/{username}/{repo}/git/trees/{ref}"
    usage.record_request(
        method="GET",
        endpoint=endpoint,
        endpoint_kind="rest",
        purpose="tree_index",
        target_key=None,
        status_code=None,
        rate_remaining=None,
        cache_hit=True,
    )

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

    def get_user_profile(self, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch a GitHub user profile (GET /users/{username})."""
        resolved_username = username or self.username
        if not resolved_username:
            raise ValueError(USERNAME_REQUIRED_ERROR)
        return self.api.get_user_profile(resolved_username)

    @cache_manager.cache_decorator(
        cache_key_func=lambda username, repo, path, **kwargs: f"file_content:{username}:{repo}:{path}",
        ttl=3600,
        on_cache_hit=_record_file_content_cache_hit,
    )
    def get_file_content(
        self,
        username: Optional[str],
        repo: str,
        path: str,
        *,
        usage: Optional[ApiUsageTracker] = None,
    ) -> Optional[str]:
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
        file_data = self.api.make_request(
            'GET',
            endpoint,
            purpose="content_fetch",
            usage=usage,
            target_key=path,
        )
        if isinstance(file_data, dict) and file_data.get('type') == 'file':
            return self.api.decode_file_content(file_data)
        return None

    @cache_manager.cache_decorator(
        cache_key_func=lambda username, repo, ref=None, **kwargs: f"repo_path_index:{username}:{repo}:{ref or 'default'}",
        ttl=900,
        on_cache_hit=_record_repo_tree_cache_hit,
    )
    def get_repo_path_index(
        self,
        username: Optional[str],
        repo: str,
        *,
        ref: Optional[str] = None,
        usage: Optional[ApiUsageTracker] = None,
    ) -> List[str]:
        """Return a stable list of file paths for the repo using the Git tree API."""
        username = username or self.username
        if not username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        resolved_ref = ref
        if not resolved_ref:
            try:
                repo_metadata = self.get_repo_metadata(username=username, repo=repo)
            except Exception as exc:
                logger.debug("Unable to resolve default branch for %s/%s: %s", username, repo, exc)
                repo_metadata = {}
            resolved_ref = repo_metadata.get("default_branch") or "HEAD"

        endpoint = f"repos/{username}/{repo}/git/trees/{resolved_ref}"
        tree_data = self.api.make_request(
            'GET',
            endpoint,
            params={"recursive": 1},
            purpose="tree_index",
            usage=usage,
        )
        if not isinstance(tree_data, dict):
            return []

        tree_items = tree_data.get("tree", [])
        if not isinstance(tree_items, list):
            return []

        paths: Set[str] = set()
        for item in tree_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "blob":
                continue
            path = item.get("path")
            if path:
                paths.add(path)
        return sorted(paths)

    def discover_repo_files(
        self,
        username: Optional[str],
        repo: str,
        *,
        mode: str = "rest",
        limit: int = 20,
        max_chars: int = 4000,
        readme_max_chars: int = 4096,
    ) -> Dict[str, Any]:
        """Discover config files + README files level-by-level with minimal API calls."""
        username = username or self.username
        if not username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        usage = ApiUsageTracker(owner=username, repo=repo)
        file_candidates = get_standard_config_file_candidates(limit=limit)
        readme_candidates = ["README.md", "README.rst", "README.txt", "readme.md"]
        readme_set = {name.lower() for name in readme_candidates}

        path_index = self.get_repo_path_index(username=username, repo=repo, usage=usage)
        path_index_set = set(path_index) if path_index else set()
        if not path_index_set:
            logger.debug("Empty path index for %s/%s; falling back to candidate probes.", username, repo)

        target_paths = self._discover_file_target_paths_by_level(
            path_index=path_index_set,
            file_candidates=file_candidates,
            readme_candidates=readme_candidates,
            limit=limit,
        )

        config_files: Dict[str, str] = {}
        readme_files: Dict[str, str] = {}

        if mode not in {"rest", "graphql"}:
            logger.warning("Unknown config discovery mode '%s'; defaulting to rest", mode)
            mode = "rest"

        if mode == "graphql":
            graphql_client = GitHubGraphQLAPI(token=self.api.token, session=self.api.session)
            content_map = self._fetch_file_targets_graphql(
                graphql_client,
                owner=username,
                repo=repo,
                paths=target_paths,
                usage=usage,
            )
        else:
            content_map = {}
            for path in target_paths:
                content = self.get_file_content(username=username, repo=repo, path=path, usage=usage)
                content_map[path] = content

        for path in target_paths:
            content = content_map.get(path)
            if not content:
                continue
            usage.mark_file_target_found(path, selected=True, bytes_returned=len(content))
            if os.path.basename(path).lower() in readme_set:
                readme_files[path] = content[:readme_max_chars]
            else:
                config_files[path] = content[:max_chars]

        primary_readme = ""
        if readme_files:
            primary_readme = readme_files.get("README.md") or next(iter(readme_files.values()))

        return {
            "config_files": config_files,
            "readme_files": readme_files,
            "primary_readme": primary_readme,
            "api_usage": usage.to_dict(),
        }

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

        api_call_estimate = 0

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

        path_index = self.get_repo_path_index(username=username, repo=repo)
        path_index_set = set(path_index) if path_index else set()
        api_call_estimate += 1
        readme_exists = bool(path_index_set and "README.md" in path_index_set)
        if not path_index_set:
            logger.debug("Empty path index for %s/%s; falling back to candidate probes.", username, repo)

        result: Dict[str, str] = {}
        for path in candidates:
            if path.lower() == "readme.md":
                if path_index_set and path not in path_index_set:
                    continue
                continue
            if path_index_set and path not in path_index_set:
                continue
            try:
                content = self.get_file_content(username=username, repo=repo, path=path)
                api_call_estimate += 1
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.debug("Skipping config fetch for %s/%s path=%s: %s", username, repo, path, exc)
                continue
            if not content:
                continue
            result[path] = content[:max_chars]

        logger.info(
            "Standard config fetch for %s/%s candidates=%d found=%d api_calls_estimated=%d readme_exists=%s",
            username,
            repo,
            len(candidates),
            len(result),
            api_call_estimate,
            readme_exists,
        )
        return result

    @staticmethod
    def _build_directory_levels(paths: Iterable[str]) -> Dict[int, Set[str]]:
        levels: Dict[int, Set[str]] = {0: {""}}
        for path in paths:
            dir_path = os.path.dirname(path)
            while True:
                depth = 0 if dir_path == "" else dir_path.count(os.sep) + 1
                levels.setdefault(depth, set()).add(dir_path)
                if not dir_path or os.sep not in dir_path:
                    break
                dir_path = os.path.dirname(dir_path)
        return levels

    @staticmethod
    def _order_directories(dirs: Set[str], preferred: Sequence[str]) -> List[str]:
        ordered: List[str] = []
        remaining = set(dirs)
        for item in preferred:
            if item in remaining:
                ordered.append(item)
                remaining.remove(item)
        ordered.extend(sorted(remaining))
        return ordered

    def _discover_file_target_paths_by_level(
        self,
        *,
        path_index: Set[str],
        file_candidates: Sequence[str],
        readme_candidates: Sequence[str],
        limit: int,
        max_dirs_per_depth: int = 25,
        max_total_dirs: int = 200,
    ) -> List[str]:
        if not path_index:
            return []

        fixed_targets = [c for c in file_candidates if "/" in c]
        base_targets = [c for c in file_candidates if "/" not in c]
        readme_set = {name.lower() for name in readme_candidates}
        base_targets = [c for c in base_targets if c.lower() not in readme_set]

        selected: List[str] = []
        seen: Set[str] = set()

        for fixed in fixed_targets:
            if fixed in path_index and fixed not in seen:
                selected.append(fixed)
                seen.add(fixed)
                if len(selected) >= limit:
                    return selected

        levels = self._build_directory_levels(path_index)
        preferred_top = [
            "",
            "src",
            "app",
            "api",
            "backend",
            "frontend",
            "services",
            "server",
            "client",
            "infra",
            ".github",
        ]

        total_dirs_seen = 0
        for depth in sorted(levels.keys()):
            dirs = levels[depth]
            if depth == 0:
                ordered = [""]
            elif depth == 1:
                ordered = self._order_directories(dirs, preferred_top)
            else:
                ordered = sorted(dirs)

            if max_dirs_per_depth:
                ordered = ordered[:max_dirs_per_depth]

            for dir_path in ordered:
                total_dirs_seen += 1
                if max_total_dirs and total_dirs_seen > max_total_dirs:
                    return selected

                for readme_name in readme_candidates:
                    target = f"{dir_path}/{readme_name}" if dir_path else readme_name
                    if target in path_index and target not in seen:
                        selected.append(target)
                        seen.add(target)
                        if len(selected) >= limit:
                            return selected

                for target_name in base_targets:
                    target = f"{dir_path}/{target_name}" if dir_path else target_name
                    if target in path_index and target not in seen:
                        selected.append(target)
                        seen.add(target)
                        if len(selected) >= limit:
                            return selected

        return selected

    @staticmethod
    def _chunk_paths(paths: Sequence[str], chunk_size: int) -> List[List[str]]:
        return [list(paths[i : i + chunk_size]) for i in range(0, len(paths), chunk_size)]

    def _fetch_file_targets_graphql(
        self,
        graphql_client: GitHubGraphQLAPI,
        *,
        owner: str,
        repo: str,
        paths: Sequence[str],
        usage: ApiUsageTracker,
        chunk_size: int = 50,
    ) -> Dict[str, Optional[str]]:
        results: Dict[str, Optional[str]] = {}
        for chunk in self._chunk_paths(paths, chunk_size):
            if usage.rate_limited:
                break
            fetched = graphql_client.fetch_blobs(
                owner=owner,
                repo=repo,
                paths=list(chunk),
                usage=usage,
            )
            results.update(fetched)
        return results

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