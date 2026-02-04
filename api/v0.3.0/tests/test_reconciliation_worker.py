from datetime import datetime, timedelta, timezone

import os
import sys

_FUNCTION_APP_PATH = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "function-app")
)
if os.path.isdir(_FUNCTION_APP_PATH) and _FUNCTION_APP_PATH not in sys.path:
    sys.path.insert(0, _FUNCTION_APP_PATH)

from blueprints.reconciliation_worker import (
    _compute_missing_repos,
    _parse_iso_ts,
    _should_requeue,
    _should_trigger_cache_jobs,
)


def test_compute_missing_repos_excludes_processed():
    expected = ["a", "b", "c"]
    synced = ["a"]
    failed = ["c"]
    missing = _compute_missing_repos(expected, synced, failed)
    assert missing == {"b"}


def test_should_trigger_cache_jobs_requires_all_processed_and_some_synced():
    expected = ["a", "b"]
    synced = ["a"]
    failed = ["b"]
    assert _should_trigger_cache_jobs(expected, synced, failed) is True

    expected = ["a", "b"]
    synced = []
    failed = ["a", "b"]
    assert _should_trigger_cache_jobs(expected, synced, failed) is False


def test_parse_iso_ts_handles_zulu_and_offset():
    value = "2025-01-01T00:00:00Z"
    parsed = _parse_iso_ts(value)
    assert parsed is not None
    assert parsed.tzinfo is not None

    value = "2025-01-01T00:00:00+00:00"
    parsed = _parse_iso_ts(value)
    assert parsed is not None


def test_should_requeue_respects_cooldown():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(seconds=30)).isoformat()
    old = (now - timedelta(seconds=600)).isoformat()

    assert _should_requeue(recent, cooldown_seconds=300) is False
    assert _should_requeue(old, cooldown_seconds=300) is True
