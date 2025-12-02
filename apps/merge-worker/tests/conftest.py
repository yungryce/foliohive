"""Pytest fixtures for merge-worker tests."""
import sys
from pathlib import Path

# Add merge-worker directory to path FIRST so 'import function_app' finds the right one
merge_worker_dir = Path(__file__).parent.parent
sys.path.insert(0, str(merge_worker_dir))

# Force reload function_app from THIS directory if already imported
if "function_app" in sys.modules:
    del sys.modules["function_app"]

# Pre-import the correct module so tests get it
import function_app  # noqa: E402, F401
