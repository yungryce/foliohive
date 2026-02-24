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

        logger.info("[GITHUB_GRAPHQL_REQUEST] purpose=%s query=%s variables=%s", purpose, query[:500], variables)

        try:
            logger.info("******************dudk*******************")
            response = self.session.post(self.GRAPHQL_URL, json=payload, headers=headers, timeout=30)
            logger.info("[GITHUB_GRAPHQL_HTTP] purpose=%s status=%d", purpose, response.status_code)
        except requests.RequestException as exc:
            logger.info("******************duedfdk*******************")
            logger.warning("[GITHUB_GRAPHQL_ERROR] Request failed: %s", exc)
            return None
        except Exception as exc:
            logger.info("******************duwefdk*******************")
            logger.error("[GITHUB_GRAPHQL_ERROR] Unexpected error: %s", exc)
            return None

        logger.info("*******************cnkn********************")
        logger.info("[GITHUB_GRAPHQL_RESPONSE] purpose=%s status=%d response_length=%d", purpose, response.status_code, len(response.content))

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
            logger.warning(
                "[GRAPHQL_FETCH_FAILED] repo=%s/%s paths=%d reason=invalid_payload",
                owner, repo, len(paths)
            )
            return {path: None for path in paths}

        # Check for GraphQL errors in response
        errors = payload.get("errors")
        if errors and isinstance(errors, list):
            error_messages = [e.get("message", str(e)) for e in errors[:3]]
            logger.warning(
                "[GRAPHQL_PARTIAL_ERRORS] repo=%s/%s paths=%d error_count=%d sample_errors=%s",
                owner, repo, len(paths), len(errors), error_messages
            )

        repo_data = payload.get("data", {}).get("repository")
        if not isinstance(repo_data, dict):
            logger.warning(
                "[GRAPHQL_FETCH_FAILED] repo=%s/%s paths=%d reason=invalid_repository_data",
                owner, repo, len(paths)
            )
            return {path: None for path in paths}

        results: Dict[str, Optional[str]] = {}
        none_count = 0
        for alias, path in alias_map.items():
            node = repo_data.get(alias)
            if not isinstance(node, dict) or node.get("isBinary"):
                results[path] = None
                none_count += 1
                if not isinstance(node, dict):
                    logger.info("[GRAPHQL_BLOB_NULL] repo=%s/%s path=%s reason=null_node", owner, repo, path)
                elif node.get("isBinary"):
                    logger.info("[GRAPHQL_BLOB_BINARY] repo=%s/%s path=%s", owner, repo, path)
                continue
            text = node.get("text")
            results[path] = text if isinstance(text, str) else None
            if text is None:
                none_count += 1
                logger.info("[GRAPHQL_BLOB_NULL] repo=%s/%s path=%s reason=null_text", owner, repo, path)
        
        if none_count > 0:
            logger.info(
                "[GRAPHQL_FETCH_SUMMARY] repo=%s/%s requested=%d fetched=%d failed=%d",
                owner, repo, len(paths), len(paths) - none_count, none_count
            )
        
        return results