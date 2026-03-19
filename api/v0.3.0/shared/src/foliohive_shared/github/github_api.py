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
        if not self.token:
            logger.warning("GitHub API token is missing; unauthenticated requests may be rate-limited")

        resolved_username = username or os.getenv('GITHUB_USERNAME')
        if not resolved_username:
            raise ValueError("GitHub username is required")
        self.username = resolved_username
        self.base_url = base_url.rstrip('/')
        self.headers = {'Authorization': f'token {self.token}'} if self.token else {}
        self.session = self._build_session()


    def _build_session(self) -> requests.Session:
        """Create a shared session with retries and connection pooling to reduce SNAT usage."""
        retry = Retry(
            total=0,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("HEAD", "GET", "OPTIONS", "POST", "PUT", "DELETE", "PATCH"),
            raise_on_status=True,
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
            "[GseL] %s %s auth=%s params=%s",
            method, endpoint, has_auth, params or {}
        )

        # STEP 1: Make the HTTP request with specific exception handling
        response = None
        try:
            response = self.session.request(
                method=method,
                url=full_url,
                headers=request_headers,
                params=params,
                json=data,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            logger.error("[GITHUB_API_TIMEOUT] %s %s (timeout=%s)", method, full_url, timeout)
            return None
        except requests.ConnectionError as exc:
            logger.error("[GITHUB_API_CONNECTION_ERROR] %s %s (error=%s)", method, full_url, exc)
            return None
        except requests.RequestException as exc:
            logger.error("[GITHUB_API_REQUEST_ERROR] %s %s (error=%s)", method, full_url, exc)
            return None
        
        # STEP 2: Validate response object exists
        if response is None:
            logger.error("[GITHUB_API_RESPONSE_IS_NONE] %s %s - response object is None", method, full_url)
            return None

        # STEP 3: Access status code separately
        status_code = None
        try:
            status_code = response.status_code
            logger.info("[GITHUB_API_STATUS_SUCCESS] %s status=%d", endpoint, status_code)
        except Exception as exc:
            logger.error("[GITHUB_API_STATUS_ACCESS_FAILED] %s - cannot access status_code: %s", endpoint, exc)
            return None

        # STEP 4: Access headers separately
        try:
            rate_limit = response.headers.get("X-RateLimit-Limit", "unknown")
            rate_remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
            rate_reset = response.headers.get("X-RateLimit-Reset", "unknown")
            logger.info(
                "[GITHUB_API_RESPONSE] %s status=%d rate_limit=%s/%s reset=%s",
                endpoint, status_code, rate_remaining, rate_limit, rate_reset
            )
        except Exception as exc:
            logger.error("[GITHUB_API_HEADERS_ACCESS_FAILED] %s - cannot access headers: %s", endpoint, exc)
            rate_limit = "unknown"
            rate_remaining = "unknown"
            rate_reset = "unknown"

        # STEP 5: Record usage if provided
        if usage:
            try:
                rate_remaining_int = None
                if isinstance(rate_remaining, str) and rate_remaining.isdigit():
                    rate_remaining_int = int(rate_remaining)
                usage.record_request(
                    method=method,
                    endpoint=endpoint,
                    endpoint_kind="rest",
                    purpose=purpose,
                    target_key=target_key,
                    status_code=status_code,
                    rate_remaining=rate_remaining_int,
                    cache_hit=False,
                )
            except Exception as exc:
                logger.warning("[GITHUB_API_USAGE_RECORDING_FAILED] %s: %s", endpoint, exc)

        # STEP 6: Respect GitHub rate limits
        try:
            if status_code == 403 and rate_remaining == "0":
                reset = response.headers.get("X-RateLimit-Reset")
                logger.error(
                    "[GITHUB_RATE_LIMIT] Rate limit exceeded for %s; remaining=0/%s reset=%s",
                    full_url, rate_limit, reset
                )
                if usage:
                    usage.mark_rate_limited()
                return None
        except Exception as exc:
            logger.error("[GITHUB_API_RATE_LIMIT_CHECK_FAILED] %s: %s", endpoint, exc)

        # STEP 7: Handle specific status codes
        if status_code == 404:
            logger.info("[GITHUB_API_NOT_FOUND] %s", full_url)
            return None

        # STEP 8: Handle successful responses (200-299)
        if 200 <= status_code < 300:
            if accept_raw:
                try:
                    return response.text
                except Exception as exc:
                    logger.error("[GITHUB_API_TEXT_ACCESS_FAILED] %s - cannot access response.text: %s", endpoint, exc)
                    return None
            
            # Try JSON parsing
            try:
                return response.json()
            except ValueError as exc:
                logger.warning("[GITHUB_API_JSON_PARSE_ERROR] %s - falling back to text: %s", endpoint, exc)
                try:
                    return response.text or None
                except Exception as exc2:
                    logger.error("[GITHUB_API_TEXT_FALLBACK_FAILED] %s - cannot access response.text: %s", endpoint, exc2)
                    return None
            except Exception as exc:
                logger.error("[GITHUB_API_JSON_ACCESS_FAILED] %s - cannot call response.json(): %s", endpoint, exc)
                return None

        # STEP 9: Handle error responses
        try:
            response_text = response.text[:200] if response.text else "(empty)"
        except Exception as exc:
            response_text = f"(inaccessible: {exc})"
        
        logger.warning(
            "[GITHUB_API_ERROR_STATUS] %s status=%d response=%s",
            endpoint,
            status_code,
            response_text,
        )
        
        try:
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[GITHUB_API_RAISE_FOR_STATUS] %s: %s", endpoint, exc)
        
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

