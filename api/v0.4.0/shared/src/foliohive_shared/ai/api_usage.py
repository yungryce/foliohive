from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("foliohive.ai.api_usage")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AIUsageTracker:
    owner: str
    purpose: str
    model_name: str
    model_tier: str
    provider: str = "openai"
    repo_name: Optional[str] = None
    job_id: Optional[str] = None
    budget_completion_tokens: int = 0
    prompt_tokens_estimated: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: Optional[str] = None
    was_truncated: bool = False
    status: str = "started"
    errors: Dict[str, Any] = field(default_factory=lambda: {"count": 0, "details": []})
    started_at: str = field(default_factory=_utcnow_iso)
    updated_at: Optional[str] = None
    table_manager: Any = None

    def record_result(
        self,
        *,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        finish_reason: Optional[str] = None,
        was_truncated: bool = False,
        status: str = "completed",
    ) -> None:
        self.prompt_tokens = int(prompt_tokens or 0)
        self.completion_tokens = int(completion_tokens or 0)
        self.total_tokens = int(total_tokens or (self.prompt_tokens + self.completion_tokens))
        self.finish_reason = finish_reason
        self.was_truncated = bool(was_truncated)
        self.status = status
        self.updated_at = _utcnow_iso()
        self.persist_request_to_table()

    def has_errors(self) -> bool:
        return int(self.errors.get("count", 0)) > 0

    def record_error(
        self,
        error_type: str,
        *,
        message: str = "",
        finish_reason: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        was_truncated: bool = False,
    ) -> None:
        if prompt_tokens is not None:
            self.prompt_tokens = int(prompt_tokens or 0)
        if completion_tokens is not None:
            self.completion_tokens = int(completion_tokens or 0)
        if total_tokens is not None:
            self.total_tokens = int(total_tokens or 0)
        elif self.prompt_tokens or self.completion_tokens:
            self.total_tokens = int(self.prompt_tokens + self.completion_tokens)
        self.errors["count"] = int(self.errors.get("count", 0)) + 1
        details = self.errors.setdefault("details", [])
        details.append(
            {
                "timestamp": _utcnow_iso(),
                "type": error_type,
                "finish_reason": finish_reason or self.finish_reason,
                "message": message[:500] if message else "",
            }
        )
        if finish_reason and not self.finish_reason:
            self.finish_reason = finish_reason
        self.was_truncated = bool(self.was_truncated or was_truncated)
        self.status = "failed"
        self.updated_at = _utcnow_iso()
        self.persist_request_to_table()

    def get_error_summary(self) -> Optional[str]:
        if not self.has_errors():
            return None
        details = self.errors.get("details", [])
        error_types = [detail.get("type", "unknown") for detail in details]
        unique_types = list(dict.fromkeys(error_types))
        return ",".join(unique_types)

    def persist_request_to_table(self) -> None:
        if self.table_manager is None:
            return
        try:
            from foliohive_shared.table.table_manager import AIRequestUsageRow

            row = AIRequestUsageRow(
                username=self.owner,
                operation_key="|".join(
                    part
                    for part in (
                        self.purpose or "purpose",
                        self.started_at,
                        self.repo_name or self.job_id,
                    )
                    if part
                ),
                purpose=self.purpose or "unknown",
                provider=self.provider,
                model_name=self.model_name,
                model_tier=self.model_tier,
                job_id=self.job_id,
                repo_name=self.repo_name,
                budget_completion_tokens=int(self.budget_completion_tokens or 0),
                prompt_tokens_estimated=int(self.prompt_tokens_estimated or 0),
                prompt_tokens=int(self.prompt_tokens or 0),
                completion_tokens=int(self.completion_tokens or 0),
                total_tokens=int(self.total_tokens or 0),
                finish_reason=self.finish_reason,
                was_truncated=bool(self.was_truncated),
                status=self.status,
                error=self.get_error_summary(),
                error_details=json.dumps(self.errors.get("details", []), separators=(",", ":")) if self.has_errors() else None,
                created_at=self.started_at,
                updated_at=self.updated_at or self.started_at,
            )
            self.table_manager.upsert_ai_request_usage(row)
        except Exception as exc:
            logger.warning("[AI_USAGE_PERSIST_REQUEST] Failed to persist AI request usage: %s", exc)
