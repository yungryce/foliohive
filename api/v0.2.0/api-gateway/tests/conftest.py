"""Pytest fixtures for api-gateway tests."""
import json
import sys
from pathlib import Path

import pytest
from typing import Optional, Any, Dict

# Add api-gateway directory to path FIRST so 'import function_app' finds the right one
api_gateway_dir = Path(__file__).parent.parent
sys.path.insert(0, str(api_gateway_dir))


class FakeRequest:
    """Mock Azure Function HTTP request for testing."""

    def __init__(
        self,
        method: str = "GET",
        url: str = "http://localhost:7071/api/test",
        route_params: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        body: Any = b"",
    ):
        self.method = method
        self.url = url
        self.route_params = route_params or {}
        self.params = params or {}
        self.headers = headers or {}
        self._raw_body = body
        self._body = self._encode_body(body)

    @staticmethod
    def _encode_body(body: Any) -> bytes:
        if body in (None, b""):
            return b""
        if isinstance(body, bytes):
            return body
        if isinstance(body, bytearray):
            return bytes(body)
        if isinstance(body, str):
            return body.encode("utf-8")
        return json.dumps(body).encode("utf-8")

    def get_body(self) -> bytes:
        return self._body

    def get_json(self) -> Any:
        if self._body:
            try:
                return json.loads(self._body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        if isinstance(self._raw_body, dict):
            return self._raw_body
        return None

@pytest.fixture
def fake_request():
    """Factory fixture to create FakeRequest instances."""
    def _make_request(**kwargs):
        return FakeRequest(**kwargs)
    return _make_request
