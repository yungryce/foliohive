import json
import importlib
import os
import logging
import time
from typing import Dict, Any, Optional, List
from openai import OpenAI

logger = logging.getLogger("foliohive.ai_assistant")
logger.setLevel(logging.INFO)
logger.propagate = True


def _resolve_default_table_manager() -> Optional[Any]:
    try:
        from foliohive_shared.table import get_table_manager

        return get_table_manager()
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Failed to resolve default table manager for AIAssistant: %s", exc)
        return None

# Model configuration - OpenAI GPT models only
MODEL_CONFIG = {
    "default": {
        "name": "gpt-5-nano",
        "provider": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
    },
    "balanced": {
        "name": "gpt-4o-mini",
        "provider": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
    },
}

class AIAssistant:
    """
    Builds rich context from repository data and generates AI responses.
    Uses OpenAI GPT models with gpt-5-nano as default for optimal cost/performance.
    """

    def __init__(self, username: Optional[str] = None, table_manager: Optional[Any] = None):
        """Initialize the AI Assistant with OpenAI API credentials."""
        logger.info("Initializing AI Assistant for user: %s", username or "<unknown>")
        self.username = username
        self.table_manager = table_manager if table_manager is not None else _resolve_default_table_manager()
        
        # Initialize OpenAI client (single provider)
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            logger.error("OPENAI_API_KEY not configured - AI processing disabled")
            self.client = None
        else:
            try:
                self.client = OpenAI(
                    api_key=self.openai_api_key,
                    base_url="https://api.openai.com/v1"
                )
                logger.info("Initialized OpenAI client with gpt-5-nano as default")
            except Exception as e:
                logger.error("Failed to initialize OpenAI client: %s", str(e))
                self.client = None

    def _get_model_name(self, model_tier: str = "default") -> str:
        """Get model name for specified tier."""
        model_config = MODEL_CONFIG.get(model_tier, MODEL_CONFIG["default"])
        return model_config["name"]

    def _estimate_tokens(self, *parts: Optional[str]) -> int:
        """Estimate token count using a simple character heuristic."""
        text = "\n".join(part for part in parts if isinstance(part, str) and part)
        return len(text) // 4 if text else 0
    

    # ---------------------------------------------------------------------------
    # Core method to call OpenAI API with prepared messages and handle response
    # ---------------------------------------------------------------------------


    def call_ai_api(
        self,
        system_message: str,
        query: str,
        request_id: str,
        model_tier: str = "default",
        max_completion_tokens: int = 1500,
        *,
        response_format: Optional[Dict[str, Any]] = None,
        purpose: str = "unknown",
        job_id: Optional[str] = None,
        repo_name: Optional[str] = None,
    ) -> str:
        """
        Call OpenAI API with the prepared messages using specified model tier.
        
        Args:
            system_message: System prompt
            query: User query
            request_id: Request ID for logging
            model_tier: Model tier to use (default=gpt-5-nano, balanced=gpt-4o-mini)
            max_completion_tokens: Maximum tokens for response (default=1500, readme=800, profile=1200, query=1500)
            
        Returns:
            AI response string
        """
        model_name = self._get_model_name(model_tier)
        tracker_module = importlib.import_module("foliohive_shared.ai.api_usage")
        tracker_cls = getattr(tracker_module, "AIUsageTracker")

        tracker = tracker_cls(
            owner=self.username or "unknown",
            request_id=request_id,
            purpose=purpose,
            model_name=model_name,
            model_tier=model_tier,
            job_id=job_id,
            repo_name=repo_name,
            budget_completion_tokens=max_completion_tokens,
            prompt_tokens_estimated=self._estimate_tokens(system_message, query),
            table_manager=self.table_manager,
        )

        if not self.client:
            raise Exception("AI service not configured. Please check OPENAI_API_KEY.")
        
        try:
            logger.info("Request ID: %s - Calling OpenAI API (model: %s, max_tokens: %d)", request_id, model_name, max_completion_tokens)
            response = self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": query}
                ],
                max_completion_tokens=max_completion_tokens,
                **({"response_format": response_format} if response_format else {}), 
                stream=False
            )

            usage = getattr(response, "usage", None)
            choice = response.choices[0] if getattr(response, "choices", None) else None
            finish_reason = getattr(choice, "finish_reason", None)
            content = choice.message.content

            if finish_reason == "length":
                raise Exception("Response truncated due to length limits")
            
            if content is None or (isinstance(content, str) and not content.strip()):
                raise Exception("AI response was empty or whitespace")
            
            tracker.record_result(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
                finish_reason=finish_reason,
                was_truncated=finish_reason == "length",
                status="completed",
            )
            return content
        
        except Exception as e:
            tracker.record_error("openai_error", message=str(e))
            raise

    # ---------------------------------------------------------------------------
    # Private method to generate repo summaries using Queue workflow 
    # returns structured JSON for micro-summary, which can be cached 
    # and used for multiple purposes (profile summary, query answering, expanded repo narrative)
    # ---------------------------------------------------------------------------

    def summarize_repo_micro_summary_json(
        self,
        *,
        repo_name: str,
        repo_context: Dict[str, Any],
        model_tier: str = "default",
        purpose: str = "summarize_repo_micro_summary_json",
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a strict JSON micro-summary for one repository."""
        if not repo_context:
            return {"error": "empty_context"}
        if not self.client:
            return {"error": "ai_service_not_configured"}

        system_message = self._build_repo_micro_summary_system(repo_name)
        query = (
            "Generate a repository micro-summary from this context JSON.\n\n"
            + json.dumps(repo_context, ensure_ascii=False)
        )
        request_id = f"repo-micro-{int(time.time())}"
        logger.info("Generating micro-summary for repo: %s with context keys: %s", repo_name, list(repo_context.keys()))
        
        try:
            result = self.call_ai_api(
                system_message,
                query,
                request_id,
                model_tier=model_tier,
                max_completion_tokens=3000,
                response_format={"type": "json_object"},
                purpose=purpose,
                job_id=job_id,
                repo_name=repo_name,
            )
            # Parse JSON response
            parsed = json.loads(result)
            if not isinstance(parsed, dict):
                logger.error("Invalid JSON root for repo: %s - %s", repo_name, result[:300])
                return {"error": "invalid_json_root", "raw_sample": result[:300]}
            return parsed
        except json.JSONDecodeError as e:
            logger.error("JSON parse error for repo: %s - %s", repo_name, str(e))
            return {"error": "invalid_json_response", "details": str(e)}
        except Exception as e:
            logger.error("Error generating micro-summary for repo: %s - %s", repo_name, str(e))
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Public methods to expand summaries and generate profile and query narratives using the core API call method
# ---------------------------------------------------------------------------

    def expand_repo(
        self,
        *,
        username: str,
        repo_name: str,
        micro_summary: Dict[str, Any],
        repo_metadata: Dict[str, Any],
        model_tier: str = "default",
        purpose: str = "expand_micro_summary",
        job_id: Optional[str] = None,
    ) -> str:
        """Expand concise micro-summary into detailed HTML summary for repo detail view.
        
        Takes a structured micro-summary and enriches it with deeper analysis
        and recruiting insights for a richer single-repo view.
        
        Args:
            repo_name: Repository name
            micro_summary: Cached micro-summary dict with structured signals
            repo_metadata: Repository metadata (name, description, language, etc.)
            model_tier: Model tier for AI call
            
        Returns:
            HTML string (detailed repo summary)
        """
        if not self.client:
            return "<p>AI service not configured.</p>"

        system_message = self._build_expand_micro_summary_system(username, repo_name, purpose)
        request_id = f"expand-micro-{int(time.time())}"
        
        try:
            result = self.call_ai_api(
                system_message,
                json.dumps(micro_summary, ensure_ascii=False),
                request_id,
                model_tier=model_tier,
                max_completion_tokens=3000,
                purpose=purpose,
                job_id=job_id,
                repo_name=repo_name,
            )
            if not result or not result.strip():
                return "_Failed to generate expanded summary._"
            return result
        except Exception as e:
            logger.error("Error expanding micro-summary for %s: %s", repo_name, str(e))
            return f"<p>Error generating expanded summary: {str(e)}</p>"
        

    def summarize_profile(
        self,
        *,
        username: str,
        profile: Dict[str, Any],
        aggregate: Dict[str, Any],
        model_tier: str = "default",
        purpose: str = "summarize_profile",
        job_id: Optional[str] = None,
    ) -> str:
        """Generate markdown profile summary from pre-aggregated micro-summary signals.
        
        Args:
            username: GitHub username
            profile: GitHub profile metadata dict (bio, followers, public_repos, etc.)
            aggregate: Aggregated profile built from repo micro-summaries
            model_tier: Model tier for AI call
            purpose: Purpose label for usage tracking
            job_id: Job ID for usage tracking
            
        Returns:
            Markdown string (recruiting-focused profile narrative)
        """
        if not self.client:
            return "_AI service not configured._"

        system_message = self._build_profile_summary_system(username, purpose)
        payload = {
            "username": username,
            "profile": profile,
            "aggregate": aggregate,
        }
        request_id = f"profile-summary-{int(time.time())}"
        
        try:
            result = self.call_ai_api(
                system_message,
                json.dumps(payload, ensure_ascii=False),
                request_id,
                model_tier=model_tier,
                max_completion_tokens=2000,
                purpose=purpose,
                job_id=job_id,
            )
            if not result or not result.strip():
                return "_Failed to generate profile summary._"
            return result
        except Exception as e:
            logger.error("Error generating profile summary for %s: %s", username, str(e))
            return f"_Error generating profile summary: {str(e)}_"


    def summarize_query(
        self,
        *,
        username: str,
        query: str,
        profile: Dict[str, Any],
        aggregate: Dict[str, Any],
        model_tier: str = "default",
        purpose: str = "summarize_query",
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate recruiter-facing answer from aggregate + selected repo summaries."""
        if not self.client:
            return {"error": "ai_service_not_configured"}

        system_message = self._build_query_from_summaries_system(username, query, purpose)
        payload = {
            "query": query,
            "profile": profile,
            "aggregate": aggregate,
        }
        request_id = f"query-summaries-{int(time.time())}"
        
        try:
            result = self.call_ai_api(
                system_message,
                json.dumps(payload, ensure_ascii=False),
                request_id,
                model_tier=model_tier,
                max_completion_tokens=2500,
                purpose=purpose,
                job_id=job_id,
            )
            if not result or not result.strip():
                return "_Failed to generate query summary._"
            return result
        except Exception as e:
            logger.error("Error generating query summary for %s: %s", username, str(e))
            return f"_Error generating query summary: {str(e)}_"


    # ---------------------------------------------------------------------------
    # Context Builder methods to build system prompts for different tasks
    # ---------------------------------------------------------------------------

    def _build_repo_micro_summary_system(self, repo_name: Optional[str] = None) -> str:
        """Build system prompt for JSON repo micro-summary output."""
        label = repo_name or "the repository"
        return (
            f"You analyze {label} and return JSON only.\n"
            "Do not return markdown, prose, or code fences.\n\n"
            "INPUT CONTEXT STRUCTURE:\n"
            "The input is a JSON object containing repository metadata and file contents:\n"
            "- repo_name: string (repository name)\n"
            "- description: string (repository description from GitHub)\n"
            "- languages: array (programming languages used, by frequency)\n"
            "- topics: array (GitHub topics/tags)\n"
            "- stats: object (GitHub metrics: stars, forks, issues, watchers)\n"
            "- readme_chunk: string (primary README.md content, chunked to fit token budget)\n"
            "- config_chunks: array of {filename, content} objects (extracted config files: package.json, requirements.txt, Dockerfile, pom.xml, etc.)\n"
            "- secondary_readme_chunks: array of {index, content} (additional README files like readme.rst, README.txt, etc., if present)\n\n"
            "ANALYSIS INSTRUCTIONS:\n"
            "1. Use readme_chunk as primary source for project overview, features, and purpose.\n"
            "2. Use config_chunks to infer tech stack, dependencies, build process, containerization, orchestration.\n"
            "3. Use secondary_readme_chunks only for supplementary details if primary README is incomplete.\n"
            "4. Combine all evidence to assess architecture, patterns, and skill signals.\n"
            "5. Be specific: reference actual tools, frameworks, and practices found in the configs and README.\n\n"
            "OUTPUT SCHEMA:\n"
            "Return valid JSON object with exactly these top-level keys:\n"
            "overview, key_features, tech_stack, architecture_patterns, skill_signals\n"
            "Constraints:\n"
            "- overview: string (2-3 sentences max)\n"
            "- key_features: array of short strings (5 max)\n"
            "- tech_stack: object with three arrays: {languages: [], frameworks: [], tools: []} (each array max 5 items, short strings)\n"
            "- architecture_patterns: array of short strings (max 3 items)\n"
            "- skill_signals: array of objects {skill, confidence, evidence} where confidence is 0-1 float and evidence is short string (max 6 items)\n"
            "Keep response concise; must be under 2000 tokens.\n"
        )
    

    def _build_expand_micro_summary_system(self, username: str, repo_name: str) -> str:
        """Build system prompt for expanding repo micro-summary into detailed HTML narrative."""
        return (
            f"You are a technical recruiter assistant. Narrate a detailed analysis of {repo_name} "
            f"for GitHub user {username} known as the candidate "
            "using ONLY the structured micro-summary and metadata provided in the JSON payload. "
            "Do not invent details not present in the data.\n\n"
            "INPUT STRUCTURE:\n"
            "The payload has three keys:\n"
            "- repo_name: string (repository name)\n"
            "- repo_metadata: GitHub repository metadata containing:\n"
            "  - name, description, language, topics, stars, forks, watchers\n"
            "- micro_summary: concise structured analysis built from README + configs, containing:\n"
            "  - overview: string (2-3 sentence project summary)\n"
            "  - key_features: array of strings (5 max, core functionality)\n"
            "  - tech_stack: object with {languages: [], frameworks: [], tools: []} (tech choices and dependencies)\n"
            "  - architecture_patterns: array of strings (max 3, design patterns observed in code structure)\n"
            "  - skill_signals: array of {skill, confidence (0–1), evidence} (developer competencies inferred from code)\n\n"
            "ANALYSIS INSTRUCTIONS:\n"
            "1. Use micro_summary.overview as project foundation; expand with purpose and scope context.\n"
            "2. Use key_features to develop narrative about functionality and design decisions.\n"
            "3. Use tech_stack to discuss technology choices and explain breadth of the technology footprint.\n"
            "4. Use architecture_patterns to characterise engineering approach and code organization quality.\n"
            "5. Use skill_signals to identify developer competencies and translate patterns into expertise areas.\n"
            "6. Use repo_metadata (stars, forks, topics) only for context; do not speculate beyond provided data.\n"
            "7. Omit sections where data is absent or low-confidence.\n\n"
            "OUTPUT RULES:\n"
            "- Return ONLY valid Markdown. No HTML tags, no code fences wrapping the whole output.\n"
            "- Use ## for top-level sections, ### for subsections, - for bullet lists.\n"
            "- Keep bullets short (one line each). Max 3–5 bullets per section.\n"
            "- Total length: 300–500 words.\n"
        )


    def _build_profile_summary_system(self, username: str) -> str:
        """Build system prompt for markdown profile summary from pre-aggregated signals."""
        return (
            f"You are a technical recruiter assistant. Narrate a recruiting profile for {username} "
            "using ONLY the pre-aggregated signals provided in the JSON payload. "
            "Do not invent details not present in the data.\n\n"
            "INPUT STRUCTURE:\n"
            "The payload has three keys:\n"
            "- profile: GitHub account metadata (name, bio, location, public_repos, followers, following, created_at)\n"
            "- aggregate: cross-repo signal aggregation built from AI-analysed micro-summaries. Contains:\n"
            "  - repos_included: array of repo names that contributed signals\n"
            "  - skills: array of {skill, frequency, avg_confidence, score, evidence[]} "
            "sorted by score descending — frequency is how many repos show this skill, "
            "avg_confidence is the mean AI confidence (0–1), score = frequency × avg_confidence\n"
            "  - domains: array of {domain, count} — normalised tech stack values (languages, frameworks, tools) "
            "with how many repos use each\n"
            "  - experience_signals.architecture_patterns: array of {pattern, count} — design patterns seen across repos\n"
            "  - experience_signals.repo_count: number of repos analysed\n"            
            "- username: the GitHub login\n\n"
            "ANALYSIS INSTRUCTIONS:\n"
            "1. Use skills.score and skills.frequency to rank importance — high score + high frequency = strong evidence.\n"
            "2. Use skills.evidence strings as specific supporting details.\n"
            "3. Use domains to describe the breadth of the technology footprint.\n"
            "4. Use architecture_patterns to characterise engineering approach.\n"
            "5. Use profile fields (bio, public_repos, followers) for the overview only.\n"
            "6. Do not speculate beyond what the signals show. Omit sections where data is absent.\n\n"
            "OUTPUT RULES:\n"
            "- Return ONLY valid Markdown. No HTML tags, no code fences wrapping the whole output.\n"
            "- Use ## for top-level sections, ### for subsections, - for bullet lists.\n"
            "- Keep bullets short (one line each). Max 3–5 bullets per section.\n"
            "- Total length: 250–450 words.\n\n"
            "OUTPUT STRUCTURE:\n"
            "## Overview\n"
            "1–2 sentences from profile bio + repo_count context.\n\n"
            "## Skills & Expertise\n"
            "Bullet list of top skills with brief evidence note (use skills.evidence).\n\n"
            "## Technology Stack\n"
            "Bullet list grouping domains by type (languages vs frameworks vs tools).\n\n"
            "## Engineering Patterns\n"
            "Bullet list from architecture_patterns. Skip if empty.\n\n"
            "## Best Fit\n"
            "Bullet list of team types or project types this developer suits, inferred from signals.\n"
        )

    def _build_query_from_summaries_system(self, username: str, query: str) -> str:
        """Build system prompt for query responses using aggregate + selected summaries."""
        return (
            f"You are a technical recruiter assistant. Answer the recruiter's query using ONLY the signals "
            f"provided in the JSON payload for GitHub user {username} known as the candidate. \nn"
            "Do not invent details not present in the data. \n"
            f"Recruiter query: {query}\n\n"
            "INPUT STRUCTURE:\n"
            "The payload has four keys:\n"
            "- query: the recruiter's specific question\n"
            "- profile: GitHub account metadata (name, bio, location, public_repos, followers, following, created_at)\n"
            "- aggregate: cross-repo signal aggregation built from AI-analysed micro-summaries. Contains:\n"
            "  - repos_included: array of repo names that contributed signals\n"
            "  - skills: array of {skill, frequency, avg_confidence, score, evidence[]} "
            "sorted by score descending — frequency is how many repos show this skill, "
            "avg_confidence is the mean AI confidence (0–1), score = frequency × avg_confidence\n"
            "  - domains: array of {domain, count} — normalised tech stack values (languages, frameworks, tools) "
            "with how many repos use each\n"
            "  - experience_signals.architecture_patterns: array of {pattern, count} — design patterns seen across repos\n"
            "  - experience_signals.repo_count: number of repos analysed\n"
            "  - repo_name: string\n"
            "  - micro_summary: {overview, key_features, tech_stack: {languages, frameworks, tools}, "
            "architecture_patterns, skill_signals: [{skill, confidence, evidence}]}\n\n"
            "ANALYSIS INSTRUCTIONS:\n"
            "1. Answer the query directly using the most relevant evidence from aggregate.\n"
            "2. Cite repository names from aggregate when using specific evidence.\n"
            "3. Use aggregate.skills scores and frequency to assess overall depth of a skill.\n"
            "4. Use aggregate.domains to describe technology breadth.\n"
            "5. Mention uncertainty explicitly if evidence is absent or weak.\n"
            "6. Do not speculate beyond what the signals show.\n\n"
            "OUTPUT RULES:\n"
            "- Return ONLY valid Markdown. No HTML tags, no code fences wrapping the whole output.\n"
            "- Start with a direct answer to the query (1–2 sentences).\n"
            "- Use ## for sections if elaborating, - for bullet lists.\n"
            "- Keep bullets short (one line each). Max 3–5 bullets per section.\n"
            "- Total length: 150–350 words.\n"
        )
