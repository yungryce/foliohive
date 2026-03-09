from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("foliohive.api_usage")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FileTargetUsage:
    attempts: int = 0
    cache_hits: int = 0
    found: bool = False
    bytes_returned: Optional[int] = None
    selected: bool = False


@dataclass
class ApiUsageTracker:
    owner: str
    repo: str
    ref: Optional[str] = None
    totals: Dict[str, Any] = field(
        default_factory=lambda: {
            "requests": 0,
            "by_kind": {},
            "by_endpoint_kind": {"rest": 0, "graphql": 0},
        }
    )
    file_targets: Dict[str, FileTargetUsage] = field(default_factory=dict)
    requests: Optional[list] = None
    rate_limited: bool = False
    rate_limit_remaining: Optional[int] = None
    rate_limit_reset: Optional[str] = None
    errors: Dict[str, Any] = field(default_factory=lambda: {"count": 0, "details": []})
    started_at: str = field(default_factory=_utcnow_iso)
    # Optional table persistence context — enables persist_operation_to_table() to write complete operation state
    table_manager: Any = None
    purpose: Optional[str] = None
    job_id: Optional[str] = None

    def record_request(
        self,
        *,
        method: str,
        endpoint: str,
        endpoint_kind: str,
        purpose: Optional[str] = None,
        target_key: Optional[str] = None,
        status_code: Optional[int] = None,
        rate_remaining: Optional[int] = None,
        rate_reset: Optional[str] = None,
        cache_hit: bool = False,
    ) -> None:
        self.totals["requests"] = int(self.totals.get("requests", 0)) + (0 if cache_hit else 1)
        endpoint_counts = self.totals.setdefault("by_endpoint_kind", {})
        endpoint_counts.setdefault("rest", 0)
        endpoint_counts.setdefault("graphql", 0)
        if not cache_hit:
            endpoint_counts[endpoint_kind] = int(endpoint_counts.get(endpoint_kind, 0)) + 1

        resolved_purpose = purpose or self.purpose
        if resolved_purpose:
            by_kind = self.totals.setdefault("by_kind", {})
            by_kind[resolved_purpose] = int(by_kind.get(resolved_purpose, 0)) + (0 if cache_hit else 1)

        if rate_remaining is not None:
            self.rate_limit_remaining = rate_remaining
        if rate_reset:
            self.rate_limit_reset = rate_reset

        if target_key:
            usage = self.file_targets.setdefault(target_key, FileTargetUsage())
            if cache_hit:
                usage.cache_hits += 1
            else:
                usage.attempts += 1

        if self.requests is not None:
            self.requests.append(
                {
                    "timestamp": _utcnow_iso(),
                    "method": method,
                    "endpoint_kind": endpoint_kind,
                    "endpoint": endpoint,
                    "purpose": resolved_purpose,
                    "target_key": target_key,
                    "cache_hit": cache_hit,
                    "status_code": status_code,
                    "rate_remaining": rate_remaining,
                    "rate_reset": rate_reset,
                }
            )

        # Persist operation to table after each request
        self.persist_operation_to_table()

    def has_errors(self) -> bool:
        return int(self.errors.get("count", 0)) > 0

    def record_error(
        self,
        error_type: str,
        endpoint: str,
        *,
        status_code: Optional[int] = None,
        message: str = "",
    ) -> None:
        """Record a critical error (timeout, rate limit, HTTP error).
        
        Args:
            error_type: One of "timeout", "connection_error", "rate_limited", "http_error", "graphql_error"
            endpoint: API endpoint that failed (e.g., "repos/user/repo/contents/file")
            status_code: HTTP status code if applicable
            message: Error message/details for debugging
        """
        self.errors["count"] = int(self.errors.get("count", 0)) + 1
        error_detail = {
            "timestamp": _utcnow_iso(),
            "type": error_type,
            "endpoint": endpoint,
            "status_code": status_code,
            "message": message[:200] if message else "",  # Truncate to avoid bloat
        }
        details = self.errors.setdefault("details", [])
        details.append(error_detail)

        # Set rate_limited flag when rate_limited error is recorded
        if error_type == "rate_limited":
            self.rate_limited = True

        # Persist operation to table after each error
        self.persist_operation_to_table()

    def persist_operation_to_table(self) -> None:
        """Persist complete operation state to RepoAPIUsage table.
        
        Writes the full operational picture: REST calls, GraphQL calls, cache hits,
        rate limit state, and all errors bundled together. Call this at the end of
        each logical operation (per-repo or per-user) to capture the entire data set.
        
        No-ops silently if table_manager context is not set.
        """
        if self.table_manager is None:
            return
        try:
            import json as _json
            from foliohive_shared.table.table_manager import RepoAPIUsageRow
            
            safe_ts = self.started_at.replace(":", "-").replace("+", "_")
            
            endpoint_counts = self.totals.get("by_endpoint_kind", {})
            rest_calls = int(endpoint_counts.get("rest", 0))
            graphql_calls = int(endpoint_counts.get("graphql", 0))

            if rest_calls == 0 and graphql_calls == 0:
                total_requests = int(self.totals.get("requests", 0))
                by_kind = self.totals.get("by_kind", {})
                rest_calls = sum(
                    count
                    for kind, count in by_kind.items()
                    if kind not in ["graphql_batch", "graphql_blob_batch"]
                )
                graphql_calls = total_requests - rest_calls if rest_calls <= total_requests else 0
            
            # Count cache hits across all file targets
            cache_hits = sum(target.cache_hits for target in self.file_targets.values())
            
            row = RepoAPIUsageRow(
                username=self.owner,
                operation_key=f"{self.purpose or 'purpose'}|{safe_ts}|{self.repo}",
                purpose=self.purpose or "unknown",
                job_id=self.job_id,
                repo_name=self.repo,
                api_calls_rest=rest_calls,
                api_calls_graphql=graphql_calls,
                cache_hits=cache_hits,
                rate_limit_remaining=self.rate_limit_remaining,
                rate_limit_reset=self.rate_limit_reset,
                error=self.get_error_summary(),
                error_details=_json.dumps(self.errors.get("details", [])) if self.has_errors() else None,
                created_at=self.started_at,
            )
            self.table_manager.upsert_api_usage(row)
        except Exception as exc:
            logger.warning("[API_USAGE_PERSIST_OPERATION] Failed to persist operation to table: %s", exc)

    def get_error_summary(self) -> Optional[str]:
        """Get a concise error summary for logging/storage.
        
        Returns:
            Comma-separated error types (e.g., "timeout,rate_limited") or None if no errors.
        """
        has_errors = int(self.errors.get("count", 0)) > 0
        if not has_errors:
            return None
        details = self.errors.get("details", [])
        error_types = [d.get("type", "unknown") for d in details]
        # Deduplicate and join
        unique_types = list(dict.fromkeys(error_types))  # Preserve order, remove duplicates
        return ",".join(unique_types)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": {"owner": self.owner, "name": self.repo, "ref": self.ref},
            "totals": self.totals,
            "file_targets": {k: vars(v) for k, v in self.file_targets.items()},
            "rate_limited": self.rate_limited,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset": self.rate_limit_reset,
            "errors": self.errors,
            "started_at": self.started_at,
            "purpose": self.purpose,
            "job_id": self.job_id,
        }