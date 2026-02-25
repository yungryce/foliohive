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

    def make_request_gql(
        self,
        *,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        usage: Optional[ApiUsageTracker] = None,
        purpose: str = "graphql_batch",
    ) -> Optional[Dict[str, Any]]:
        headers = {"Authorization": f"bearer {self.token}"} if self.token else {}
        payload = {"query": query, "variables": variables or {}}

        logger.info("[GITHUB_GRAPHQL_REQUEST_START] purpose=%s variables_count=%d", purpose, len(variables) if variables else 0)

        response = None
        try:
            response = self.session.post(self.GRAPHQL_URL, json=payload, headers=headers, timeout=30)
            logger.info("[GITHUB_GRAPHQL_POST_COMPLETE] purpose=%s response_exists=%s", purpose, response is not None)
        except requests.Timeout as exc:
            logger.error("[GITHUB_GRAPHQL_TIMEOUT] purpose=%s timeout_error=%s", purpose, str(exc)[:100])
            return None
        except requests.ConnectionError as exc:
            logger.error("[GITHUB_GRAPHQL_CONNECTION_ERROR] purpose=%s connection_error=%s", purpose, str(exc)[:100])
            return None
        except requests.RequestException as exc:
            logger.error("[GITHUB_GRAPHQL_REQUEST_ERROR] purpose=%s request_error_type=%s error=%s", purpose, type(exc).__name__, str(exc)[:100])
            return None
        except Exception as exc:
            logger.error("[GITHUB_GRAPHQL_UNEXPECTED_ERROR] purpose=%s error_type=%s error=%s", purpose, type(exc).__name__, str(exc)[:100])
            return None

        # CRITICAL: Validate response exists before accessing properties
        if response is None:
            logger.error("[GITHUB_GRAPHQL_RESPONSE_IS_NONE] purpose=%s - POST call returned None without raising exception", purpose)
            return None

        # Try to access response properties separately
        try:
            status_code = response.status_code
            logger.info("[GITHUB_GRAPHQL_STATUS_ACQUIRED] purpose=%s status=%d", purpose, status_code)
        except Exception as exc:
            logger.error("[GITHUB_GRAPHQL_STATUS_ACCESS_FAILED] purpose=%s error_type=%s error=%s", purpose, type(exc).__name__, str(exc)[:100])
            return None

        try:
            rate_remaining = response.headers.get("X-RateLimit-Remaining")
            logger.info("[GITHUB_GRAPHQL_HEADERS_ACQUIRED] purpose=%s rate_remaining=%s", purpose, rate_remaining)
        except Exception as exc:
            logger.error("[GITHUB_GRAPHQL_HEADERS_ACCESS_FAILED] purpose=%s error=%s", purpose, str(exc)[:100])
            rate_remaining = None

        status = response.status_code
        if usage:
            try:
                usage.record_request(
                    method="POST",
                    endpoint="graphql",
                    endpoint_kind="graphql",
                    purpose=purpose,
                    status_code=status,
                    rate_remaining=int(rate_remaining) if isinstance(rate_remaining, str) and rate_remaining.isdigit() else None,
                    cache_hit=False,
                )
            except Exception as exc:
                logger.warning("[GITHUB_GRAPHQL_USAGE_RECORD_FAILED] purpose=%s error=%s", purpose, str(exc)[:100])

        if status == 403 and rate_remaining == "0":
            if usage:
                usage.mark_rate_limited()
            logger.error("[GITHUB_GRAPHQL_RATE_LIMIT_EXCEEDED] purpose=%s", purpose)
            return None

        if status < 200 or status >= 300:
            try:
                error_body = response.text[:200]
            except Exception as exc:
                logger.error("[GITHUB_GRAPHQL_ERROR_BODY_READ_FAILED] purpose=%s read_error=%s", purpose, str(exc)[:100])
                error_body = "<unable to read response body>"
            logger.warning("[GITHUB_GRAPHQL_HTTP_ERROR] purpose=%s status=%d error_body=%s", purpose, status, error_body)
            return None

        try:
            json_response = response.json()
            logger.info("[GITHUB_GRAPHQL_JSON_PARSED] purpose=%s response_type=%s", purpose, type(json_response).__name__)
            return json_response
        except ValueError as exc:
            logger.error("[GITHUB_GRAPHQL_JSON_PARSE_ERROR] purpose=%s parse_error=%s", purpose, str(exc)[:100])
            return None
        except Exception as exc:
            logger.error("[GITHUB_GRAPHQL_JSON_ACCESS_ERROR] purpose=%s error_type=%s error=%s", purpose, type(exc).__name__, str(exc)[:100])
            return None

    def fetch_blobs_gql(
        self,
        *,
        owner: str,
        repo: str,
        paths: List[str],
        ref: str = "HEAD",
        usage: Optional[ApiUsageTracker] = None,
    ) -> Dict[str, Optional[str]]:
        if not paths:
            logger.info("[GRAPHQL_FETCH_EMPTY] repo=%s/%s reason=no_paths", owner, repo)
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

        payload = self.make_request_gql(
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