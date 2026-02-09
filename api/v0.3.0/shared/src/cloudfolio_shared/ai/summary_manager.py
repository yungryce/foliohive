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

from cloudfolio_shared.ai import AIAssistant

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

# Repo selection strategies for query context
REPO_SELECTION_STRATEGIES = {
    "recent": "last_updated",      # Most recently updated repos (default)
    "random": "random_sample",     # Random selection for diversity
    "top_starred": "stars_desc",   # Most starred repos
}


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
        
        # Build context
        token_budget = TOKEN_BUDGETS.get(summary_type, {})
        
        # Use provided repo files or empty dict
        repo_files = repo_files or {}
        
        context = self.build_profile_context(
            profile=profile,
            repo_rows=repo_rows,
            repo_files=repo_files,
            statistics=statistics,
            token_budget=token_budget
        )
        
        # Generate summary via AIAssistant with appropriate model tier
        model_tier = MODEL_ASSIGNMENTS.get(summary_type, "default")
        summary_html = self.ai_assistant.summarize_profile_html(
            profile_payload=context,
            username=self.username,
            model_tier=model_tier
        )

        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "tokens_estimated": context.get("tokens_estimated", 0),
            "repos_included": len(repo_files),
            "generation_time_ms": int((time.time() - start_time) * 1000),
        }

        # Track metrics
        self.track_generation_metrics(
            summary_type,
            start_time,
            metadata["tokens_estimated"],
            metadata["repos_included"],
            cache_hit=False
        )

        return {
            "summary_html": summary_html,
            "metadata": metadata
        }

    def get_or_generate_readme_summary(
        self,
        *,
        job_id: str,
        repo_name: str,
        readme_content: str,
        repo_metadata: Dict[str, Any],
        config_files: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Get cached or generate new README summary.
        
        Args:
            job_id: Job ID for cache invalidation
            repo_name: Repository name
            readme_content: Full README content
            repo_metadata: Repository metadata dict
            config_files: Optional dict of {filename: content}
            
        Returns:
            Dict with summary_html, metadata (cache_hit, tokens, etc.)
        """
        start_time = time.time()
        summary_type = "readme"
        
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
        
        # Build metadata
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "repo_name": repo_name,
            "tokens_estimated": context.get("tokens_estimated", 0),
            "files_included": len(config_files or {}) + 1,  # +1 for README
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

        # Build query bundle context
        token_budget = TOKEN_BUDGETS.get(summary_type, {})
        context = self.build_query_bundle_context(
            query=query,
            repo_rows=repo_rows,
            repo_files=repo_files,
            token_budget=token_budget,
        )

        # Generate response via AIAssistant with rich context and appropriate model tier
        model_tier = MODEL_ASSIGNMENTS.get(summary_type, "default")
        result = self.ai_assistant.summarize_query_html(
            query=query,
            bundle_context=context,
            model_tier=model_tier
        )

        # Build metadata
        repos_included = context.get("repositories", [])
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "summary_type": summary_type,
            "repositories_used": [
                {"name": r["name"], "stars": r.get("stars", 0), "primary_language": r.get("primary_language")}
                for r in repos_included
            ],
            "total_repositories": len(repo_rows),
            "repos_in_context": len(repos_included),
            "tokens_estimated": context.get("tokens_estimated", 0),
            "files_included": sum(1 for r in repos_included if r.get("readme_summary")),
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
            **result,
            "metadata": metadata
        }

    # ---------------------------------------------------------------------------
    # Token Estimation & Chunking Utilities
    # ---------------------------------------------------------------------------

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
        logger.info("Building profile context for user '%s' with %d repos. Total token budget for repos: %d", self.username, num_repos, token_budget.get("readme", 0) + token_budget.get("config", 0))
        readme_budget_per_repo = token_budget.get("readme", 0) // max(num_repos, 1)
        config_budget_per_repo = token_budget.get("config", 0) // max(num_repos, 1)
        
        # Build context for each repo
        for repo in repo_rows:
            repo_name = repo.get("repo_name")
            logger.info("Processing repo '%s' for profile context. Repo metadata: %s", repo_name, {k: repo.get(k) for k in ['description', 'primary_language', 'languages', 'topics', 'stats']})
            if not repo_name:
                continue
            
            files = repo_files.get(repo_name, {})
            readme = files.get("readme_content", "")
            configs = files.get("config_files", [])
            logger.info("Profile context - processing repo '%s': readme length=%d chars, config files=%d", repo_name, len(readme), len(configs) if isinstance(configs, list) else 0)
            
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
                logger.info("Profile context - repo '%s': README chunked to %d tokens", repo_name, self.estimate_tokens(repo_context["readme_chunk"]))
                logger.info("Profile context - repo '%s': README chunk content preview: %s", repo_name, repo_context["readme_chunk"])
            
            # Add chunked configs
            if configs and config_budget_per_repo > 0:
                config_chunks = []
                configs_list = configs if isinstance(configs, list) else []
                budget_per_file = config_budget_per_repo // max(len(configs_list), 1)
                
                for config_file in configs_list:
                    filename = config_file.get("filename", "")
                    content = config_file.get("content", "")
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
                configs_list = configs if isinstance(configs, list) else []
                budget_per_file = config_budget_per_repo // max(len(configs_list), 1)
                
                for config_file in configs_list[:2]:  # Max 2 config files per repo for query
                    filename = config_file.get("filename", "")
                    content = config_file.get("content", "")
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
