"""Pytest fixtures for api-gateway tests."""
import sys
from pathlib import Path

import pytest
from typing import Optional, Dict, Any

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
        body: bytes = b"",
    ):
        self.method = method
        self.url = url
        self.route_params = route_params or {}
        self.params = params or {}
        self.headers = headers or {}
        self._body = body
    
    def get_body(self) -> bytes:
        return self._body
    
    def get_json(self) -> Any:
        import json
        return json.loads(self._body) if self._body else None


@pytest.fixture
def fake_request():
    """Factory fixture to create FakeRequest instances."""
    def _make_request(**kwargs):
        return FakeRequest(**kwargs)
    return _make_request
