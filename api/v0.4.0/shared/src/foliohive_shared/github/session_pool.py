"""Centralized session management for GitHub API clients.

Provides consistently-configured requests.Session instances for REST and GraphQL operations
with shared retry configurations and connection pooling to reduce SNAT usage.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


class SessionPool:
    """Factory and pool for consistently-configured HTTP sessions.
    
    Centralizes session creation with unified retry and connection pooling
    configuration to ensure all GitHub API clients (REST and GraphQL) use
    the same transport behavior.
    """

    def __init__(
        self,
        *,
        pool_connections: int = 20,
        pool_maxsize: int = 20,
        retry_total: int = 0,
        retry_backoff_factor: float = 0.5,
    ) -> None:
        """Initialize SessionPool with configuration.
        
        Args:
            pool_connections: Number of urllib3 connection pools to maintain
            pool_maxsize: Maximum number of entries per connection pool
            retry_total: Total number of retries (0 = no retries)
            retry_backoff_factor: Backoff factor for retries
        """
        self.pool_connections = pool_connections
        self.pool_maxsize = pool_maxsize
        self.retry_total = retry_total
        self.retry_backoff_factor = retry_backoff_factor
        self._adapter: Optional[HTTPAdapter] = None

    def _build_adapter(self) -> HTTPAdapter:
        """Build HTTP adapter with retry and pooling configuration."""
        retry = Retry(
            total=self.retry_total,
            backoff_factor=self.retry_backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("HEAD", "GET", "OPTIONS", "POST", "PUT", "DELETE", "PATCH"),
            raise_on_status=True,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=self.pool_connections,
            pool_maxsize=self.pool_maxsize,
        )
        return adapter

    def get_session(self) -> requests.Session:
        """Get a new or reused session with standard GitHub API configuration.
        
        Returns:
            Configured requests.Session instance with retry and pooling settings.
        """
        if self._adapter is None:
            self._adapter = self._build_adapter()

        session = requests.Session()
        session.mount("https://", self._adapter)
        session.mount("http://", self._adapter)
        return session


# Global singleton instance for convenience (can be replaced at runtime)
_global_pool: Optional[SessionPool] = None


def get_default_session_pool() -> SessionPool:
    """Get or create the global session pool.
    
    Returns:
        Global SessionPool instance with default configuration.
    """
    global _global_pool
    if _global_pool is None:
        _global_pool = SessionPool()
    return _global_pool


def get_session_from_pool() -> requests.Session:
    """Get a session from the default global pool.
    
    Convenience function for quick access to a configured session.
    
    Returns:
        Configured requests.Session instance.
    """
    return get_default_session_pool().get_session()


def set_session_pool(pool: SessionPool) -> None:
    """Override the global session pool.
    
    Useful for testing or custom configuration.
    
    Args:
        pool: SessionPool instance to use globally.
    """
    global _global_pool
    _global_pool = pool
