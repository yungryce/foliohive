from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from .api_usage import ApiUsageTracker


logger = logging.getLogger(__name__)


class GitHubGraphQLAPI:
    """Minimal GitHub GraphQL client for batch blob fetches."""

    GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(self, token: Optional[str], *, session: Optional[requests.Session] = None) -> None:
        self.token = token
        self.session = session or requests.Session()

    def make_request(
        self,
        *,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        usage: Optional[ApiUsageTracker] = None,
        purpose: str = "graphql_batch",
    ) -> Optional[Dict[str, Any]]:
        headers = {"Authorization": f"bearer {self.token}"} if self.token else {}
        payload = {"query": query, "variables": variables or {}}
        try:
            response = self.session.post(self.GRAPHQL_URL, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            logger.warning("[GITHUB_GRAPHQL_ERROR] Request failed: %s", exc)
            return None

        rate_remaining = response.headers.get("X-RateLimit-Remaining")
        status = response.status_code
        if usage:
            usage.record_request(
                method="POST",
                endpoint="graphql",
                endpoint_kind="graphql",
                purpose=purpose,
                status_code=status,
                rate_remaining=int(rate_remaining) if isinstance(rate_remaining, str) and rate_remaining.isdigit() else None,
                cache_hit=False,
            )

        if status == 403 and rate_remaining == "0":
            if usage:
                usage.mark_rate_limited()
            logger.error("[GITHUB_GRAPHQL_RATE_LIMIT] Rate limit exceeded")
            return None

        if status < 200 or status >= 300:
            logger.warning("[GITHUB_GRAPHQL_ERROR] status=%d body=%s", status, response.text[:200])
            return None

        try:
            return response.json()
        except ValueError:
            logger.warning("[GITHUB_GRAPHQL_ERROR] Invalid JSON response")
            return None

    def fetch_blobs(
        self,
        *,
        owner: str,
        repo: str,
        paths: List[str],
        ref: str = "HEAD",
        usage: Optional[ApiUsageTracker] = None,
    ) -> Dict[str, Optional[str]]:
        if not paths:
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

        payload = self.make_request(
            query=query,
            variables={"owner": owner, "name": repo},
            usage=usage,
            purpose="graphql_batch",
        )

        if not isinstance(payload, dict):
            return {path: None for path in paths}

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