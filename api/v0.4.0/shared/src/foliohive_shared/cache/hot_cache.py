"""In-process hot cache for short-lived acceleration."""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Optional, Tuple


class HotCache:
    """Thread-safe LRU cache with TTL."""

    def __init__(self, max_size: int = 1024, default_ttl: int = 600) -> None:
        self.max_size = max(1, int(max_size))
        self.default_ttl = max(1, int(default_ttl))
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        expires_at = time.time() + (int(ttl) if ttl is not None else self.default_ttl)
        with self._lock:
            self._entries[key] = (expires_at, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def create_default_hot_cache() -> HotCache:
    max_size = _env_int("CF_HOT_CACHE_MAX_SIZE", 1024)
    default_ttl = _env_int("CF_HOT_CACHE_TTL", 600)
    return HotCache(max_size=max_size, default_ttl=default_ttl)
