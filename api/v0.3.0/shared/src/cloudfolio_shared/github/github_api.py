import logging
import os
from base64 import b64decode
from typing import Any, Dict, Optional

from .api_usage import ApiUsageTracker

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


logger = logging.getLogger(__name__)


class GitHubAPI:
    """Lightweight wrapper around the GitHub REST API."""

    DEFAULT_BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None, username: Optional[str] = None,
                 base_url: str = DEFAULT_BASE_URL) -> None:
        """Initialise the client and require an explicit GitHub username."""
        self.token = token or os.getenv('GITHUB_TOKEN')
        resolved_username = username or os.getenv('GITHUB_USERNAME')
        if not resolved_username:
            raise ValueError("GitHub username is required")
        self.username = resolved_username
        self.base_url = base_url.rstrip('/')
        self.headers = {'Authorization': f'token {self.token}'} if self.token else {}
        self.session = self._build_session()
        
        # Log token availability for debugging
        token_status = 'present' if self.token else 'MISSING'
        token_preview = f"{self.token[:12]}..." if self.token and len(self.token) > 12 else 'N/A'
        logger.info(
            "[GITHUB_API_INIT] username=%s token_status=%s token_preview=%s base_url=%s",
            self.username, token_status, token_preview, self.base_url
        ) # we dont need to log this. we just need to capture when api token is missing and inform frontend

    def _build_session(self) -> requests.Session:
        """Create a shared session with retries and connection pooling to reduce SNAT usage."""
        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("HEAD", "GET", "OPTIONS", "POST", "PUT", "DELETE", "PATCH"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

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
        usage: Optional[ApiUsageTracker] = None,
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

        # Log outgoing API call (before making request)
        has_auth = 'Authorization' in request_headers
        logger.info(
            "[GITHUB_API_CALL] %s %s auth=%s params=%s",
            method, endpoint, has_auth, params or {}
        )

        try:
            response = self.session.request(
                method=method,
                url=full_url,
                headers=request_headers,
                params=params,
                json=data,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            logger.warning("[GITHUB_API_ERROR] Request failed: %s %s (%s)", method, full_url, exc)
            return None

        # Log rate limit headers for every response
        rate_limit = response.headers.get("X-RateLimit-Limit", "unknown")
        rate_remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
        rate_reset = response.headers.get("X-RateLimit-Reset", "unknown")
        
        logger.info(
            "[GITHUB_API_RESPONSE] %s status=%d rate_limit=%s/%s reset=%s",
            endpoint, response.status_code, rate_remaining, rate_limit, rate_reset
        )

        rate_remaining = response.headers.get("X-RateLimit-Remaining")
        if usage:
            usage.record_request(
                method=method,
                endpoint=endpoint,
                endpoint_kind="rest",
                purpose=purpose,
                target_key=target_key,
                status_code=response.status_code,
                rate_remaining=int(rate_remaining) if isinstance(rate_remaining, str) and rate_remaining.isdigit() else None,
                cache_hit=False,
            )

        # Respect GitHub rate limits: if we're limited, log and return None so caller can decide.
        if response.status_code == 403 and rate_remaining == "0":
            reset = response.headers.get("X-RateLimit-Reset")
            logger.error(
                "[GITHUB_RATE_LIMIT] Rate limit exceeded for %s; remaining=0/%s reset=%s",
                full_url, rate_limit, reset
            )
            if usage:
                usage.mark_rate_limited()
            return None

        if response.status_code == 404:
            logger.info("GitHub resource not found: %s", full_url)
            return None

        if 200 <= response.status_code < 300:
            if accept_raw:
                return response.text
            try:
                return response.json()
            except ValueError:
                return response.text or None

        logger.warning(
            "GitHub API error (%s): %s",
            response.status_code,
            response.text[:200],
        )
        response.raise_for_status()
        return None

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

