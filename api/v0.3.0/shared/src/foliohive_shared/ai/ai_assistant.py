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
    # Public methods to generate specific summaries (README, profile) using the core API call method
    # ---------------------------------------------------------------------------

    def summarize_readme_html(
        self,
        readme_text: str,
        repo_name: Optional[str] = None,
        model_tier: str = "default",
        *,
        purpose: str = "summarize_readme_html",
        job_id: Optional[str] = None,
    ) -> str:
        """Summarize a README into HTML formatted for the project detail view.
        
        Args:
            readme_text: README content to summarize
            repo_name: Repository name
            model_tier: Model tier to use (default=gpt-5-nano, balanced=gpt-4o-mini)
        """
        if not readme_text:
            return "<p>No README content available.</p>"
        if not self.client:
            return "<p>AI service not configured. Please check OPENAI_API_KEY.</p>"

        system_message = self._build_readme_summary_system(repo_name)
        query = (
            f"Repository: {repo_name or 'Unknown'}\n\n"
            "README:\n"
            f"{readme_text}"
        )
        request_id = f"readme-{int(time.time())}"
        
        try:
            result = self.call_ai_api(
                system_message,
                query,
                request_id,
                model_tier=model_tier,
                max_completion_tokens=4000,
                purpose=purpose,
                job_id=job_id,
                repo_name=repo_name,
            )
            return result
        except Exception as e:
            logger.error("Error summarizing README for %s: %s", repo_name, str(e))
            return f"<p>Error generating README summary: {str(e)}</p>"

    def summarize_profile_html(
        self,
        profile_payload: Dict[str, Any],
        username: Optional[str] = None,
        model_tier: str = "default",
        *,
        purpose: str = "summarize_profile_html",
        job_id: Optional[str] = None,
    ) -> str:
        """Summarize a candidate profile payload into HTML for the profile view.
        
        Args:
            profile_payload: Profile data to summarize
            username: GitHub username
            model_tier: Model tier to use (default=gpt-5-nano, balanced=gpt-4o-mini)
        """
        if not profile_payload:
            return "<p>No profile data available.</p>"
        if not self.client:
            return "<p>AI service not configured. Please check OPENAI_API_KEY.</p>"

        system_message = self._build_profile_summary_system(username or self.username)
        query = json.dumps(profile_payload, ensure_ascii=False)
        request_id = f"profile-{int(time.time())}"
        
        try:
            result = self.call_ai_api(
                system_message,
                query,
                request_id,
                model_tier=model_tier,
                max_completion_tokens=6000,
                purpose=purpose,
                job_id=job_id,
                repo_name=username or self.username,
            )
            return result
        except Exception as e:
            logger.error("Error summarizing profile for %s: %s", username or self.username, str(e))
            return f"<p>Error generating profile summary: {str(e)}</p>"

    def summarize_query_html(
        self,
        query: str,
        bundle_context: Dict[str, Any],
        model_tier: str = "default",
        *,
        purpose: str = "summarize_query_html",
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process query with pre-built bundle context containing multiple repo summaries.
        
        Args:
            query: User query string
            bundle_context: Pre-built context with multiple repo summaries
            model_tier: Model tier to use (default/balanced)
            
        Returns:
            Dictionary with AI response and metadata
        """
        try:
            logger.info("Processing query with bundle context: %d repos", bundle_context.get("repos_included", 0))
            
            if not bundle_context.get("repositories"):
                return {
                    "response": f"No repositories found for {self.username or 'this candidate'}.",
                    "repositories_used": [],
                    "total_repositories": 0,
                    "query": query
                }
            
            # Build system message with bundle context
            system_message = self._build_query_bundle_system(query, bundle_context)
            
            # Call AI with specified model tier
            request_id = f"query-bundle-{int(time.time())}"
            ai_response = self.call_ai_api(
                system_message,
                query,
                request_id,
                model_tier=model_tier,
                max_completion_tokens=4000,
                purpose=purpose,
                job_id=job_id,
            )
            
            # Validate response - check for error messages from call_ai_api
            if not ai_response or "Unable to generate" in ai_response or "encountered an error" in ai_response:
                return {
                    "response": ai_response or "AI processing unavailable.",
                    "repositories_used": [
                        {"name": r["name"], "stars": r.get("stars", 0)}
                        for r in bundle_context.get("repositories", [])
                    ],
                    "total_repositories": bundle_context.get("repos_included", 0),
                    "query": query
                }

            # Build response with metadata
            repositories_used = [
                {"name": r["name"], "stars": r.get("stars", 0), "primary_language": r.get("primary_language")}
                for r in bundle_context.get("repositories", [])
            ]

            return {
                "response": ai_response,
                "repositories_used": repositories_used,
                "total_repositories": bundle_context.get("repos_included", 0),
                "query": query
            }
        except Exception as e:
            logger.error("Error during bundle query processing: %s", str(e), exc_info=True)
            return {
                "response": f"Error processing query: {str(e)}",
                "repositories_used": [],
                "total_repositories": bundle_context.get("repos_included", 0),
                "query": query
            }

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

    def summarize_profile_aggregation_json(
        self,
        *,
        username: str,
        micro_summaries: List[Dict[str, Any]],
        model_tier: str = "default",
        purpose: str = "summarize_profile_aggregation_json",
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate profile aggregate JSON from repo micro-summaries."""
        if not micro_summaries:
            return {"error": "empty_micro_summaries"}
        if not self.client:
            return {"error": "ai_service_not_configured"}

        system_message = self._build_profile_aggregation_system(username)
        query = json.dumps({"username": username, "micro_summaries": micro_summaries}, ensure_ascii=False)
        request_id = f"profile-agg-{int(time.time())}"
        
        try:
            result = self.call_ai_api(
                system_message,
                query,
                request_id,
                model_tier=model_tier,
                max_completion_tokens=2500,
                purpose=purpose,
                job_id=job_id,
                repo_name=username,
            )
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
            logger.error("Invalid JSON root for profile aggregation: %s - %s", username, result[:300])
            return {"error": "invalid_json_root", "raw_sample": result[:300]}
        except json.JSONDecodeError as e:
            logger.error("JSON parse error for profile aggregation: %s - %s", username, str(e))
            return {"error": "invalid_json_response", "details": str(e)}
        except Exception as e:
            logger.error("Exception in profile aggregation for %s: %s", username, str(e))
            return {"error": str(e)}

    def summarize_query_from_summaries(
        self,
        *,
        query: str,
        profile_aggregate: Dict[str, Any],
        selected_repo_summaries: List[Dict[str, Any]],
        model_tier: str = "default",
        purpose: str = "summarize_query_from_summaries",
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate recruiter-facing answer from aggregate + selected repo summaries."""
        if not query.strip():
            return {"response": "Please provide a query.", "repositories_used": [], "query": query}
        if not self.client:
            return {"error": "ai_service_not_configured"}

        system_message = self._build_query_from_summaries_system(query)
        payload = {
            "query": query,
            "profile_aggregate": profile_aggregate,
            "selected_repo_summaries": selected_repo_summaries,
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
            return {
                "response": result,
                "repositories_used": [
                    {"name": item.get("repo_name")} for item in selected_repo_summaries if item.get("repo_name")
                ],
                "total_repositories": len(selected_repo_summaries),
                "query": query,
            }
        except Exception as e:
            logger.error("Error generating query response: %s", str(e))
            return {
                "response": {"error": str(e)},
                "repositories_used": [],
                "total_repositories": len(selected_repo_summaries),
                "query": query,
            }

    def expand_micro_summary_to_html(
        self,
        *,
        repo_name: str,
        micro_summary: Dict[str, Any],
        repo_metadata: Dict[str, Any],
        model_tier: str = "default",
        purpose: str = "expand_micro_summary_to_html",
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

        system_message = self._build_expand_micro_summary_system(repo_name)
        payload = {
            "repo_name": repo_name,
            "micro_summary": micro_summary,
            "repo_metadata": repo_metadata,
        }
        request_id = f"expand-micro-{int(time.time())}"
        
        try:
            result = self.call_ai_api(
                system_message,
                json.dumps(payload, ensure_ascii=False),
                request_id,
                model_tier=model_tier,
                max_completion_tokens=3000,
                purpose=purpose,
                job_id=job_id,
                repo_name=repo_name,
            )
            # Result should be HTML; validate basic structure
            if not result or result.startswith("{"):
                return "<p>Failed to generate expanded summary.</p>"
            return result
        except Exception as e:
            logger.error("Error expanding micro-summary for %s: %s", repo_name, str(e))
            return f"<p>Error generating expanded summary: {str(e)}</p>"

    # ---------------------------------------------------------------------------
    # Helper methods to build system prompts for different tasks
    # ---------------------------------------------------------------------------

    def _build_profile_summary_system(self, username: Optional[str] = None) -> str:
        """Build a system prompt that returns HTML-only profile summary output."""
        candidate = username or "the candidate"
        return (
            "You are an assistant that summarizes GitHub candidate profiles into clean HTML for recruiters.\n"
            f"Summarize the profile for {candidate} using the provided JSON payload.\n\n"
            "The payload includes:\n"
            "- GitHub profile metadata (bio, location, followers, etc.)\n"
            "- Repository statistics (stars, forks, languages, topics)\n"
            "- Recent repository file contents (READMEs and config files) for quality analysis\n\n"
            "Output rules:\n"
            "- Return ONLY valid HTML (no Markdown, no code fences).\n"
            "- Do not include <html>, <head>, or <body> tags.\n"
            "- Use semantic tags: <h2>, <h3>, <p>, <ul>, <li>, <strong>, <code>, <pre>, <a>.\n"
            "- Keep it concise (10-16 short bullets/paragraphs total).\n"
            "- If a detail is not present in the data, omit it.\n"
            "- Do not speculate about employment status or seniority.\n\n"
            "Content goals:\n"
            "- Provide a concise overview of the candidate.\n"
            "- Analyze code quality, documentation style, and technical depth from file contents.\n"
            "- Identify skills, tools, and technologies used (infer from config files like package.json, requirements.txt, Dockerfile, etc.).\n"
            "- Highlight strengths based on repositories, languages, topics, and recent activity.\n"
            "- Assess engineering practices (testing, CI/CD, documentation, architecture).\n"
            "- Suggest what kind of systems or teams they would fit well in.\n\n"
            "Structure:\n"
            "- <h2>Overview</h2> then 1 short paragraph\n"
            "- <h3>Technical Skills & Tools</h3> with a bullet list (inferred from config files)\n"
            "- <h3>Code Quality & Practices</h3> with a bullet list\n"
            "- <h3>Strengths</h3> with a bullet list\n"
            "- <h3>Best Fit</h3> with a bullet list (team types, project types)\n"
            "- <h3>Recent Activity</h3> with 1 short paragraph about most recent work\n"
            "- <h3>Notable Projects</h3> for top repos (optional)\n"
        )

    def _build_readme_summary_system(self, repo_name: Optional[str] = None) -> str:
        """Build a system prompt that returns HTML-only README summary output."""
        repo_label = repo_name or "the repository"
        return (
            "You are an assistant that summarizes GitHub README files into clean HTML.\n"
            f"Summarize the README for {repo_label}.\n\n"
            "Output rules:\n"
            "- Return ONLY valid HTML (no Markdown, no code fences).\n"
            "- Do not include <html>, <head>, or <body> tags.\n"
            "- Use semantic tags: <h2>, <h3>, <p>, <ul>, <li>, <code>, <pre>, <a>.\n"
            "- Keep it concise (6-12 short bullets/paragraphs total).\n"
            "- If setup/run steps exist, include them in a short list.\n"
            "- Avoid inline styles; rely on the host application's CSS.\n"
            "- If a detail is not in the README, omit it.\n\n"
            "Structure:\n"
            "- <h2>Overview</h2> then 1-2 paragraphs\n"
            "- <h3>Key Features</h3> with a bullet list\n"
            "- <h3>Tech Stack</h3> with a bullet list (if present)\n"
            "- <h3>How to Run</h3> with steps (if present)\n"
            "- <h3>Notes</h3> for caveats or missing pieces (optional)\n"
        )

    def _build_query_bundle_system(self, query: str, bundle_context: Dict[str, Any]) -> str:
        """Build system prompt for query with bundle context.
        
        Args:
            query: User query string
            bundle_context: Bundle context with multiple repo summaries
            
        Returns:
            System prompt string
        """
        repos = bundle_context.get("repositories", [])
        strategy = bundle_context.get("selection_strategy", "recent")
        
        # Build repo list
        repo_list = []
        for repo in repos:
            name = repo["name"]
            lang = repo.get("primary_language", "Unknown")
            stars = repo.get("stars", 0)
            repo_list.append(f"- **{name}** ({lang}, {stars} stars)")
        
        repos_intro = "\n".join(repo_list)
        
        # Build detailed repo contexts
        context_parts = []
        for i, repo in enumerate(repos, 1):
            context = [f"### Repository {i}: {repo['name']}"]
            
            if repo.get("description"):
                context.append(f"**Description:** {repo['description']}")
            
            if repo.get("primary_language"):
                context.append(f"**Primary Language:** {repo['primary_language']}")
            
            if repo.get("stars") or repo.get("forks"):
                context.append(f"**Stats:** {repo.get('stars', 0)} stars, {repo.get('forks', 0)} forks")
            
            if repo.get("readme_summary"):
                context.append(f"\n**README Summary:**\n{repo['readme_summary']}")
            
            if repo.get("config_summaries"):
                context.append("\n**Configuration Files:**")
                for config in repo['config_summaries']:
                    context.append(f"\n*{config['filename']}:*\n```\n{config['content']}\n```")
            
            context_parts.append("\n".join(context))
        
        context_str = "\n\n".join(context_parts)
        
        # Strategy description
        strategy_desc = {
            "recent": "most recently updated (showing current work)",
            "random": "randomly selected (for diversity)",
            "top_starred": "most starred (most popular)"
        }.get(strategy, strategy)
        
        candidate = self.username or "the candidate"
        
        system_template = (
            f"You are an AI assistant helping recruiters understand {candidate}'s GitHub portfolio.\n\n"
            f"**Context Selection:** The following {len(repos)} repositories were selected as {strategy_desc}:\n\n"
            f"{repos_intro}\n\n"
            "**Your Task:**\n"
            f"Answer the query: '{query}'\n\n"
            "**How to Respond:**\n"
            "- Use ONLY the information provided in the repository contexts below\n"
            "- Reference specific repositories by name when citing evidence\n"
            "- If a technology/skill is mentioned, identify which repos demonstrate it\n"
            "- Draw connections between projects when relevant\n"
            "- Be specific about technical implementations\n"
            "- If information is missing, state it briefly and work with what's available\n\n"
            "**Formatting:**\n"
            "- Use Markdown with headings, bullets, and code blocks as appropriate\n"
            "- Keep response concise but technically rich\n"
            "- Start with a direct answer, then provide supporting details\n\n"
            "**REPOSITORY CONTEXTS:**\n\n"
            f"{context_str}\n"
        )
        
        return system_template

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
            "overview, key_features, tech_stack_assessment, architecture_patterns, skill_signals\n"
            "Constraints:\n"
            "- overview: string (2-3 sentences max)\n"
            "- key_features: array of short strings (5 max)\n"
            "- tech_stack_assessment: string (2-3 sentences max)\n"
            "- architecture_patterns: array of short strings (max 3 items)\n"
            "- skill_signals: array of objects {skill, confidence, style} where confidence and style are 0-1 floats (max 6 items)\n"
            "Keep response concise; must be under 2000 tokens.\n"
        )

    def _build_profile_aggregation_system(self, username: Optional[str] = None) -> str:
        """Build system prompt for JSON-only profile aggregation from micro-summaries."""
        candidate = username or self.username or "candidate"
        return (
            f"You aggregate repository micro-summaries for {candidate} into JSON only.\n"
            "Do not return markdown, prose, or code fences.\n"
            "Output must be valid JSON object with keys: overview, skills, domains, experience_signals.\n"
            "skills must be array of {skill, score, evidence}.\n"
            "domains must be array of {domain, evidence}.\n"
            "experience_signals must include architecture_patterns and collaboration indicators when present.\n"
            "Use only provided micro-summary evidence; do not speculate.\n"
        )

    def _build_profile_formatter_system(self, username: Optional[str] = None) -> str:
        """Build system prompt for HTML-only formatting from aggregate JSON."""
        candidate = username or self.username or "candidate"
        return (
            f"You format an aggregated profile JSON for {candidate} into HTML only.\n"
            "Return only semantic HTML using h2/h3/p/ul/li/strong.\n"
            "Sections required: Overview, Technical Skills & Tools, Code Quality & Practices, Strengths, Best Fit, Recent Activity.\n"
            "Do not add details not present in JSON.\n"
        )

    def _build_query_from_summaries_system(self, query: str) -> str:
        """Build system prompt for query responses using aggregate + selected summaries."""
        return (
            "You answer recruiter queries using only provided profile aggregate and repository micro-summaries.\n"
            f"Primary query: {query}\n"
            "Requirements:\n"
            "- Start with direct answer.\n"
            "- Cite repository names for evidence.\n"
            "- Mention uncertainty if evidence is missing.\n"
            "- Return concise markdown suitable for recruiter UI.\n"
        )

    def _build_expand_micro_summary_system(self, repo_name: Optional[str] = None) -> str:
        """Build system prompt for expanding micro-summary into detailed HTML.
        
        Takes a concise micro-summary and enriches it with deeper analysis
        and recruiting insights for single-repo view.
        """
        repo_label = repo_name or "this repository"
        return (
            f"You are expanding a concise micro-summary of {repo_label} into a detailed, recruiting-focused HTML analysis.\n\n"
            "Input: A structured micro-summary with keys: overview, key_features, tech_stack, architecture_patterns, skill_signals.\n\n"
            "Your task:\n"
            "1. Expand the overview with deeper context about the project's purpose and scope.\n"
            "2. Elaborate on key features with technical depth and design decisions.\n"
            "3. Analyze the tech stack for depth, breadth, and technology choices.\n"
            "4. Provide recruiting insights about the architecture patterns and engineering practices.\n"
            "5. Translate skill signals into specific developer competencies and expertise areas.\n"
            "6. Suggest what team roles or project types this developer would be well-suited for.\n\n"
            "Output rules:\n"
            "- Return ONLY valid HTML (no Markdown, no code fences).\n"
            "- Do not include <html>, <head>, or <body> tags.\n"
            "- Use semantic tags: <h2>, <h3>, <p>, <ul>, <li>, <strong>, <code>, <a>.\n"
            "- Keep it professional and concise (2000-3000 tokens max).\n"
            "- Avoid speculation; use only information from the input micro-summary.\n\n"
            "Structure:\n"
            "- <h2>Overview</h2> with 1-2 paragraphs expanding on the project scope\n"
            "- <h3>Technical Architecture</h3> with bullet analysis of design patterns and decisions\n"
            "- <h3>Technology Stack</h3> with categorized list (languages, frameworks, tools)\n"
            "- <h3>Developer Competencies</h3> with bullet list of inferred skills and expertise\n"
            "- <h3>Engineering Practices</h3> with bullet observations about code organization, patterns\n"
            "- <h3>Best Fit Roles</h3> with bullet list of team types and project types this developer excels in\n"
        )
