"""Summary Manager for AI-generated summaries with caching and context building.

Handles context orchestration, token budget management, content chunking,
and fingerprint-based cache invalidation for candidate profiles and repositories.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from foliohive_shared.ai import AIAssistant
from foliohive_shared import cache_manager

logger = logging.getLogger(__name__)


# Model configuration - OpenAI GPT models only
MODEL_CONFIG = {
    "default": {
        "name": "gpt-5-nano",
        "provider": "openai",
        "context_window": 128000,  # Assumed standard for GPT-5 nano
        "cost_per_1m_input": 0.05,
        "cost_per_1m_output": 0.40,
        "use_case": "Default for all summaries - best value with cached input support"
    },
    "balanced": {
        "name": "gpt-4o-mini",
        "provider": "openai",
        "context_window": 128000,
        "cost_per_1m_input": 0.15,
        "cost_per_1m_output": 0.60,
        "use_case": "Higher quality when needed - better for complex analysis"
    },
}

# Model selection per summary type (optimized for gpt-5-nano)
MODEL_ASSIGNMENTS = {
    "profile": "default",        # Use gpt-5-nano for profile summaries
    "readme": "default",         # Use gpt-5-nano for repo summaries
    "query": "default",          # Use gpt-5-nano for queries
    "initial_summary": "default", # Use gpt-5-nano for bulk processing
}

# Token budget configuration per summary type
# Optimized for recruiting analysis with gpt-5-nano
# Leverages cached input pricing ($0.005 vs $0.05) for repeated context
TOKEN_BUDGETS = {
    "profile": {
        "metadata": 3000,      # Profile + repo metadata
        "readme": 25000,       # 8-10 repos with rich README context
        "config": 15000,       # Config files for comprehensive skill inference
        "reserve": 2000,       # Safety margin for prompt overhead
        # Total: ~45k tokens (gpt-5-nano with caching)
    },
    "readme": {
        "metadata": 2000,      # Single repo metadata
        "readme": 18000,       # Single README - comprehensive coverage
        "config": 5000,        # Supporting config files
        "reserve": 1000,       # Safety margin
        # Total: ~26k tokens (gpt-5-nano optimized)
    },
    "query": {
        "metadata": 2000,      # Query + repo list metadata
        "readme": 22000,       # 4-6 selected repos with detailed context
        "config": 10000,       # Config context for skill validation
        "reserve": 2000,       # Reserve for query overhead
        # Total: ~36k tokens (gpt-5-nano with caching)
    },
    "initial_summary": {
        "metadata": 1000,      # Minimal metadata for bulk processing
        "readme": 8000,        # Lighter README chunks
        "config": 3000,        # Basic config context
        "reserve": 500,        # Minimal reserve
        # Total: ~12.5k tokens (gpt-5-nano budget tier)
    },
}

# File retrieval budget configuration per summary type
# Aligns with TOKEN_BUDGETS to optimize cache retrieval and minimize over-fetching
#
# Workflow:
#   1. Endpoint calls get_file_budget(summary_type) to get limits
#   2. Passes limits to _get_repo_files(max_readme_files, max_config_files)
#   3. RepoCacheRetrieval.get_repo_files() enforces limits at retrieval
#   4. SummaryManager receives right-sized content (minimal chunking needed)
#
# Benefits:
#   - Single source of truth for limits per summary type
#   - No over-fetching from cache storage
#   - Minimal redundant chunking in AI layer
#   - Easy to tune independently per use case
FILE_BUDGETS = {
    "profile": {
        "max_repos": 20,              # 8 repos for profile context
        "max_readme_files": 5,       # Testing with only config files for profile summaries
        "max_config_files": 20,       # 2 key config files per repo
    },
    "readme": {
        "max_repos": 1,              # Single repo focus
        "max_readme_files": 5,       # Primary + 2 additional readmes
        "max_config_files": 5,       # 3 config files for detailed analysis
    },
    "query": {
        "max_repos": 8,              # Up to 8 repos for query context
        "max_readme_files": 0,       # Only primary readme per repo
        "max_config_files": 2,       # 2 config files per repo
    },
    "initial_summary": {
        "max_repos": 10,             # More repos but lighter content
        "max_readme_files": 0,       # Only primary readme
        "max_config_files": 1,       # Minimal config context
    },
}

# Repo selection strategies for query context
REPO_SELECTION_STRATEGIES = {
    "recent": "last_updated",      # Most recently updated repos (default)
    "random": "random_sample",     # Random selection for diversity
    "top_starred": "stars_desc",   # Most starred repos
}


def get_file_budget(summary_type: str) -> Dict[str, int]:
    """Get file retrieval budget for a summary type.
    
    Args:
        summary_type: Type of summary (profile, readme, query, initial_summary)
    
    Returns:
        Dict with max_repos, max_readme_files, max_config_files
    """
    return FILE_BUDGETS.get(summary_type, FILE_BUDGETS["profile"])


class SummaryManager:
    """Orchestrates AI summarization with caching and context building."""

    def __init__(self, username: str):
        """Initialize SummaryManager.
        
        Args:
            username: GitHub username for the candidate
        """
        self.username = username
        self.ai_assistant = AIAssistant(username=username)

    # ---------------------------------------------------------------------------
    # Public API - High-Level Summary Methods
    # ---------------------------------------------------------------------------

    def get_or_generate_profile_summary(
        self,
        *,
        job_id: str,
        profile: Dict[str, Any],
        repo_rows: List[Dict[str, Any]],
        statistics: Dict[str, Any],
        repo_files: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Get cached or generate new profile summary.
        
        Args:
            job_id: Job ID for cache invalidation
            profile: GitHub user profile dict
            repo_rows: List of repository metadata rows
            languages_by_repo: Languages data by repo name
            statistics: Aggregated statistics
            repo_files: Optional dict of {repo_name: {readme, configs}}
            
        Returns:
            Dict with summary_html, metadata (cache_hit, tokens, etc.)
        """
        start_time = time.time()
        summary_type = "profile"

        micro_summaries: List[Dict[str, Any]] = []
        for repo in repo_rows:
            repo_name = repo.get("repo_name") or repo.get("name")
            fingerprint = repo.get("fingerprint")
            if not repo_name or not fingerprint:
                continue
            cached = self.get_repo_micro_summary(repo_name, fingerprint)
            if not cached:
                continue
            micro_summaries.append(
                {
                    "repo_name": repo_name,
                    "fingerprint": fingerprint,
                    "micro_summary": cached,
                }
            )

        aggregate = self.aggregate_profile_from_summaries(
            micro_summaries=micro_summaries,
            profile=profile,
            statistics=statistics,
        )
        summary_html = self.format_profile_html(aggregate)

        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "repos_included": len(micro_summaries),
            "repos_total": len(repo_rows),
            "generation_time_ms": int((time.time() - start_time) * 1000),
        }
        return {"summary_html": summary_html, "metadata": metadata, "aggregate": aggregate}

    def get_or_generate_readme_summary(
        self,
        *,
        job_id: str,
        repo_name: str,
        repo_metadata: Dict[str, Any],
        repo_files: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get cached or generate new README summary.
        
        Args:
            job_id: Job ID for cache invalidation
            repo_name: Repository name
            repo_metadata: Repository metadata dict
            repo_files: Dict with {readme_content, readme_files, config_files}
            
        Returns:
            Dict with summary_html, metadata (cache_hit, tokens, etc.)
        """
        start_time = time.time()
        summary_type = "readme"
        
        # Extract file contents from repo_files dict
        readme_content = repo_files.get("readme_content")
        config_files = repo_files.get("config_files", {})
        
        # Build context
        token_budget = TOKEN_BUDGETS.get(summary_type, {})
        context = self.build_repo_context(
            repo_metadata=repo_metadata,
            readme_content=readme_content,
            config_files=config_files,
            token_budget=token_budget
        )
        
        # Generate summary with appropriate model tier
        model_tier = MODEL_ASSIGNMENTS.get(summary_type, "default")
        summary_html = self.ai_assistant.summarize_readme_html(
            readme_text=readme_content,
            repo_name=repo_name,
            model_tier=model_tier
        )
        
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "repo_name": repo_name,
            "tokens_estimated": context.get("tokens_estimated", 0),
            "files_included": len(config_files) + 1,  # +1 for README
            "generation_time_ms": int((time.time() - start_time) * 1000),
        }
        
        # Track metrics
        self.track_generation_metrics(
            summary_type,
            start_time,
            metadata["tokens_estimated"],
            metadata["files_included"],
            cache_hit=False
        )
        
        return {
            "summary_html": summary_html,
            "metadata": metadata
        }

    def get_or_generate_query_response(
        self,
        *,
        job_id: str,
        query: str,
        repo_rows: List[Dict[str, Any]],
        repo_files: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Get cached or generate new query response with multi-repo context.
        
        Args:
            job_id: Job ID for cache invalidation
            query: User query string
            repo_rows: List of repository metadata rows (pre-selected by strategy)
            repo_files: Dict of {repo_name: {readme, configs}} file contents
            selection_strategy: Strategy used for repo selection (for logging/metadata)
            max_repos: Maximum repos (for logging/metadata)
            
        Returns:
            Dict with response (markdown), repositories_used, metadata
        """
        start_time = time.time()
        summary_type = "query"

        micro_summaries: List[Dict[str, Any]] = []
        for repo in repo_rows:
            repo_name = repo.get("repo_name") or repo.get("name")
            fingerprint = repo.get("fingerprint")
            if not repo_name or not fingerprint:
                continue
            cached = self.get_repo_micro_summary(repo_name, fingerprint)
            if not cached:
                continue
            micro_summaries.append(
                {
                    "repo_name": repo_name,
                    "fingerprint": fingerprint,
                    "micro_summary": cached,
                    "repo_metadata": repo,
                }
            )

        aggregate = self.aggregate_profile_from_summaries(micro_summaries=micro_summaries)
        result = self.query_from_summaries(
            query=query,
            profile_aggregate=aggregate,
            repo_micro_summaries=micro_summaries,
            max_repos=get_file_budget("query").get("max_repos", 8),
        )
        result["metadata"] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "generation_time_ms": int((time.time() - start_time) * 1000),
        }
        return result

    def build_profile_aggregate_cache_key(self, fingerprint: str) -> str:
        safe = str(fingerprint).replace("/", "_").replace(" ", "_")
        return f"profile_aggregate:{self.username}:{safe}"

    def build_profile_html_cache_key(self, fingerprint: str) -> str:
        safe = str(fingerprint).replace("/", "_").replace(" ", "_")
        return f"profile_html:{self.username}:{safe}"

    def build_query_response_cache_key(self, query_fingerprint: str) -> str:
        safe = str(query_fingerprint).replace("/", "_").replace(" ", "_")
        return f"query_response:{self.username}:{safe}"

    def calculate_fingerprint(self, summary_type: str, inputs: List[Dict[str, Any]]) -> str:
        """Calculate stable fingerprint from structured inputs for cache invalidation."""
        normalized_inputs: List[Dict[str, Any]] = []
        for item in inputs or []:
            if not isinstance(item, dict):
                continue
            if item.get("fingerprint"):
                normalized_inputs.append({"fingerprint": str(item.get("fingerprint"))})
                continue
            normalized_inputs.append(item)
        payload = {
            "summary_type": summary_type,
            "username": self.username,
            "inputs": normalized_inputs,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def aggregate_profile_from_summaries(
        self,
        *,
        micro_summaries: List[Dict[str, Any]],
        profile: Optional[Dict[str, Any]] = None,
        statistics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Aggregate repo micro-summaries into profile-level JSON."""
        skills: Dict[str, Dict[str, Any]] = {}
        domains: Dict[str, int] = {}
        architecture_counts: Dict[str, int] = {}
        included_repos: List[str] = []

        for item in micro_summaries:
            repo_name = item.get("repo_name")
            micro = item.get("micro_summary")
            if not repo_name or not isinstance(micro, dict):
                continue
            included_repos.append(repo_name)

            for pattern in micro.get("architecture_patterns", []) or []:
                if not pattern:
                    continue
                key = str(pattern).strip().lower()
                architecture_counts[key] = architecture_counts.get(key, 0) + 1

            tech_stack = micro.get("tech_stack") or {}
            for domain_key in ("languages", "frameworks", "tools"):
                for value in tech_stack.get(domain_key, []) or []:
                    normalized = str(value).strip().lower()
                    if not normalized:
                        continue
                    domains[normalized] = domains.get(normalized, 0) + 1

            for signal in micro.get("skill_signals", []) or []:
                if not isinstance(signal, dict):
                    continue
                skill_name = str(signal.get("skill") or "").strip().lower()
                if not skill_name:
                    continue
                confidence = signal.get("confidence")
                try:
                    conf = float(confidence)
                except (TypeError, ValueError):
                    conf = 0.0
                evidence = str(signal.get("evidence") or "").strip()
                entry = skills.setdefault(
                    skill_name,
                    {"skill": skill_name, "count": 0, "confidence_sum": 0.0, "evidence": []},
                )
                entry["count"] += 1
                entry["confidence_sum"] += max(0.0, min(conf, 1.0))
                if evidence and evidence not in entry["evidence"]:
                    entry["evidence"].append(evidence)

        skill_list = []
        for value in skills.values():
            count = value["count"]
            avg_conf = value["confidence_sum"] / max(count, 1)
            score = round(count * avg_conf, 3)
            skill_list.append(
                {
                    "skill": value["skill"],
                    "frequency": count,
                    "avg_confidence": round(avg_conf, 3),
                    "score": score,
                    "evidence": value["evidence"][:3],
                }
            )
        skill_list.sort(key=lambda item: (item["score"], item["frequency"]), reverse=True)

        domain_list = [
            {"domain": key, "count": count}
            for key, count in sorted(domains.items(), key=lambda pair: pair[1], reverse=True)
        ]
        architecture_list = [
            {"pattern": key, "count": count}
            for key, count in sorted(architecture_counts.items(), key=lambda pair: pair[1], reverse=True)
        ]

        aggregate = {
            "username": self.username,
            "repos_included": included_repos,
            "skills": skill_list,
            "domains": domain_list,
            "experience_signals": {
                "architecture_patterns": architecture_list,
                "repo_count": len(included_repos),
            },
            "profile": profile or {},
            "statistics": statistics or {},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        fingerprint = self.calculate_fingerprint("profile_aggregate", micro_summaries)
        cache_manager.save(self.build_profile_aggregate_cache_key(fingerprint), aggregate)
        return aggregate

    def format_profile_html(self, aggregate_profile: Dict[str, Any]) -> str:
        """Render HTML from profile aggregate JSON only (no AI call)."""
        if not aggregate_profile:
            return "<p>No profile aggregate available.</p>"

        profile = aggregate_profile.get("profile") or {}
        name = profile.get("name") or self.username
        skills = aggregate_profile.get("skills") or []
        domains = aggregate_profile.get("domains") or []
        patterns = (aggregate_profile.get("experience_signals") or {}).get("architecture_patterns", [])

        def _list_items(items: List[str]) -> str:
            if not items:
                return "<li>No data available.</li>"
            return "".join(f"<li>{item}</li>" for item in items)

        top_skills = [
            f"<strong>{item.get('skill')}</strong> (freq: {item.get('frequency')}, score: {item.get('score')})"
            for item in skills[:8]
        ]
        top_domains = [f"{item.get('domain')} ({item.get('count')})" for item in domains[:8]]
        top_patterns = [f"{item.get('pattern')} ({item.get('count')})" for item in patterns[:8]]

        html = (
            f"<h2>Overview</h2><p>{name} has evidence across {len(aggregate_profile.get('repos_included', []))} repositories.</p>"
            f"<h3>Skills</h3><ul>{_list_items(top_skills)}</ul>"
            f"<h3>Domains</h3><ul>{_list_items(top_domains)}</ul>"
            f"<h3>Experience Signals</h3><ul>{_list_items(top_patterns)}</ul>"
        )

        fingerprint = self.calculate_fingerprint("profile_html", [aggregate_profile])
        cache_manager.save(self.build_profile_html_cache_key(fingerprint), {"summary_html": html})
        return html

    def query_from_summaries(
        self,
        *,
        query: str,
        profile_aggregate: Dict[str, Any],
        repo_micro_summaries: List[Dict[str, Any]],
        max_repos: int = 5,
    ) -> Dict[str, Any]:
        """Answer query using only aggregate profile + repo micro-summaries."""
        query_tokens = {token for token in (query or "").lower().split() if token}

        ranked: List[Dict[str, Any]] = []
        for item in repo_micro_summaries:
            micro = item.get("micro_summary") or {}
            haystack_parts: List[str] = []
            for key in ("overview",):
                value = micro.get(key)
                if isinstance(value, str):
                    haystack_parts.append(value.lower())
            for section in ("key_features", "architecture_patterns"):
                for value in micro.get(section, []) or []:
                    haystack_parts.append(str(value).lower())
            tech = micro.get("tech_stack") or {}
            for section in ("languages", "frameworks", "tools"):
                for value in tech.get(section, []) or []:
                    haystack_parts.append(str(value).lower())
            haystack = " ".join(haystack_parts)
            score = sum(1 for token in query_tokens if token in haystack)
            ranked.append({**item, "relevance": score})

        ranked.sort(key=lambda row: row.get("relevance", 0), reverse=True)
        selected = [row for row in ranked if row.get("relevance", 0) > 0][:max_repos]
        if not selected:
            selected = ranked[:max_repos]

        highlights = []
        for row in selected:
            repo_name = row.get("repo_name")
            micro = row.get("micro_summary") or {}
            overview = str(micro.get("overview") or "No overview available.")
            highlights.append(f"- **{repo_name}**: {overview}")

        top_skills = [item.get("skill") for item in (profile_aggregate.get("skills") or [])[:6] if item.get("skill")]
        response_text = (
            f"Query: {query}\n\n"
            f"Top profile skills: {', '.join(top_skills) if top_skills else 'No skills aggregated yet.'}\n\n"
            "Relevant repositories:\n"
            + ("\n".join(highlights) if highlights else "- No matching repository summaries available.")
        )

        query_fingerprint = self.calculate_fingerprint(
            "query",
            [{"query": query}, profile_aggregate, {"repos": [row.get("repo_name") for row in selected]}],
        )
        response_payload = {
            "response": response_text,
            "repositories_used": [
                {"name": row.get("repo_name"), "relevance": row.get("relevance", 0)} for row in selected
            ],
            "total_repositories": len(repo_micro_summaries),
            "query": query,
        }
        cache_manager.save(self.build_query_response_cache_key(query_fingerprint), response_payload)
        return response_payload

    def build_repo_micro_summary_cache_key(self, repo_name: str, fingerprint: str) -> str:
        safe_repo = str(repo_name).replace("/", "_").replace(" ", "_")
        safe_fingerprint = str(fingerprint).replace("/", "_").replace(" ", "_")
        return f"repo_micro_summary:{self.username}:{safe_repo}:{safe_fingerprint}"

    def get_repo_micro_summary(self, repo_name: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        key = self.build_repo_micro_summary_cache_key(repo_name, fingerprint)
        cached = cache_manager.get(key)
        if cached.get("status") == "valid":
            data = cached.get("data")
            return data if isinstance(data, dict) else None
        return None

    def _validate_micro_summary_schema(self, payload: Dict[str, Any]) -> bool:
        required = {"overview", "key_features", "tech_stack", "architecture_patterns", "skill_signals"}
        if not isinstance(payload, dict):
            return False
        if not required.issubset(set(payload.keys())):
            return False
        if not isinstance(payload.get("overview"), str):
            return False
        if not isinstance(payload.get("key_features"), list):
            return False
        if not isinstance(payload.get("tech_stack"), dict):
            return False
        if not isinstance(payload.get("architecture_patterns"), list):
            return False
        if not isinstance(payload.get("skill_signals"), list):
            return False
        return True

    def generate_repo_micro_summary(
        self,
        *,
        repo_name: str,
        fingerprint: str,
        repo_metadata: Dict[str, Any],
        readme_content: Optional[str],
        config_files: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Generate and cache JSON micro-summary for one repository."""

        logger.info(f"Generating micro-summary for {repo_name} (fingerprint: {fingerprint}) with metadata: {repo_metadata.keys()} and readme length: {len(readme_content) if readme_content else 0} and config files: {list(config_files.keys()) if config_files else []}")

        cached = self.get_repo_micro_summary(repo_name, fingerprint)
        if cached:
            return {"cache_hit": True, "summary": cached}

        token_budget = {
            "metadata": 2000,
            "readme": 8000,
            "config": 2000,
            "reserve": 1000,
        }

        context = self.build_repo_context(
            repo_metadata=repo_metadata,
            readme_content=readme_content,
            config_files=config_files or {},
            token_budget=token_budget,
        )

        attempts = 2
        last_error = None
        for _ in range(attempts):
            summary = self.ai_assistant.summarize_repo_micro_summary_json(
                repo_name=repo_name,
                repo_context=context,
                model_tier=MODEL_ASSIGNMENTS.get("readme", "default"),
            )
            if isinstance(summary, dict) and self._validate_micro_summary_schema(summary):
                key = self.build_repo_micro_summary_cache_key(repo_name, fingerprint)
                cache_manager.save(key, summary)
                summary_response = {
                    "cache_hit": False,
                    "summary": summary,
                    "tokens_estimated": context.get("tokens_estimated", 0),
                }
                logger.info("[Count] %s : [Summary] %s", _, summary)
                return summary_response
            
            last_error = summary.get("error") if isinstance(summary, dict) else "invalid_response"
            logger.info(f"Micro-summary generation attempt failed for {repo_name} (fingerprint: {fingerprint}): {last_error}")

        return {
            "error": "micro_summary_generation_failed",
            "reason": last_error or "schema_validation_failed",
            "tokens_estimated": context.get("tokens_estimated", 0),
        }

    def build_expanded_summary_cache_key(self, repo_name: str, fingerprint: str) -> str:
        """Build cache key for expanded repo summary HTML."""
        safe_repo = str(repo_name).replace("/", "_").replace(" ", "_")
        safe_fingerprint = str(fingerprint).replace("/", "_").replace(" ", "_")
        return f"repo_expanded_summary:{self.username}:{safe_repo}:{safe_fingerprint}"

    def expand_repo_micro_summary(
        self,
        *,
        repo_name: str,
        micro_summary: Dict[str, Any],
        repo_metadata: Dict[str, Any],
        fingerprint: str,
    ) -> Dict[str, Any]:
        """Expand micro-summary into detailed HTML summary for repo detail view.
        
        This takes a concise micro-summary and enriches it with deeper analysis
        and recruiting insights for the single-repo view.
        
        Args:
            repo_name: Repository name
            micro_summary: Cached micro-summary dict
            repo_metadata: Repository metadata (name, description, language, etc.)
            fingerprint: Repo fingerprint for cache invalidation
            
        Returns:
            Dict with summary_html (cached) and metadata
        """
        cache_key = self.build_expanded_summary_cache_key(repo_name, fingerprint)
        cached = cache_manager.get(cache_key)
        if cached.get("status") == "valid":
            cached_html = cached.get("data", {})
            return {
                "cache_hit": True,
                "summary_html": cached_html.get("summary_html", ""),
                "metadata": {"generated_at": cached_html.get("generated_at")},
            }

        # AI call to expand micro-summary into detailed HTML
        start_time = time.time()
        expanded_html = self.ai_assistant.expand_micro_summary_to_html(
            repo_name=repo_name,
            micro_summary=micro_summary,
            repo_metadata=repo_metadata,
            model_tier=MODEL_ASSIGNMENTS.get("readme", "default"),
        )

        # Cache the expanded summary
        generated_at = datetime.now(timezone.utc).isoformat()
        cache_payload = {
            "summary_html": expanded_html,
            "generated_at": generated_at,
            "micro_summary_fingerprint": fingerprint,
        }
        cache_manager.save(cache_key, cache_payload)

        return {
            "cache_hit": False,
            "summary_html": expanded_html,
            "metadata": {
                "generated_at": generated_at,
                "generation_time_ms": int((time.time() - start_time) * 1000),
            },
        }

    # ---------------------------------------------------------------------------
    # Token Estimation & Chunking Utilities
    # ---------------------------------------------------------------------------
    # NOTE: Chunking is now minimal as FILE_BUDGETS control retrieval limits.
    # Content received is already sized appropriately per summary type.
    # These methods primarily handle edge cases and final token estimation.

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count using char-based approximation.
        
        Args:
            text: Input text to estimate
            
        Returns:
            Estimated token count (1 token ≈ 4 chars)
        """
        if not text:
            return 0
        return len(text) // 4

    def chunk_readme(self, content: str, max_tokens: int) -> str:
        """Intelligently truncate README to fit token budget.
        
        Prioritizes top of file (overview, features, quick start).
        Attempts to preserve complete markdown sections.
        
        Args:
            content: Full README content
            max_tokens: Maximum tokens allowed
            
        Returns:
            Chunked README with truncation marker if needed
        """
        if not content:
            return ""
        
        max_chars = max_tokens * 4
        if len(content) <= max_chars:
            return content
        
        # Find last complete section before limit
        truncated = content[:max_chars]
        
        # Look for markdown headers to break at section boundaries
        last_header = max(
            truncated.rfind('\n# '),
            truncated.rfind('\n## '),
            truncated.rfind('\n### ')
        )
        
        # Use header boundary if at least 70% of budget used
        if last_header > max_chars * 0.7:
            return content[:last_header].rstrip() + "\n\n... [README truncated for length]"
        
        # Otherwise truncate at char limit
        return truncated.rstrip() + "\n\n... [README truncated for length]"

    def chunk_config_file(self, filename: str, content: str, max_tokens: int) -> str:
        """Smart truncation for config files by type.
        
        Args:
            filename: Name of config file (used to detect type)
            content: File content
            max_tokens: Maximum tokens allowed
            
        Returns:
            Chunked content optimized per file type
        """
        if not content:
            return ""
        
        max_chars = max_tokens * 4
        
        # Full file if under 500 chars
        if len(content) < 500:
            return content
        
        # Type-specific truncation
        filename_lower = filename.lower()
        
        if filename_lower == "package.json":
            return self._chunk_package_json(content, max_chars)
        elif filename_lower == "dockerfile":
            return self._chunk_dockerfile(content, max_chars)
        elif filename_lower.endswith(".yml") or filename_lower.endswith(".yaml"):
            return self._chunk_yaml(content, max_chars)
        elif filename_lower == "requirements.txt":
            # Usually small, keep all
            return content if len(content) <= max_chars else content[:max_chars]
        else:
            # Generic truncation
            return content[:max_chars] + "\n... [truncated]" if len(content) > max_chars else content

    def _chunk_package_json(self, content: str, max_chars: int) -> str:
        """Extract key sections from package.json."""
        try:
            data = json.loads(content)
            # Keep essential fields
            filtered = {
                "name": data.get("name"),
                "version": data.get("version"),
                "description": data.get("description"),
                "scripts": data.get("scripts", {}),
                "dependencies": data.get("dependencies", {}),
                "engines": data.get("engines"),
            }
            result = json.dumps(filtered, indent=2)
            return result if len(result) <= max_chars else result[:max_chars] + "\n... [truncated]"
        except Exception:
            # Fallback to truncation if parsing fails
            return content[:max_chars] + "\n... [truncated]"

    def _chunk_dockerfile(self, content: str, max_chars: int) -> str:
        """Extract key instructions from Dockerfile."""
        lines = content.split('\n')
        important_lines = []
        
        for line in lines:
            stripped = line.strip()
            # Keep FROM, RUN, EXPOSE, CMD, ENTRYPOINT
            if any(stripped.startswith(keyword) for keyword in ['FROM', 'RUN', 'EXPOSE', 'CMD', 'ENTRYPOINT', 'ENV']):
                important_lines.append(line)
        
        result = '\n'.join(important_lines)
        return result if len(result) <= max_chars else result[:max_chars] + "\n... [truncated]"

    def _chunk_yaml(self, content: str, max_chars: int) -> str:
        """Extract key sections from YAML files."""
        # For workflow files, keep job names and key steps
        lines = content.split('\n')
        result_lines = []
        in_important_section = False
        
        for line in lines:
            stripped = line.strip()
            # Keep job definitions, step names, workflow triggers
            if any(keyword in stripped for keyword in ['name:', 'on:', 'jobs:', 'steps:', 'run:', 'uses:']):
                in_important_section = True
                result_lines.append(line)
            elif in_important_section and line and not line[0].isspace():
                in_important_section = False
        
        result = '\n'.join(result_lines) if result_lines else content[:max_chars]
        return result if len(result) <= max_chars else result[:max_chars] + "\n... [truncated]"

    # ---------------------------------------------------------------------------
    # Context Building
    # ---------------------------------------------------------------------------

    def build_repo_context(
        self,
        repo_metadata: Dict[str, Any],
        readme_content: Optional[str],
        config_files: Optional[Dict[str, str]],
        token_budget: Dict[str, int]
    ) -> Dict[str, Any]:
        """Build standardized repo context with chunking.
        
        Args:
            repo_metadata: Repository metadata (name, description, languages, etc.)
            readme_content: Full README content
            config_files: Dict of {filename: content}
            token_budget: Token budget allocation dict
            
        Returns:
            Structured repo context dict with chunked content
        """
        context = {
            "repo_name": repo_metadata.get("name", "Unknown"),
            "description": repo_metadata.get("description", ""),
            "primary_language": repo_metadata.get("primary_language"),
            "languages": repo_metadata.get("languages", []),
            "topics": repo_metadata.get("topics", []),
            "stats": repo_metadata.get("stats", {}),
            "readme_chunk": "",
            "config_chunks": [],
        }
        logger.info("[Context] %s", context)

        tokens_used = self.estimate_tokens(json.dumps(context))
        
        # Chunk README within budget
        if readme_content and token_budget.get("readme", 0) > 0:
            readme_budget = token_budget["readme"]
            context["readme_chunk"] = self.chunk_readme(readme_content, readme_budget)
            tokens_used += self.estimate_tokens(context["readme_chunk"])
        
        # Chunk config files within budget
        if config_files and token_budget.get("config", 0) > 0:
            config_budget = token_budget["config"]
            config_budget_per_file = config_budget // max(len(config_files), 1)
            
            for filename, content in config_files.items():
                chunked = self.chunk_config_file(filename, content, config_budget_per_file)
                context["config_chunks"].append({
                    "filename": filename,
                    "content": chunked
                })
                tokens_used += self.estimate_tokens(chunked)
        
        context["tokens_estimated"] = tokens_used
        return context

    def build_profile_context(
        self,
        profile: Dict[str, Any],
        repo_rows: List[Dict[str, Any]],
        repo_files: Dict[str, Dict[str, Any]],
        statistics: Dict[str, Any],
        token_budget: Dict[str, int]
    ) -> Dict[str, Any]:
        """Build profile context with multiple repos.
        
        Args:
            profile: GitHub user profile data
            repo_rows: List of top repository metadata dicts
            repo_files: Dict of {repo_name: {readme, configs}} file contents
            statistics: Aggregated statistics
            token_budget: Token budget allocation dict
            
        Returns:
            Structured profile context dict with chunked content
        """
        context = {
            "username": self.username,
            "profile": {
                "name": profile.get("name"),
                "bio": profile.get("bio"),
                "location": profile.get("location"),
                "company": profile.get("company"),
                "blog": profile.get("blog"),
                "public_repos": profile.get("public_repos"),
                "followers": profile.get("followers"),
                "following": profile.get("following"),
            },
            "statistics": statistics,
            "repositories": [],
        }
        
        tokens_used = self.estimate_tokens(json.dumps(context))
        
        # Calculate per-repo budgets
        num_repos = len(repo_rows)
        readme_budget_per_repo = token_budget.get("readme", 0) // max(num_repos, 1)
        config_budget_per_repo = token_budget.get("config", 0) // max(num_repos, 1)
        
        # Build context for each repo
        for repo in repo_rows:
            repo_name = repo.get("repo_name")
            if not repo_name:
                continue
            
            files = repo_files.get(repo_name, {})
            readme = files.get("readme_content", "")
            configs = files.get("config_files", [])
            
            # Build mini repo context
            repo_context = {
                "name": repo_name,
                "description": repo.get("description", ""),
                # "primary_language": repo.get("primary_language"),
                "languages": repo.get("languages", [])[:3],  # Top 3
                # "topics": repo.get("topics", [])[:5],  # Top 5
                # "stars": repo.get("stats", {}).get("stars", 0),
                # "forks": repo.get("stats", {}).get("forks", 0),
            }
            
            # Add chunked README
            if readme and readme_budget_per_repo > 0:
                repo_context["readme_chunk"] = self.chunk_readme(readme, readme_budget_per_repo)
            
            # Add chunked configs
            if configs and config_budget_per_repo > 0:
                config_chunks = []
                # configs is now a dict {filename: content}
                budget_per_file = config_budget_per_repo // max(len(configs), 1)
                
                for filename, content in configs.items():
                    if filename and content:
                        chunked = self.chunk_config_file(filename, content, budget_per_file)
                        config_chunks.append({
                            "filename": filename,
                            "content": chunked
                        })
                
                repo_context["config_chunks"] = config_chunks
            
            context["repositories"].append(repo_context)
            tokens_used += self.estimate_tokens(json.dumps(repo_context))
        
        context["tokens_estimated"] = tokens_used
        return context

    def build_query_bundle_context(
        self,
        query: str,
        repo_rows: List[Dict[str, Any]],
        repo_files: Dict[str, Dict[str, Any]],
        token_budget: Dict[str, int],
    ) -> Dict[str, Any]:
        """Build query context with multiple repo summaries.
        
        Args:
            query: User query string
            repo_rows: List of repository metadata rows (already pre-selected by strategy)
            repo_files: Dict of {repo_name: {readme, configs}} file contents
            token_budget: Token budget allocation dict
            
        Returns:
            Structured query context dict with chunked multi-repo content
        """
        
        # Use pre-selected repos directly (selection done in endpoint via _select_repos_for_context)
        context = {
            "query": query,
            "repositories": [],
        }
        
        tokens_used = self.estimate_tokens(json.dumps({"query": query}))
        
        # Calculate per-repo budgets
        num_repos = len(repo_rows)
        if num_repos == 0:
            context["tokens_estimated"] = tokens_used
            return context
        
        readme_budget_per_repo = token_budget.get("readme", 0) // num_repos
        config_budget_per_repo = token_budget.get("config", 0) // num_repos
        
        # Build context for each repo (already selected)
        for repo in repo_rows:
            repo_name = repo.get("repo_name") or repo.get("name")
            if not repo_name:
                continue
            
            files = repo_files.get(repo_name, {})
            readme = files.get("readme_content", "")
            configs = files.get("config_files", [])
            
            # Build mini repo summary
            repo_summary = {
                "name": repo_name,
                "description": repo.get("description", "")[:200],  # Truncate descriptions
                "primary_language": repo.get("primary_language"),
                "stars": repo.get("stars_count", 0),
                "forks": repo.get("forks_count", 0),
                "last_updated": repo.get("github_updated_at", ""),
            }
            
            # Add chunked README if available
            if readme and readme_budget_per_repo > 0:
                repo_summary["readme_summary"] = self.chunk_readme(readme, readme_budget_per_repo)
            
            # Add chunked configs if available
            if configs and config_budget_per_repo > 0:
                config_summaries = []
                # configs is now a dict {filename: content}
                configs_list = list(configs.items())[:2]  # Max 2 config files per repo for query
                budget_per_file = config_budget_per_repo // max(len(configs_list), 1)
                
                for filename, content in configs_list:
                    if filename and content:
                        chunked = self.chunk_config_file(filename, content, budget_per_file)
                        config_summaries.append({
                            "filename": filename,
                            "content": chunked
                        })
                
                if config_summaries:
                    repo_summary["config_summaries"] = config_summaries
            
            context["repositories"].append(repo_summary)
            tokens_used += self.estimate_tokens(json.dumps(repo_summary))
        
        context["tokens_estimated"] = tokens_used
        context["repos_included"] = len(context["repositories"])
 
        return context

    # ---------------------------------------------------------------------------
    # Metrics & Observability
    # ---------------------------------------------------------------------------

    def track_generation_metrics(
        self,
        summary_type: str,
        start_time: float,
        tokens_estimated: int,
        files_included: int,
        cache_hit: bool
    ) -> None:
        """Log metrics for observability.
        
        Args:
            summary_type: Type of summary generated
            start_time: Generation start time (from time.time())
            tokens_estimated: Estimated token count
            files_included: Number of files included in context
            cache_hit: Whether result came from cache
        """
        duration_ms = int((time.time() - start_time) * 1000)
        
        logger.info(
            "[SUMMARY_METRICS] type=%s user=%s cache_hit=%s duration_ms=%d tokens=%d files=%d",
            summary_type,
            self.username,
            cache_hit,
            duration_ms,
            tokens_estimated,
            files_included
        )
