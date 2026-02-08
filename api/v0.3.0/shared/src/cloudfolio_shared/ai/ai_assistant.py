import json
import os
import logging
import time
from typing import Dict, Any, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)

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

    def __init__(self, username: Optional[str] = None):
        """Initialize the AI Assistant with OpenAI API credentials."""
        logger.info("Initializing AI Assistant for user: %s", username or "<unknown>")
        self.username = username
        
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


    def process_query_with_bundle(self, query: str, bundle_context: Dict[str, Any], model_tier: str = "default") -> Dict[str, Any]:
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
            ai_response = self.call_ai_api(system_message, query, request_id, model_tier=model_tier)
            
            if not ai_response or "error" in ai_response.lower():
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




    def call_ai_api(self, system_message: str, query: str, request_id: str, model_tier: str = "default") -> str:
        """
        Call OpenAI API with the prepared messages using specified model tier.
        
        Args:
            system_message: System prompt
            query: User query
            request_id: Request ID for logging
            model_tier: Model tier to use (default=gpt-5-nano, balanced=gpt-4o-mini)
            
        Returns:
            AI response string
        """
        if not self.client:
            return "I'm sorry, but the AI service is not configured. Please check OPENAI_API_KEY."
        
        model_name = self._get_model_name(model_tier)
        
        try:
            logger.info("Request ID: %s - Calling OpenAI API (model: %s)", request_id, model_name)
            response = self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": query}
                ],
                max_tokens=2000,
                temperature=0.7,
                stream=False
            )
            ai_response = response.choices[0].message.content
            logger.info("Request ID: %s - Received AI response (%s chars) from %s", request_id, len(ai_response), model_name)
            return ai_response
        except Exception as e:
            logger.error("Request ID: %s - OpenAI API error (%s): %s", request_id, model_name, str(e))
            return f"I encountered an error while processing your query with the AI service: {str(e)}"


    def summarize_readme_html(self, readme_text: str, repo_name: Optional[str] = None, model_tier: str = "default") -> str:
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
        return self.call_ai_api(system_message, query, request_id, model_tier=model_tier)

    def summarize_profile_html(self, profile_payload: Dict[str, Any], username: Optional[str] = None, model_tier: str = "default") -> str:
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
        return self.call_ai_api(system_message, query, request_id, model_tier=model_tier)

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

