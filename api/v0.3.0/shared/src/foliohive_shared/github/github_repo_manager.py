import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ..ai.data_filter import (
    get_config_extractor,
    get_standard_config_file_candidates,
)
from .github_api import GitHubAPI


logger = logging.getLogger("foliohive.github_repo_manager")
logger.setLevel(logging.INFO)
logger.propagate = True

USERNAME_REQUIRED_ERROR = "Username is required"
FILE_FETCH_MODE = "graphql" if os.getenv("ENABLE_CONFIG_DISCOVERY_GRAPHQL", "false").lower() == "true" else "rest"
# Max character limits for file content to manage token budgets and ensure we get some content even for large files.
STANDARD_CONFIG_MAX_CHARS = 2048
READ_ME_EXCERPT_MAX_CHARS = 4096
# Static caps for file discovery (config_files=5, readme=1 primary + 3 others)
CONFIG_FILES_CAP = 5
README_FILES_CAP = 4

_NON_BUNDLE_CACHE_PREFIXES = (
    "repo_metadata:",
    "repos_metadata:",
    "file_content:",
    "repo_path_index:",
)


def get_non_bundle_cache_prefixes() -> List[str]:
    """Return cache key prefixes for non-bundled blob cleanup."""
    return list(_NON_BUNDLE_CACHE_PREFIXES)

