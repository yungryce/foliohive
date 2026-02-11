from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


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
    totals: Dict[str, Any] = field(default_factory=lambda: {"requests": 0, "by_kind": {}})
    file_targets: Dict[str, FileTargetUsage] = field(default_factory=dict)
    requests: Optional[list] = None
    rate_limited: bool = False

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
        cache_hit: bool = False,
    ) -> None:
        self.totals["requests"] = int(self.totals.get("requests", 0)) + (0 if cache_hit else 1)
        if purpose:
            by_kind = self.totals.setdefault("by_kind", {})
            by_kind[purpose] = int(by_kind.get(purpose, 0)) + (0 if cache_hit else 1)

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
                    "purpose": purpose,
                    "target_key": target_key,
                    "cache_hit": cache_hit,
                    "status_code": status_code,
                    "rate_remaining": rate_remaining,
                }
            )

    def mark_file_target_found(self, target_key: str, *, selected: bool = False, bytes_returned: Optional[int] = None) -> None:
        usage = self.file_targets.setdefault(target_key, FileTargetUsage())
        usage.found = True
        if selected:
            usage.selected = True
        if bytes_returned is not None:
            usage.bytes_returned = bytes_returned

    def mark_rate_limited(self) -> None:
        self.rate_limited = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": {"owner": self.owner, "name": self.repo, "ref": self.ref},
            "totals": self.totals,
            "file_targets": {k: vars(v) for k, v in self.file_targets.items()},
            "rate_limited": self.rate_limited,
        }