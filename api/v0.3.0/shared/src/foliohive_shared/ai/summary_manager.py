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
from foliohive_shared.cache.cache_manager import cache_manager
from foliohive_shared.table import get_table_manager, RepoCacheSummaryRow

logger = logging.getLogger("foliohive.summary_manager")
logger.setLevel(logging.INFO)
logger.propagate = True


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
        "readme": 8000,       # 8-10 repos with rich README context
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
    "default": {
        "metadata": 512,
        "readme": 1024,
        "config": 512,
        "reserve": 512,
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
        self.table_manager = get_table_manager()

    # ---------------------------------------------------------------------------
    # Public API - High-Level Summary Methods
    # ---------------------------------------------------------------------------


    def generate_repo_micro_summary(
        self,
        *,
        username: str,
        repo_name: str,
        fingerprint: str,
        job_id: Optional[str] = None,
        repo_metadata: Dict[str, Any],
        primary_readme_content: Optional[str],
        config_content: Optional[Dict[str, str]],
        secondary_readme_content: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate and cache JSON micro-summary.
        
        Assumes cache entry was registered with pending status before calling.
        Updates status to 'valid' on success or leaves as 'pending' on failure.
        """
        
        # Check cache table first
        cached = self.get_cache_repo_micro_summary(username, repo_name, fingerprint)
        if cached:
            logger.info(f"Cache hit for micro-summary of {repo_name} (fingerprint: {fingerprint})")
            return {"cache_hit": True, "summary": True, "tokens_estimated": 0}
        
        token_budget = TOKEN_BUDGETS["default"]  # Use default budget for micro-summary generation

        context = self.build_repo_context(
            repo_metadata=repo_metadata,
            primary_readme_content=primary_readme_content,
            config_files=config_content or {},
            secondary_readme_content=secondary_readme_content or [],
            token_budget=token_budget,
        )
        logger.info(f"Context built for micro-summary of {repo_name} (fingerprint: {fingerprint}) with estimated tokens: {context.get('tokens_estimated', 0)}")


        summary = self.ai_assistant.summarize_repo_micro_summary_json(
            repo_name=repo_name,
            repo_context=context,
            model_tier=MODEL_ASSIGNMENTS["readme"],
            purpose="get_repo_micro_summary",
            job_id=job_id,
        )

        logger.info(f"Micro-summary generation attempted for {repo_name} (fingerprint: {fingerprint}) - validating response")
        if "error" not in summary:
            # Validate schema before caching
            validation = self._validate_micro_summary_schema(summary, repo_name)
            if validation.get("status") != "valid":
                error_code = validation.get("error", "schema_validation_failed")
                logger.error(f"Schema validation failed for {repo_name}: {error_code}")
                return {
                    "cache_hit": False,
                    "summary": False,
                    "error": error_code,
                    "tokens_estimated": context.get("tokens_estimated", 0),
                }
            
            key = self.build_repo_micro_summary_cache_key(repo_name, fingerprint)
            cache_manager.save(key, summary)

            table_manager = get_table_manager()
            cache_row = RepoCacheSummaryRow(
                username=self.username,
                repo_name=repo_name,
                fingerprint=fingerprint,
                cache_key=key,
                cache_status="valid",
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
            table_manager.upsert_cache_summary(cache_row)
            
            return {
                "cache_hit": False,
                "summary": True,
                "tokens_estimated": context.get("tokens_estimated", 0),
            }
        
        # summarize_repo_micro_summary_json always returns dict (with error key on failure)
        last_error = summary.get("error")
        return {
            "cache_hit": False,
            "summary": False,
            "error": last_error,
            "tokens_estimated": context.get("tokens_estimated", 0),
        }


    def expand_repo_micro_summary(
        self,
        *,
        repo_name: str,
        job_id: str,
    ) -> Dict[str, Any]:
        """Expand micro-summary into detailed HTML summary for repo detail view.
        
        This takes a concise micro-summary and enriches it with deeper analysis
        and recruiting insights for the single-repo view.
        
        Args:
            username: GitHub username
            repo_name: Repository name
            job_id: Job ID for cache invalidation
            
        Returns:
            Dict with summary_html (cached) and metadata
        """
        
        start_time = time.time()
        summary_type = "repo"

        repo_metadata = self.table_manager.get_repo_github_metadata(job_id, repo_name)
        if repo_metadata.get("job_id") == job_id:
            repo_metadata_fingerprint = repo_metadata.get("fingerprint") if repo_metadata else None
            cached = self.get_cache_repo_micro_summary(repo_name, repo_metadata_fingerprint)
            if cached:
                expanded_markdown = self.ai_assistant.expand_repo(
                    username=self.username,
                    repo_name=repo_name,
                    micro_summary=cached,
                    repo_metadata=repo_metadata,
                    model_tier=MODEL_ASSIGNMENTS.get("readme", "default"),
                    purpose="expand_repo_micro_summary",
                )

            metadata = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "job_id": job_id,
                "summary_type": summary_type,
                "generation_time_ms": int((time.time() - start_time) * 1000),
            }
            return {"summary_markdown": expanded_markdown, "metadata": metadata}
        return None
    

    def get_or_generate_profile_summary(
        self,
        *,
        job_id: str,
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Get cached or generate new profile summary.
        
        Args:
            username: GitHub username
            job_id: Job ID for cache invalidation
            profile: GitHub user profile dict
            
        Returns:
            Dict with summary_html, metadata (cache_hit, tokens, etc.)
        """
        start_time = time.time()
        summary_type = "profile"

        micro_summaries = self._load_cached_micro_summaries(job_id)
        aggregate = self.aggregate_micro_summaries(micro_summaries=micro_summaries)
        
        # Second-stage AI summarization: aggregate + profile → markdown narrative
        summary_markdown = self.ai_assistant.summarize_profile(
            username=self.username,
            profile=profile,
            aggregate=aggregate,
            model_tier=MODEL_ASSIGNMENTS["profile"],
            purpose="get_profile_summary",
            job_id=job_id,
        )

        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "repos_total": len(micro_summaries),
            "generation_time_ms": int((time.time() - start_time) * 1000),
        }
        return {"summary_markdown": summary_markdown, "metadata": metadata}


    def get_or_generate_query_response(
        self,
        *,
        job_id: str,
        query: str,
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Get cached or generate new query response with multi-repo context.
        
        Args:
            job_id: Job ID for cache invalidation
            query: User query string
            profile: GitHub user profile dict
            
        Returns:
            Dict with response (markdown), repositories_used, metadata
        """
        start_time = time.time()
        summary_type = "query"

        micro_summaries = self._load_cached_micro_summaries(job_id=job_id)
        aggregate = self.aggregate_micro_summaries(micro_summaries=micro_summaries)

        result = self.ai_assistant.summarize_query(
            query=query,
            profile=profile,
            aggregate=aggregate,
            model_tier=MODEL_ASSIGNMENTS["query"],
            purpose="get_query_response",
            job_id=job_id,
        )

        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "repos_total": len(micro_summaries),
            "generation_time_ms": int((time.time() - start_time) * 1000),
        }
        result["metadata"] = metadata
        return result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

    def aggregate_micro_summaries(
        self,
        *,
        micro_summaries: List[Dict[str, Any]],
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
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        fingerprint = self.calculate_fingerprint("profile_aggregate", micro_summaries)
        cache_manager.save(self.build_profile_aggregate_cache_key(fingerprint), aggregate)
        return aggregate


    def build_repo_micro_summary_cache_key(self, repo_name: str, fingerprint: str) -> str:
        """Build cache key for micro-summary blob storage.
        
        Args:
            repo_name: Repository name (may contain /)
            fingerprint: Content fingerprint for cache invalidation
            
        Returns:
            Safe cache key for blob storage
        """
        safe_repo = str(repo_name).replace("/", "_").replace(" ", "_")
        safe_fingerprint = str(fingerprint).replace("/", "_").replace(" ", "_")
        return f"repo_micro_summary:{self.username}:{safe_repo}:{safe_fingerprint}"

    def build_profile_aggregate_cache_key(self, fingerprint: str) -> str:
        safe = str(fingerprint).replace("/", "_").replace(" ", "_")
        return f"profile_aggregate:{self.username}:{safe}"

    def build_expanded_summary_cache_key(self, repo_name: str, fingerprint: str) -> str:
        """Build cache key for expanded repo summary HTML."""
        safe_repo = str(repo_name).replace("/", "_").replace(" ", "_")
        safe_fingerprint = str(fingerprint).replace("/", "_").replace(" ", "_")
        return f"repo_expanded_summary:{self.username}:{safe_repo}:{safe_fingerprint}"

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


    def _load_cached_micro_summaries(self, job_id: str) -> List[Dict[str, Any]]:
        """Load micro-summaries from cache using repository metadata rows.
        
        Args:
            job_id: Job ID for which to load micro-summaries
            
        Returns:
            List of loaded micro-summaries with repo_name, fingerprint, and micro_summary data
        """
        all_repos = self.table_manager.query_repo_github_metadata(job_id) 
        for repo in all_repos if repo.get("job_id") == job_id else []:
            repo_name = repo.get("repo_name")
            fingerprint = repo.get("fingerprint")

            valid_summaries = self.table_manager.list_valid_cache_summaries(self.username)
            micro_summaries: List[Dict[str, Any]] = []

            if valid_summaries and valid_summaries.get("repo_name") == repo_name and valid_summaries.get("fingerprint") == fingerprint and valid_summaries.get("status") == "valid":

                cache_key = valid_summaries.get("cache_key")

                # Cache entry exists and is valid - fetch from blob
                cached = cache_manager.get(cache_key)
                if cached.get("status") == "valid":
                    data = cached.get("data")

                micro_summaries.append(data) if isinstance(data, dict) else None
                return micro_summaries
            
        return None
        

    def get_cache_repo_micro_summary(self, repo_name: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        """Get micro-summary from cache with table validation."""


        cache_entry = self.table_manager.get_cache_summary(self.username, repo_name, fingerprint)
        if not cache_entry or cache_entry.get("cache_status") != "valid":
            return None  # Not cached or stale
        
        # Cache entry exists and is valid - fetch from blob
        cache_key = cache_entry.get("cache_key")
        cached = cache_manager.get(cache_key)
        if cached.get("status") == "valid":
            data = cached.get("data")
            return data if isinstance(data, dict) else None
        
        return None

    # ---------------------------------------------------------------------------
    # Token Estimation & Chunking Utilities
    # ---------------------------------------------------------------------------
    # NOTE: Chunking is now minimal as FILE_BUDGETS control retrieval limits.
    # Content received is already sized appropriately per summary type.
    # These methods primarily handle edge cases and final token estimation.

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count using char-based approximation.
        
        Args:
            text: Input text to estimate (must be string)
            
        Returns:
            Estimated token count (1 token ≈ 4 chars)
            
        Note:
            Returns 0 if text is empty or None. Assumes 1 token ≈ 4 characters.
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
        """Truncate pre-extracted config content to token budget.
        
        Args:
            filename: Name of config file (for logging only, all inputs pre-extracted)
            content: JSON-serialized extracted config dict (already optimized by upstream extraction)
            max_tokens: Maximum tokens allowed
            
        Returns:
            Truncated content or full content if under budget
        """
        if not content:
            return ""
        
        max_chars = max_tokens * 4
        
        # Return full content if under budget
        if len(content) <= max_chars:
            return content
        
        # Truncate to budget and append indicator
        return content[:max_chars] + "\n... [truncated]"

    # ---------------------------------------------------------------------------
    # Context Building
    # ---------------------------------------------------------------------------

    def build_repo_context(
        self,
        repo_metadata: Dict[str, Any],
        token_budget: Dict[str, int],
        primary_readme_content: Optional[str],
        config_files: Optional[Dict[str, str]],
        secondary_readme_content: Optional[List[str]] = None,
        
    ) -> Dict[str, Any]:
        """Build standardized repo context with chunking and token budgeting.
        
        Token budget priority (in order):
        1. Primary README (high priority from budget["readme"])
        2. Config files (medium priority from budget["config"])
        3. Secondary READMEs (low priority from remaining budget)
        
        Args:
            repo_metadata: Repository metadata (name, description, languages, etc.)
            primary_readme_content: Full primary README content (string)
            config_files: Dict of {filename: content_string}. Values MUST be JSON strings
                         (pre-serialized from extracted dicts). Token estimation depends on
                         receiving strings, not dicts.
            secondary_readme_content: List of secondary README contents (strings)
            token_budget: Token budget allocation dict with keys: readme, config, reserve
            
        Returns:
            Structured repo context dict with chunked content and token accounting
        """
        context = {
            "repo_name": repo_metadata.get("name", "Unknown"),
            "description": repo_metadata.get("description", ""),
            "languages": repo_metadata.get("languages", []),
            "topics": repo_metadata.get("topics", []),
            "stats": repo_metadata.get("stats", {}),
            "readme_chunk": "",
            "config_chunks": [],
            "secondary_readme_chunks": [],
        }

        tokens_used = self.estimate_tokens(json.dumps(context))
        logger.info(f"Initial context for {context['repo_name']} estimated tokens: {tokens_used}")
        
        # Priority 1: Chunk primary README within allocated budget
        if primary_readme_content and token_budget.get("readme", 0) > 0:
            readme_budget = token_budget["readme"]
            context["readme_chunk"] = self.chunk_readme(primary_readme_content, readme_budget)
            tokens_used += self.estimate_tokens(context["readme_chunk"])
            logger.info(f"Chunked primary README for {context['repo_name']} to fit {readme_budget} tokens, estimated tokens used: {self.estimate_tokens(context['readme_chunk'])}")
        
        # Priority 2: Chunk config files within allocated budget
        if config_files and token_budget.get("config", 0) > 0:
            config_budget = token_budget["config"]
            config_budget_per_file = config_budget // max(len(config_files), 1)
            
            for filename, content in config_files.items():
                chunked = self.chunk_config_file(filename, content, config_budget_per_file)
                logger.info(f"[Chunk] filename: {filename}, original length: {len(content)}, chunked length: {len(chunked)}, budget: {config_budget_per_file} tokens...")
                context["config_chunks"].append({
                    "filename": filename,
                    "content": chunked
                })
                tokens_used += self.estimate_tokens(chunked)
                logger.info(f"Chunked config file {filename} for {context['repo_name']} to fit {config_budget_per_file} tokens, estimated tokens used: {self.estimate_tokens(chunked)}")
        
        # Priority 3: Chunk secondary READMEs with remaining budget (if any)
        if secondary_readme_content and token_budget.get("reserve", 0) > 0:
            # Calculate remaining budget after primary readme and configs
            total_budget = token_budget.get("readme", 0) + token_budget.get("config", 0) + token_budget.get("reserve", 0)
            remaining_budget = total_budget - tokens_used
            
            if remaining_budget > 0 and len(secondary_readme_content) > 0:
                secondary_budget_per_file = remaining_budget // len(secondary_readme_content)
                
                for idx, readme_content in enumerate(secondary_readme_content):
                    if not readme_content:
                        continue
                    
                    chunked = self.chunk_readme(readme_content, secondary_budget_per_file)
                    context["secondary_readme_chunks"].append({
                        "index": idx,
                        "content": chunked
                    })
                    tokens_used += self.estimate_tokens(chunked)
                    logger.info(f"Chunked secondary README {idx} for {context['repo_name']} to fit {secondary_budget_per_file} tokens, estimated tokens used: {self.estimate_tokens(chunked)}")
                
                logger.info(f"[Secondary READMEs] {context['repo_name']} - included {len(context['secondary_readme_chunks'])} files with {remaining_budget} token budget")
        
        context["tokens_estimated"] = tokens_used
        logger.info("[Context] %s", context)
        return context


    # ---------------------------------------------------------------------------
    # Schema Validation
    # ---------------------------------------------------------------------------
    
    def _validate_micro_summary_schema(self, micro_summary: Dict[str, Any], repo_name: str) -> Dict[str, Any]:
        """Validate micro-summary JSON structure before caching.
        
        Checks for required top-level keys and expected types. Does not deeply validate
        nested content — focuses on structural integrity to prevent cache poisoning.
        
        Args:
            micro_summary: Parsed JSON micro-summary dict
            repo_name: Repository name for logging
            
        Returns:
            Dict with status 'valid' or 'invalid' and optional error details
        """
        if not isinstance(micro_summary, dict):
            logger.warning("Micro-summary for %s is not a dict: %s", repo_name, type(micro_summary))
            return {"status": "invalid", "error": "root_not_dict"}
        
        required_keys = {"overview", "key_features", "tech_stack", "architecture_patterns", "skill_signals"}
        missing = required_keys - set(micro_summary.keys())
        if missing:
            logger.warning("Micro-summary for %s missing keys: %s", repo_name, missing)
            return {"status": "invalid", "error": f"missing_keys:{','.join(sorted(missing))}"}
        
        # Validate key field types
        if not isinstance(micro_summary.get("overview"), str):
            logger.warning("Micro-summary for %s: overview is not string", repo_name)
            return {"status": "invalid", "error": "overview_not_string"}
        
        if not isinstance(micro_summary.get("key_features"), list):
            logger.warning("Micro-summary for %s: key_features is not list", repo_name)
            return {"status": "invalid", "error": "key_features_not_list"}
        
        if not isinstance(micro_summary.get("tech_stack"), dict):
            logger.warning("Micro-summary for %s: tech_stack is not dict", repo_name)
            return {"status": "invalid", "error": "tech_stack_not_dict"}
        
        tech_stack = micro_summary.get("tech_stack", {})
        expected_tech_keys = {"languages", "frameworks", "tools"}
        for key in expected_tech_keys:
            if not isinstance(tech_stack.get(key), list):
                logger.warning("Micro-summary for %s: tech_stack.%s is not list", repo_name, key)
                return {"status": "invalid", "error": f"tech_stack_{key}_not_list"}
        
        if not isinstance(micro_summary.get("architecture_patterns"), list):
            logger.warning("Micro-summary for %s: architecture_patterns is not list", repo_name)
            return {"status": "invalid", "error": "architecture_patterns_not_list"}
        
        if not isinstance(micro_summary.get("skill_signals"), list):
            logger.warning("Micro-summary for %s: skill_signals is not list", repo_name)
            return {"status": "invalid", "error": "skill_signals_not_list"}
        
        # Validate skill_signals items have required fields
        for idx, signal in enumerate(micro_summary.get("skill_signals", [])):
            if not isinstance(signal, dict):
                logger.warning("Micro-summary for %s: skill_signals[%d] is not dict", repo_name, idx)
                return {"status": "invalid", "error": f"skill_signals_item_not_dict"}
            if "skill" not in signal or "confidence" not in signal:
                logger.warning("Micro-summary for %s: skill_signals[%d] missing skill or confidence", repo_name, idx)
                return {"status": "invalid", "error": "skill_signals_missing_fields"}
        
        logger.info("Micro-summary for %s schema validation passed", repo_name)
        return {"status": "valid"}


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
