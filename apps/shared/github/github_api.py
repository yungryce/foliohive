import logging
import os
from base64 import b64decode
from typing import Any, Dict, Optional

import requests


logger = logging.getLogger('portfolio.api')


class GitHubAPI:
    """Lightweight wrapper around the GitHub REST API."""

    DEFAULT_BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None, username: Optional[str] = None,
                 base_url: str = DEFAULT_BASE_URL) -> None:
        """Initialise the client with optional token and username."""
        self.token = token or os.getenv('GITHUB_TOKEN')
        self.username = username
        self.base_url = base_url.rstrip('/')
        self.headers = {'Authorization': f'token {self.token}'} if self.token else {}

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
    ) -> Any:
        """Perform an HTTP request against the GitHub API."""
        full_url = f"{self.base_url}/{endpoint.lstrip('/')}"
        request_headers = self.headers.copy()
        if headers:
            request_headers.update(headers)
        if accept_raw:
            request_headers['Accept'] = 'application/vnd.github.v3.raw'

        response = requests.request(
            method=method,
            url=full_url,
            headers=request_headers,
            params=params,
            json=data,
            timeout=timeout,
        )

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

