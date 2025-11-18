import os
import requests
import logging
from base64 import b64decode
from fa_helpers import handle_github_error


logger = logging.getLogger('portfolio.api')

class GitHubAPI:
    def __init__(self, token=None, username=None):
        self.token = token or os.getenv('GITHUB_TOKEN')
        self.username = username or 'yungryce'
        self.headers = {'Authorization': f'token {self.token}'} if self.token else {}

    # @cache_manager.cache_decorator(cache_key_func=generate_request_cache_key, ttl=3600)
    def make_request(self, method, endpoint, headers=None, params=None, data=None, accept_raw=False, timeout=30):
        full_url = f"https://api.github.com/{endpoint.lstrip('/')}"
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
            timeout=timeout
        )
        if response.status_code == 200:
            if accept_raw:
                return response.text
            try:
                return response.json()
            except Exception:
                return response.text
        elif response.status_code == 404:
            logger.info(f"Resource not found: {full_url}")
            return None
        else:
            logger.warning(f"GitHub API error: {response.status_code} {response.text[:200]}")
            response.raise_for_status()

    def decode_file_content(self, file_data):
        content = file_data.get('content', '')
        if content:
            try:
                return b64decode(content).decode('utf-8')
            except Exception as e:
                logger.warning(f"Failed to decode content: {str(e)}")
                return None
        return None
