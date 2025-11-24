import sys
from pathlib import Path

# Ensure the function app module is importable when running tests directly from this folder.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakeRequest:
    """Minimal stand-in for azure.functions.HttpRequest used in unit tests."""

    def __init__(self, route_params=None, params=None, body=None, headers=None):
        self.route_params = route_params or {}
        self.params = params or {}
        self._body = body
        self.headers = headers or {}

    def get_json(self):
        if self._body is None:
            raise ValueError("No JSON body provided")
        return self._body
