import logging
import os
from base64 import b64decode
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .api_usage import ApiUsageTracker
from .session_pool import SessionPool

import requests


logger = logging.getLogger("foliohive.github_api")
logger.setLevel(logging.INFO)
logger.propagate = True


def _resolve_default_table_manager() -> Optional[Any]:
    try:
        from foliohive_shared.table import get_table_manager

        return get_table_manager()
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Failed to resolve default table manager for GitHubAPI: %s", exc)
        return None


def _parse_rate_limit_reset(reset_value: Optional[str]) -> Optional[str]:
    if not reset_value or not isinstance(reset_value, str) or not reset_value.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(reset_value), timezone.utc).isoformat()
    except (OverflowError, ValueError):
        return None


class GitHubAPI:
    """Lightweight wrapper around the GitHub REST API."""

    DEFAULT_BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None, username: Optional[str] = None,
                 base_url: str = DEFAULT_BASE_URL, session_pool: Optional[SessionPool] = None,
                 table_manager: Optional[Any] = None) -> None:
        """Initialise the client and require an explicit GitHub username."""

        self.token = token or os.getenv('GITHUB_TOKEN')
        if not self.token:
            logger.warning("GitHub API token is missing; unauthenticated requests may be rate-limited")

        resolved_username = username or os.getenv('GITHUB_USERNAME')
        if not resolved_username:
            raise ValueError("GitHub username is required")
        self.username = resolved_username
        self.base_url = base_url.rstrip('/')
        self.headers = {'Authorization': f'token {self.token}'} if self.token else {}
        
        # Use provided session pool or create a new one
        self.session_pool = session_pool or SessionPool()
        self.session = self.session_pool.get_session()
        self.table_manager = table_manager if table_manager is not None else _resolve_default_table_manager()
        # Tracker owned by the API instance; reset per-operation via begin_tracking()
        self.tracker = ApiUsageTracker(
            owner=self.username,
            repo=self.username,
            table_manager=self.table_manager,
        )

    def begin_tracking(self, repo: str, *, purpose: str = "unknown", job_id: Optional[str] = None) -> ApiUsageTracker:
        """Reset the internal tracker for a new logical operation.

        Call this at the start of each top-level operation (per-repo or per-user)
        so the tracker captures only the work done in that operation.

        Returns the new tracker for convenience.
        """
        self.tracker = ApiUsageTracker(
            owner=self.username,
            repo=repo,
            purpose=purpose,
            job_id=job_id,
            table_manager=self.table_manager,
        )
        return self.tracker

    @contextmanager
    def track_operation(
        self,
        *,
        repo: str,
        purpose: str = "unknown",
        job_id: Optional[str] = None,
    ):
        tracker = self.begin_tracking(repo=repo, purpose=purpose, job_id=job_id)
        try:
            yield tracker
        finally:
            tracker.persist_operation_to_table()

    def make_request(
        self,
        method: str,
        endpoint: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        accept_raw: bool = False,
        timeout: int = 30,
        purpose: Optional[str] = None,
        target_key: Optional[str] = None,
    ) -> Any:
        """Perform an HTTP request against the GitHub API."""
        full_url = f"{self.base_url}/{endpoint.lstrip('/')}"
        request_headers = self.headers.copy()
        if headers:
            request_headers.update(headers)
        if accept_raw:
            request_headers['Accept'] = 'application/vnd.github.v3.raw'

        try:
            response = self.session.request(
                method=method,
                url=full_url,
                headers=request_headers,
                params=params,
                json=data,
                timeout=timeout,
            )
        except Exception as exc:
            error_type = (
                "timeout" if isinstance(exc, requests.Timeout)
                else "connection_error" if isinstance(exc, requests.ConnectionError)
                else "request_error"
            )
            logger.error("[GITHUB_API_REQUEST_FAILED %s] %s %s: %s", purpose or "unknown", method, full_url, exc)
            self.tracker.record_error(error_type, endpoint, message=str(exc)[:100])
            return None

        status_code = response.status_code
        rate_remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
        rate_reset = _parse_rate_limit_reset(response.headers.get("X-RateLimit-Reset"))

        rate_remaining_int = int(rate_remaining) if isinstance(rate_remaining, str) and rate_remaining.isdigit() else None
        self.tracker.record_request(
            method=method,
            endpoint=endpoint,
            endpoint_kind="rest",
            purpose=purpose or self.tracker.purpose,
            target_key=target_key,
            status_code=status_code,
            rate_remaining=rate_remaining_int,
            rate_reset=rate_reset,
            cache_hit=False,
        )

        if status_code == 403 and rate_remaining_int == 0:
            self.tracker.mark_rate_limited()
            self.tracker.record_error(
                "rate_limited",
                endpoint,
                status_code=status_code,
                message=getattr(response, "text", "")[:100],
            )
        elif status_code >= 400:
            self.tracker.record_error(
                "http_error",
                endpoint,
                status_code=status_code,
                message=getattr(response, "text", "")[:100],
            )

        if accept_raw:
            return response.text
        try:
            return response.json()
        except Exception as exc:
            logger.warning("[GITHUB_API_JSON_PARSE_ERROR %s] %s: %s", purpose or "unknown", endpoint, exc)
            return None
        

    def make_request_gql(
        self,
        *,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        purpose: str = "graphql_batch",
    ) -> Optional[Dict[str, Any]]:
        """Perform a GraphQL request against the GitHub API."""
        headers = {"Authorization": f"bearer {self.token}"} if self.token else {}
        payload = {"query": query, "variables": variables or {}}
        repo = variables.get("name") if variables else None
        GRAPHQL_URL = "https://api.github.com/graphql"
        logger.info("[GITHUB_GRAPHQL_POST] repo=%s purpose=%s", repo, purpose)

        try:
            response = self.session.post(GRAPHQL_URL, json=payload, headers=headers, timeout=30)
        except Exception as exc:
            error_type = (
                "timeout" if isinstance(exc, requests.Timeout)
                else "connection_error" if isinstance(exc, requests.ConnectionError)
                else "request_error"
            )
            logger.error("[GITHUB_GRAPHQL_FAILED %s] %s: %s", purpose, type(exc).__name__, exc)
            self.tracker.record_error(error_type, "graphql", message=str(exc)[:100])
            return None

        status = response.status_code
        rate_remaining = response.headers.get("X-RateLimit-Remaining")
        rate_reset = _parse_rate_limit_reset(response.headers.get("X-RateLimit-Reset"))
        logger.info("[GITHUB_GRAPHQL_RESPONSE %s] status=%d rate_remaining=%s", purpose, status, rate_remaining)

        self.tracker.record_request(
            method="POST",
            endpoint="graphql",
            endpoint_kind="graphql",
            purpose=purpose or self.tracker.purpose,
            status_code=status,
            rate_remaining=int(rate_remaining) if isinstance(rate_remaining, str) and rate_remaining.isdigit() else None,
            rate_reset=rate_reset,
            cache_hit=False,
        )

        if status == 403 and isinstance(rate_remaining, str) and rate_remaining.isdigit() and int(rate_remaining) == 0:
            self.tracker.mark_rate_limited()
            self.tracker.record_error(
                "rate_limited",
                "graphql",
                status_code=status,
                message=getattr(response, "text", "")[:100],
            )

        payload = response.json()
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            first_message = errors[0].get("message", "") if isinstance(errors[0], dict) else str(errors[0])
            self.tracker.record_error(
                "graphql_error",
                "graphql",
                status_code=status,
                message=first_message,
            )

        return payload


    def decode_file_content(self, file_data: Dict[str, Any]) -> Optional[str]:
        """Base64 decode a file payload returned by GitHub."""
        content = file_data.get('content', '')
        if not content:
            return None
        try:
            # GitHub inserts newlines every 60 chars; b64decode handles them but strip just in case
            normalised = content.replace('\n', '')
            return b64decode(normalised).decode('utf-8')
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to decode GitHub file content: %s", exc)
            return None