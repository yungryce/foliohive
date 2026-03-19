import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ..ai.data_filter import (
    get_config_extractor,
    get_standard_config_file_candidates,
)
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

    def get_repo_metadata(
        self,
        username: Optional[str]=None,
        repo: Optional[str]=None,
        include_languages: bool=False,
        *,
        usage: Optional[ApiUsageTracker] = None,
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
        usage_tracker = usage or ApiUsageTracker(owner=username, repo=repo)
        repo_data = self.api.make_request('GET', endpoint, usage=usage_tracker, purpose="repo_metadata")
        if not isinstance(repo_data, dict):
            raise ValueError("Invalid response format for repository metadata")
        if include_languages:
            languages = self.api.make_request(
                'GET',
                f"{endpoint}/languages",
                usage=usage_tracker,
                purpose="repo_languages",
                target_key=repo,
            )
            if isinstance(languages, dict):
                repo_data['languages'] = languages
        repo_data["api_usage"] = usage_tracker.to_dict()
        return repo_data

    def get_all_repos_metadata(
        self,
        username: Optional[str]=None,
        per_page=100,
        include_languages: bool=False,
        *,
        usage: Optional[ApiUsageTracker] = None,
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
        
        usage_tracker = usage or ApiUsageTracker(owner=username, repo="all_repos")
        repos = self.api.make_request(
            'GET',
            endpoint,
            params={'per_page': per_page},
            usage=usage_tracker,
            purpose="repo_list",
        )
        if not isinstance(repos, list):
            raise ValueError("Invalid response format for repositories metadata")
        if include_languages:
            for repo in repos:
                if isinstance(repo, dict) and 'name' in repo:
                    languages = self.api.make_request(
                        'GET',
                        f"repos/{username}/{repo['name']}/languages",
                        usage=usage_tracker,
                        purpose="repo_languages",
                        target_key=repo.get("name"),
                    )
                    if isinstance(languages, dict):
                        repo['languages'] = languages
        if repos and isinstance(repos[0], dict):
            repos[0]["api_usage"] = usage_tracker.to_dict()
        return repos

    def get_user_profile(
        self,
        username: Optional[str] = None,
        *,
        usage: Optional[ApiUsageTracker] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a GitHub user profile (GET /users/{username})."""
        resolved_username = username or self.username
        if not resolved_username:
            raise ValueError(USERNAME_REQUIRED_ERROR)
        usage_tracker = usage or ApiUsageTracker(owner=resolved_username, repo="user_profile")
        profile = self.api.make_request("GET", f"users/{resolved_username}", usage=usage_tracker, purpose="user_profile")
        if isinstance(profile, dict):
            profile["api_usage"] = usage_tracker.to_dict()
        return profile

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
        
        # Cache miss - fetch from GitHub API
        endpoint = f"repos/{username}/{repo}/contents/{path}"
        file_data = self.api.make_request(
            'GET',
            endpoint,
            purpose="content_fetch",
            usage=usage,
            target_key=path,
        )
        
        content = None
        if isinstance(file_data, dict) and file_data.get('type') == 'file':
            content = self.api.decode_file_content(file_data)
        
        return content

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

        logger.info("Using ref '%s' for repo tree of %s/%s", ref, username, repo)

        endpoint = f"repos/{username}/{repo}/git/trees/{ref}"
        tree_data = self.api.make_request(
            'GET',
            endpoint,
            params={"recursive": 1},
            purpose="tree_index",
            usage=usage,
        )

        logger.info("*******************ertyu*********************")
        logger.info("[Tree Data] repo=%s/%s ref=%s data=%s", username, repo, ref or "default", tree_data)

        tree_items = tree_data.get("tree", [])
        if not isinstance(tree_data, dict) or tree_data is None or not isinstance(tree_items, list):
            return []

        logger.info(
            "[TREE_RESPONSE] repo=%s/%s ref=%s truncated=%s tree_count=%d",
            username, repo, ref or "default branch", tree_data.get("truncated", False),
            len(tree_items)
        )

        logger.info(
            "[TREE_LOOP_START] repo=%s/%s tree_items_type=%s tree_items_count=%d",
            username, repo, type(tree_items).__name__, len(tree_items) if isinstance(tree_items, list) else "N/A"
        )

        paths: Set[str] = set()
        for item in tree_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "blob":
                continue
            path = item.get("path")
            if path:
                paths.add(path)
        
        logger.info(
            "[TREE_LOOP_END] repo=%s/%s paths_collected=%d",
            username, repo, len(paths)
        )

        result = sorted(paths)
        logger.info(
            "[TREE_PARSED] repo=%s/%s ref=%s extracted_paths=%d",
            username, repo, ref or "default", len(result)
        )
        return result


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
        
        # Separate counters for file type capping
        config_files: List[str] = []
        readme_files: List[str] = []
        primary_readme_found = False
        
        # Caps: primary=1, other_readmes=3, config_files=5
        readme_cap = 4  # 1 primary + 3 others
        config_cap = 5

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
        mode: str = "rest",
        ref: Optional[str] = None,
        limit: int = 20,
        max_chars: int = 4000,
        readme_max_chars: int = 4096,
    ) -> Dict[str, Any]:
        """Discover config files + README files level-by-level with minimal API calls."""
        username = username or self.username
        if not username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        usage = ApiUsageTracker(owner=username, repo=repo)
        logger.info("[Starting blob fetch] repo=%s/%s mode=%s ref=%s", username, repo, mode, ref or "default")
        
        file_candidates = get_standard_config_file_candidates(limit=limit)
        readme_candidates = ["README.md", "README.rst", "README.txt", "readme.md"]
        readme_set = {name.lower() for name in readme_candidates}

        logger.info("***************************feik*****************************")
        logger.info("[Candidates] repo=%s, readme=%s, files=%s", repo, readme_set, file_candidates)

        path_index = self.get_repo_path_index(username=username, repo=repo, ref=ref, usage=usage)
        path_index_set = set(path_index) if path_index else set()
        
        if not path_index_set:
            logger.warning(
                "[EMPTY_PATH_INDEX] repo=%s/%s - path_index is empty (check logs for [TREE_REQUEST_FAILED] or [TREE_REQUEST_INVALID] to determine if API call failed)",
                username, repo
            )
        else:
            logger.info("[PATH_INDEX_SUCCESS] repo=%s/%s paths=%d", username, repo, len(path_index_set))
        
        logger.info("[Path Index] contains %d paths for %s/%s", len(path_index_set), username, repo)

        target_paths = self._discover_file_target_paths_by_level(
            path_index=path_index_set,
            file_candidates=file_candidates,
            readme_candidates=readme_candidates,
            limit=limit,
        )
        if not target_paths:
            logger.warning(
                "[NO_PATHS_DISCOVERED] repo=%s/%s path_index_size=%d - no config/readme files found after discovery",
                username, repo, len(path_index_set)
            )
            return {
                "config_files": {},
                "readme_files": {},
                "primary_readme": "",
                "api_usage": usage.to_dict(),
                "paths_discovered": 0,
            }
        logger.info("[Discovered Paths] %s", target_paths)

        config_files: Dict[str, str] = {}
        readme_files: Dict[str, str] = {}

        if mode not in {"rest", "graphql"}:
            logger.warning("Unknown config discovery mode '%s'; defaulting to rest", mode)
            mode = "rest"

        if mode == "graphql":
            logger.info("Using GraphQL API for blob fetch in %s/%s for %d paths", username, repo, len(target_paths))
            graphql_client = GitHubGraphQLAPI(token=self.api.token, session=self.api.session)
            logger.info("[GraphQL Fetch--] Fetching %d files for %s/%s using GraphQL", len(target_paths), username, repo)
            content_map = self._fetch_file_targets_graphql(
                graphql_client,
                owner=username,
                repo=repo,
                paths=target_paths,
                usage=usage,
            )
        else:
            logger.info("Using REST API for blob fetch in %s/%s for %d paths", username, repo, len(target_paths))
            content_map = {}
            rest_errors = []
            for path in target_paths:
                try:
                    content = self.get_file_content(username=username, repo=repo, path=path, usage=usage)
                    content_map[path] = content
                    if content is None:
                        rest_errors.append({"path": path, "reason": "fetch_failed"})
                except Exception as exc:
                    logger.warning("[REST_FETCH_ERROR] repo=%s path=%s error=%s", repo, path, exc)
                    content_map[path] = None
                    rest_errors.append({"path": path, "reason": f"exception: {exc}"})
            
            if rest_errors:
                logger.warning(
                    "[REST_FETCH_ERRORS] repo=%s/%s failed=%d/%d sample=%s",
                    username, repo, len(rest_errors), len(target_paths), rest_errors[:3]
                )

        # Validate content_map has entries for all target_paths
        if len(content_map) != len(target_paths):
            missing = [p for p in target_paths if p not in content_map]
            logger.error(
                "[CONTENT_MAP_INCOMPLETE] repo=%s/%s mode=%s requested=%d returned=%d missing=%d sample_missing=%s",
                username, repo, mode, len(target_paths), len(content_map), len(missing), missing[:5]
            )
            # Add missing paths to content_map as None
            for path in missing:
                content_map[path] = None

        # Track fetch failures for detailed logging
        fetch_failures: Dict[str, str] = {}
        
        for path in target_paths:
            content = content_map.get(path)
            if not content:
                # Categorize failure reason
                if content is None:
                    fetch_failures[path] = "fetch_failed_or_not_found"
                elif content == "":
                    fetch_failures[path] = "empty_content"
                else:
                    fetch_failures[path] = "unknown"
                continue
            usage.mark_file_target_found(path, selected=True, bytes_returned=len(content))
            if os.path.basename(path).lower() in readme_set:
                readme_files[path] = content[:readme_max_chars]
            else:
                config_files[path] = content[:max_chars]

        if fetch_failures:
            # Log first 5 failures with details
            sample_failures = list(fetch_failures.items())[:5]
            logger.warning(
                "[FILE_FETCH_FAILURES] repo=%s/%s mode=%s failed=%d/%d sample=%s",
                username, repo, mode, len(fetch_failures), len(target_paths), sample_failures
            )

        primary_readme = ""
        if readme_files:
            primary_readme = readme_files.get("README.md") or next(iter(readme_files.values()))

        logger.info(
            "Discovery complete for %s/%s mode=%s candidates=%d found=%d readme=%d config=%d primary_readme=%s",
            username,
            repo,
            mode,
            len(target_paths),
            len(config_files) + len(readme_files),
            len(readme_files),
            len(config_files),
            bool(primary_readme),
        )

        return {
            "config_files": config_files,
            "readme_files": readme_files,
            "primary_readme": primary_readme,
            "api_usage": usage.to_dict(),
            "paths_discovered": len(target_paths),
        }

    def persist_discovered_paths(
        self,
        *,
        table_manager_obj: Any,
        username: Optional[str],
        repo: str,
        fingerprint: str,
        config_files: Dict[str, str],
        readme_files: Dict[str, str],
        extraction_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        """Persist discovered readme/config paths for a repository.

        This helper centralizes discovered-path persistence for cache workflows.
        """
        resolved_username = username or self.username
        if not resolved_username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        from ..table.table_manager import RepoDiscoveredPathsRow

        readme_paths = list(readme_files.keys())
        config_paths = list(config_files.keys())
        discovered_paths = readme_paths + config_paths

        row = RepoDiscoveredPathsRow(
            username=resolved_username,
            repo_name=repo,
            fingerprint=fingerprint,
            discovered_paths=discovered_paths,
            readme_paths=readme_paths,
            config_paths=config_paths,
            extraction_metadata=extraction_metadata or {},
        )
        table_manager_obj.upsert_repo_discovered_paths(row)

    def extract_config_payloads(
        self,
        config_files: Dict[str, str],
    ) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        """Extract structured payloads from raw config file contents."""
        extracted_config_files: Dict[str, Any] = {}

        for filename, content in config_files.items():
            extractor = get_config_extractor(filename)
            if extractor is None:
                logger.warning("No extractor found for file %s; skipping", filename)
                continue

            extractor_key = getattr(extractor, "__name__", None) if extractor else None
            extracted = extractor(content or "")
            if extracted is None:
                logger.warning("Extractor for %s returned None for file %s; skipping", extractor_key, filename)
                continue
            logger.info("Extracted config file %s with extractor %s: keys=%s", filename, extractor_key, list(extracted.keys()) if isinstance(extracted, dict) else type(extracted))
            logger.info("Extracted content for %s: %s", filename, str(extracted)[:500])  # Log a snippet of the extracted content

            extracted_config_files[filename] = extracted
            status = "failed" if isinstance(extracted, dict) and extracted.get("error") else "extracted"
            if status == "failed":
                logger.warning("Extraction failed for file %s with extractor %s: %s", filename, extractor_key, extracted.get("error"))

        return extracted_config_files

    def cache_extracted_config_files(
        self,
        *,
        cache_manager_obj: Any,
        username: Optional[str],
        repo: str,
        extracted_config_files: Dict[str, Any],
    ) -> int:
        """Persist extracted config artifacts using the cache manager."""
        resolved_username = username or self.username
        if not resolved_username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        cached_count = 0
        for filename, payload in extracted_config_files.items():
            key = cache_manager_obj.generate_cache_key(
                username=resolved_username,
                repo=repo,
                file_type="config",
                filename=filename,
            )
            cache_manager_obj.save(key, payload)
            cached_count += 1
        return cached_count

    def persist_extraction_statuses(
        self,
        *,
        table_manager_obj: Any,
        username: Optional[str],
        repo: str,
        extraction_metadata: Dict[str, Dict[str, Any]],
    ) -> None:
        """Persist per-file extraction statuses to discovered-path metadata."""
        resolved_username = username or self.username
        if not resolved_username:
            raise ValueError(USERNAME_REQUIRED_ERROR)

        for file_path, metadata in extraction_metadata.items():
            status = metadata.get("extraction_status")
            if not status:
                continue
            table_manager_obj.update_repo_discovered_path_extraction_status(
                resolved_username,
                repo,
                file_path,
                extraction_status=str(status),
                extractor_key=metadata.get("extractor_key"),
                error=metadata.get("error"),
            )

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

    @staticmethod
    def categorize_fetch_failure(content: Optional[str], context: Optional[Dict[str, Any]] = None) -> str:
        """Categorize why a file fetch might have failed.
        
        Args:
            content: The content returned (None, empty string, or actual content)
            context: Optional context dict with keys like 'status_code', 'rate_limited', etc.
        
        Returns:
            Category string: "not_found" | "rate_limited" | "api_error" | "empty" | "success" | "unknown"
        """
        if context:
            if context.get("rate_limited"):
                return "rate_limited"
            status_code = context.get("status_code")
            if status_code == 404:
                return "not_found"
            if status_code and status_code >= 400:
                return "api_error"
        
        if content is None:
            return "not_found"
        if content == "":
            return "empty"
        if isinstance(content, str) and len(content) > 0:
            return "success"
        
        return "unknown"

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
        chunks = list(self._chunk_paths(paths, chunk_size))
        total_chunks = len(chunks)
        
        logger.info("[GraphQL Fetch] Starting fetch for %d paths in %d chunks for %s/%s", len(paths), total_chunks, owner, repo)
        for chunk_idx, chunk in enumerate(chunks):
            if usage.rate_limited:
                remaining_chunks = total_chunks - chunk_idx
                remaining_paths = sum(len(c) for c in chunks[chunk_idx:])
                logger.warning(
                    "[GRAPHQL_RATE_LIMITED] repo=%s/%s chunk=%d/%d remaining_chunks=%d remaining_paths=%d",
                    owner, repo, chunk_idx + 1, total_chunks, remaining_chunks, remaining_paths
                )
                # Fill remaining paths with None to ensure complete result set
                for remaining_chunk in chunks[chunk_idx:]:
                    for path in remaining_chunk:
                        results[path] = None
                break
            
            logger.info(
                "[GRAPHQL_FETCH_CHUNK] repo=%s/%s chunk=%d/%d paths_in_chunk=%d",
                owner, repo, chunk_idx + 1, total_chunks, len(chunk)
            )

            fetched = graphql_client.fetch_blobs_gql(
                owner=owner,
                repo=repo,
                paths=list(chunk),
                usage=usage,
            )

            logger.info(
                "[GRAPHQL_FETCH_RESULT] repo=%s/%s chunk=%d/%d fetched=%d",
                owner, repo, chunk_idx + 1, total_chunks, len(fetched)
            )

            results.update(fetched)
        
        # Validate that all input paths have results
        missing_paths = [p for p in paths if p not in results]
        if missing_paths:
            logger.error(
                "[GRAPHQL_MISSING_PATHS] repo=%s/%s requested=%d returned=%d missing=%d sample_missing=%s",
                owner, repo, len(paths), len(results), len(missing_paths), missing_paths[:5]
            )
            # Add missing paths as None to ensure complete result set
            for path in missing_paths:
                results[path] = None
        
        success_count = sum(1 for v in results.values() if v is not None)
        logger.info(
            "GraphQL fetch for %s/%s paths=%d fetched=%d successful=%d failed=%d",
            owner, repo, len(paths), len(results), success_count, len(results) - success_count
        )
        return results