class GitHubRepoManager:
    def __init__(self, api: GitHubAPI, username: Optional[str] = None):
        """Initialize the GitHubRepoManager with API, cache, and file manager."""
        self.api = api
        self.username = username

    def get_repo_metadata(
        self,
        username: Optional[str]=None,
        repo: Optional[str]=None,
        purpose: str="repo_metadata",
        job_id: Optional[str]=None,
        include_languages: bool=False,
    ) -> Dict[str, Any]:
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
        self.api.begin_tracking(repo=repo, purpose=purpose, job_id=job_id)
        repo_data = self.api.make_request('GET', endpoint, purpose=purpose)
        if not isinstance(repo_data, dict):
            raise ValueError("Invalid response format for repository metadata")
        if include_languages:
            languages = self.api.make_request(
                'GET',
                f"{endpoint}/languages",
                purpose=purpose,
                target_key=repo,
            )
            if isinstance(languages, dict):
                repo_data['languages'] = languages
        return repo_data

    def get_all_repos_metadata(
        self,
        username: Optional[str]=None,
        per_page=100,
        purpose: str="repo_list",
        job_id: Optional[str]=None,
        include_languages: bool=False,
    ) -> List[Dict[str, Any]]:
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

        self.api.begin_tracking(repo="all_repos", purpose=purpose, job_id=job_id)
        repos = self.api.make_request(
            'GET',
            endpoint,
            params={'per_page': per_page},
            purpose=purpose,
        )
        if not isinstance(repos, list):
            raise ValueError("Invalid response format for repositories metadata")
        if include_languages:
            for repo in repos:
                if isinstance(repo, dict) and 'name' in repo:
                    languages = self.api.make_request(
                        'GET',
                        f"repos/{username}/{repo['name']}/languages",
                        purpose=purpose,
                        target_key=repo.get("name"),
                    )
                    if isinstance(languages, dict):
                        repo['languages'] = languages
        return repos


    def get_user_profile(
        self,
        username: Optional[str] = None,
        purpose: str = "user_profile",
        job_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a GitHub user profile (GET /users/{username})."""
        resolved_username = username or self.username
        if not resolved_username:
            raise ValueError(USERNAME_REQUIRED_ERROR)
        self.api.begin_tracking(repo=resolved_username, purpose=purpose, job_id=job_id)
        profile = self.api.make_request("GET", f"users/{resolved_username}", purpose=purpose)
        return profile


    def get_repo_path_index(
        self,
        username: Optional[str],
        repo: str,
        *,
        ref: Optional[str] = None,
    ) -> List[str]:
        """Return a stable list of file paths for the repo using the Git tree API."""
        username = username or self.username
        if not username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        endpoint = f"repos/{username}/{repo}/git/trees/{ref}"
        tree_data = self.api.make_request(
            'GET',
            endpoint,
            params={"recursive": 1},
            purpose="tree_index",
        )

        tree_items = tree_data.get("tree", [])
        if not isinstance(tree_data, dict) or tree_data is None or not isinstance(tree_items, list):
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


    def _discover_file_target_paths_by_level(
        self,
        *,
        path_index: Set[str],
        file_candidates: Sequence[str],
        readme_candidates: Sequence[str],
        config_cap: int,
        readme_cap: int,
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
        
        # Separate counters for file type capping
        config_files: List[str] = []
        readme_files: List[str] = []
        primary_readme_found = False

        for fixed in fixed_targets:
            if fixed in path_index and fixed not in seen:
                config_files.append(fixed)
                seen.add(fixed)
                if len(config_files) >= config_cap:
                    break

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
                    break

                # SEARCH CONFIG FILES FIRST (up to 5)
                if len(config_files) < config_cap:
                    for target_name in base_targets:
                        if len(config_files) >= config_cap:
                            break
                        target = f"{dir_path}/{target_name}" if dir_path else target_name
                        if target in path_index and target not in seen:
                            config_files.append(target)
                            seen.add(target)

                # THEN SEARCH README FILES (up to 4: 1 primary + 3 others)
                if len(readme_files) < readme_cap:
                    for readme_name in readme_candidates:
                        if len(readme_files) >= readme_cap:
                            break
                        target = f"{dir_path}/{readme_name}" if dir_path else readme_name
                        if target in path_index and target not in seen:
                            # Prioritize primary README.md at root
                            if target == "README.md" and not primary_readme_found:
                                readme_files.insert(0, target)  # Add at beginning
                                primary_readme_found = True
                            else:
                                readme_files.append(target)
                            seen.add(target)

            # Early exit if both quotas are filled
            if len(config_files) >= config_cap and len(readme_files) >= readme_cap:
                break

        # Combine: config files first, then README files
        selected = config_files + readme_files
        logger.info(
            "[File Capping] config=%d/%d, readme=%d/%d (primary=%s), total=%d",
            len(config_files), config_cap,
            len(readme_files), readme_cap,
            primary_readme_found,
            len(selected),
        )

        return selected


    def get_repo_blob_files(
        self,
        username: Optional[str],
        repo: str,
        *,
        ref: Optional[str] = None,
        purpose: str = "file_cache",
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Discover config files + README files level-by-level with minimal API calls."""
        
        username = username or self.username
        if not username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        file_candidates = get_standard_config_file_candidates()
        readme_candidates = ["README.md", "README.rst", "README.txt", "readme.md"]
        readme_set = {name.lower() for name in readme_candidates}

        self.api.begin_tracking(repo=repo, purpose=purpose, job_id=job_id)
        path_index = self.get_repo_path_index(username=username, repo=repo, ref=ref)
        path_index_set = set(path_index) if path_index else set()
        
        
        target_paths = self._discover_file_target_paths_by_level(
            path_index=path_index_set,
            file_candidates=file_candidates,
            readme_candidates=readme_candidates,
            config_cap=CONFIG_FILES_CAP,
            readme_cap=README_FILES_CAP,
        )
        if not target_paths:
            return {
                "config_files": {},
                "readme_files": {},
                "primary_readme": "",
                "paths_discovered": 0,
            }

        config_files: Dict[str, str] = {}
        readme_files: Dict[str, str] = {}

        if FILE_FETCH_MODE == "graphql":
            chunks = list(self._chunk_paths(target_paths, chunk_size=50))
            content_map = {}
            
            for chunk_idx, chunk in enumerate(chunks):
                if self.api.tracker.rate_limited:
                    for remaining_chunk in chunks[chunk_idx:]:
                        for path in remaining_chunk:
                            content_map[path] = None
                    break
                
                chunk_results = self.fetch_blobs_gql(
                    owner=username,
                    repo=repo,
                    paths=list(chunk),
                    ref=ref or "HEAD",
                )
                content_map.update(chunk_results)
        else:
            content_map = {}
            for path in target_paths:
                endpoint = f"repos/{username}/{repo}/contents/{path}"
                file_data = self.api.make_request(
                    'GET',
                    endpoint,
                    purpose="content_request",
                    target_key=path,
                )
                content = None
                if isinstance(file_data, dict) and file_data.get('type') == 'file':
                    content = self.api.decode_file_content(file_data)
                content_map[path] = content

        for path in target_paths:
            content = content_map.get(path)
            if not content:
                continue
            if os.path.basename(path).lower() in readme_set:
                readme_files[path] = content[:READ_ME_EXCERPT_MAX_CHARS]
            else:
                config_files[path] = content[:STANDARD_CONFIG_MAX_CHARS]

        if "README.md" in readme_files:
            primary_readme = readme_files.pop("README.md")
        elif readme_files:
            first_key = next(iter(readme_files))
            primary_readme = readme_files.pop(first_key)

        logger.info(
            "Discovery complete for %s/%s mode=%s candidates=%d found=%d readme=%d config=%d primary_readme=%s missing=%d",
            username,
            repo,
            FILE_FETCH_MODE,
            len(target_paths),
            len(config_files) + len(readme_files),
            len(readme_files),
            len(config_files),
            bool(primary_readme),
            len(target_paths) - (len(config_files) + len(readme_files)),
        )

        return {
            "config_files": config_files,
            "readme_files": readme_files,
            "primary_readme": primary_readme,
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

        result: Dict[str, str] = {}
        for path in candidates:
            if path.lower() == "readme.md":
                if path_index_set and path not in path_index_set:
                    continue
                continue
            if path_index_set and path not in path_index_set:
                continue
            try:
                endpoint = f"repos/{username}/{repo}/contents/{path}"
                file_data = self.api.make_request(
                    'GET',
                    endpoint,
                    purpose="content_fetch",
                    target_key=path,
                )
                content = None
                if isinstance(file_data, dict) and file_data.get('type') == 'file':
                    content = self.api.decode_file_content(file_data)
                api_call_estimate += 1
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning("Skipping config fetch for %s/%s path=%s: %s", username, repo, path, exc)
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

    @staticmethod
    def _chunk_paths(paths: Sequence[str], chunk_size: int) -> List[List[str]]:
        return [list(paths[i : i + chunk_size]) for i in range(0, len(paths), chunk_size)]


    def fetch_blobs_gql(
        self,
        *,
        owner: str,
        repo: str,
        paths: List[str],
        ref: str = "HEAD",
    ) -> Dict[str, Optional[str]]:
        """Fetch blob contents using GraphQL batch API.
        
        Args:
            owner: GitHub username/org
            repo: Repository name
            paths: List of file paths to fetch
            ref: Git ref (branch/tag/commit), defaults to HEAD
            
        Returns:
            Dict mapping path → content (None if fetch failed)
        """
        if not paths:
            logger.warning("[GRAPHQL_FETCH_EMPTY] repo=%s/%s reason=no_paths", owner, repo)
            return {}

        alias_map: Dict[str, str] = {}
        selections: List[str] = []
        for index, path in enumerate(paths):
            alias = f"f{index}"
            alias_map[alias] = path
            escaped = path.replace("\\", "\\\\").replace('"', "\\\"")
            selections.append(
                f"{alias}: object(expression:\"{ref}:{escaped}\") {{ ... on Blob {{ text byteSize isBinary }} }}"
            )

        query = (
            "query($owner: String!, $name: String!) { "
            "repository(owner: $owner, name: $name) { "
            + " ".join(selections)
            + " } }"
        )

        payload = self.api.make_request_gql(
            query=query,
            variables={"owner": owner, "name": repo},
            purpose="graphql_blob_batch",
        )

        repo_data = payload.get("data", {}).get("repository")
        if not isinstance(repo_data, dict):
            return {path: None for path in paths}

        results: Dict[str, Optional[str]] = {}
        for alias, path in alias_map.items():
            node = repo_data.get(alias)
            if not isinstance(node, dict) or node.get("isBinary"):
                results[path] = None
                continue
            text = node.get("text")
            results[path] = text if isinstance(text, str) else None
        
        return results
    