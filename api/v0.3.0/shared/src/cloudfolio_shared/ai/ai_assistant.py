import json
import os
import logging
import time
from typing import List, Dict, Any, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)

class AIAssistant:
    """
    Builds rich context from repository data and generates AI responses using Groq.
    Optimized for llama-3.1-8b-instant with 131k context window.
    """
    # Increased limit for larger context window
    MAX_TOKENS = 32000  # Safe limit for llama-3.1-8b-instant (out of 131k)

    def __init__(self, username: Optional[str] = None):
        """Initialize the AI Assistant with API credentials."""
        logger.info("Initializing AI Assistant for user: %s", username or "<unknown>")
        self.username = username
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.openai_client = self._initialize_openai_client()

    def _initialize_openai_client(self) -> Optional[OpenAI]:
        """Initialize OpenAI client for Groq API."""
        if not self.groq_api_key:
            logger.warning("Groq API key not configured - AI processing disabled")
            return None
        return OpenAI(
            api_key=self.groq_api_key,
            base_url="https://api.groq.com/openai/v1"
        )

    def process_scored_repositories(self, query: str, scored_repos: List[Dict[str, Any]], max_repos: int = 3) -> Dict[str, Any]:
        """
        Process pre-scored repositories to generate AI responses.
        
        Args:
            query: User's query string
            scored_repos: List of repositories with scores already calculated
            max_repos: Maximum number of repositories to include in context
            
        Returns:
            Dictionary with AI response and metadata
        """
        try:
            logger.info("Processing AI response for query '%s'", query[:100])
            if not scored_repos:
                target = self.username or "this candidate"
                return {
                    "response": f"No repositories found for {target}.",
                    "repositories_used": [],
                    "total_repositories": 0,
                    "query": query
                }

            # Get top repositories
            top_repos = scored_repos[:max_repos]
            
            # Generate AI system message with context
            system_message = self.build_rules_context(top_repos, query=query)

            # Get LLM response
            if not self.groq_api_key:
                logger.warning("Groq API key not configured - AI processing disabled")
                repositories_used = [
                    {"name": repo.get("name", "Unknown"), "relevance_score": repo.get("total_relevance_score", 0)}
                    for repo in top_repos
                ]
                return {
                    "response": "AI processing is disabled: GROQ_API_KEY not configured.",
                    "repositories_used": repositories_used,
                    "total_repositories": len(scored_repos),
                    "query": query
                }
                
            # Call AI with context
            request_id = f"req-{int(time.time())}"
            ai_response = self.call_groq_api(system_message, query, request_id)
            
            # Build response with metadata
            repositories_used = [
                {"name": repo.get("name", "Unknown"), "relevance_score": repo.get("total_relevance_score", 0)}
                for repo in top_repos
            ]
            
            return {
                "response": ai_response,
                "repositories_used": repositories_used,
                "total_repositories": len(scored_repos),
                "query": query
            }
        except Exception as e:
            logger.error("Error during AI processing: %s", str(e), exc_info=True)
            return {
                "response": f"Error processing query: {str(e)}",
                "repositories_used": [],
                "total_repositories": len(scored_repos) if scored_repos else 0,
                "query": query
            }




    def build_rules_context(self, tiered_context: Dict[str, Any], query: str, options: Optional[Dict[str, Any]] = None) -> str:
        """
        Build the full system prompt, encapsulating:
        - how to respond
        - what to respond
        - formatting to use
        - contexts to use (built from the provided tiered_context)
        """
        options = options or {}
        include_secondary = options.get("include_secondary", False)
        include_tertiary = options.get("include_tertiary", False)

        # Get repository names and scores for all repositories in the tiered context
        repo_names_with_scores = []
        for key in ["primary_repo", "secondary_repo", "tertiary_repo"]:
            repo = tiered_context.get(key)
            if repo:
                repo_name = repo.get("name", "Unknown")
                score_metadata = repo.get("score_metadata", {})
                total_score = score_metadata.get("total_relevance_score", 0)
                repo_names_with_scores.append(f"{repo_name} (relevance: {total_score:.2f})")

        # Context assembly
        context_parts: List[str] = []
        for label in ["primary_repo", "secondary_repo", "tertiary_repo"]:
            if label == "secondary_repo" and not include_secondary:
                continue
            if label == "tertiary_repo" and not include_tertiary:
                continue
            repo = tiered_context.get(label)
            if repo:
                context_parts.append(self._repo_section(repo, label.replace("_repo", "")))
            
        context_str = "\n\n".join(context_parts)

        # Build a list of all repositories for the introduction
        repos_list = ", ".join(repo_names_with_scores)
        repositories_intro = f"The following repositories were found to be most relevant to the query '{query}':\n{repos_list}\n\n"

        # Rules and formatting guidance
        how_to_respond = (
            "When answering:\n"
            "- Begin by mentioning the repositories used to answer the query.\n"
            "- Highlight architecture patterns, components, and technical implementations when relevant.\n"
            "- Draw connections between different projects and technologies.\n"
            "- Use README content to understand project goals and features.\n"
            "- Use skills indexes and manifests to identify competencies.\n"
            "- Organize your response with clear sections and specific examples.\n"
            "- Be specific about technical implementations and challenges solved.\n"
        )

        formatting = (
            "Formatting:\n"
            "- Use Markdown with headings, bullet points, and short paragraphs.\n"
            "- Use code blocks for snippets or configuration when helpful.\n"
            "- Keep the response concise but technically rich.\n"
        )

        # Format what_to_respond separately
        repo_names_only = ", ".join([r.split(" (")[0] for r in repo_names_with_scores])
        what_to_respond = (
            "What to respond:\n"
            f"- Start by mentioning that your answer is based on the repositories: {repo_names_only}\n"
            "- Provide an accurate answer grounded in the supplied repository context.\n"
            "- If asked about a technology or skill, cite the most relevant project(s) and details.\n"
            "- If information is missing, state limitations briefly and proceed with best-available context.\n"
        )

        candidate = self.username or "the candidate"

        # Use a single format operation with all parameters
        system_template = (
            "You are an AI assistant that helps recruiters understand {candidate}'s GitHub portfolio projects.\n"
            "These projects are retrieved dynamically for the requested username and have been pre-processed to surface repositories "
            "most relevant to the user's query.\n\n"
            "{repositories_intro}"
            "Detailed context is provided for the primary repository, but be aware of all relevant repositories in your response.\n"
            "Provide a response to '{query}' using the following information.\n\n"
            "PORTFOLIO REPOSITORY ANALYSIS:\n"
            "{context}\n\n"
            "{how_to_respond}\n"
            "{formatting}\n"
            "{what_to_respond}"
        ).format(
            candidate=candidate,
            repositories_intro=repositories_intro,
            query=query,
            context=context_str,
            how_to_respond=how_to_respond,
            formatting=formatting,
            what_to_respond=what_to_respond
        )
        
        return system_template

    def call_groq_api(self, system_message: str, query: str, request_id: str) -> str:
        """
        Call the Groq API with the prepared messages.
        """
        if not self.openai_client:
            return "I'm sorry, but the AI service is not configured. Please check the Groq API key."
        try:
            logger.info("Request ID: %s - Calling Groq API", request_id)
            response = self.openai_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": query}
                ],
                max_tokens=1500,  # Increased output tokens
                temperature=0.7,
                stream=False
            )
            ai_response = response.choices[0].message.content
            logger.info("Request ID: %s - Received AI response (%s chars)", request_id, len(ai_response))
            return ai_response
        except Exception as e:
            logger.error("Request ID: %s - Groq API error: %s", request_id, str(e))
            return f"I encountered an error while processing your query with the AI service: {str(e)}"


    def summarize_readme_html(self, readme_text: str, repo_name: Optional[str] = None) -> str:
        """Summarize a README into HTML formatted for the project detail view."""
        if not readme_text:
            return "<p>No README content available.</p>"
        if not self.openai_client:
            return "<p>AI service not configured. Please check the Groq API key.</p>"

        system_message = self._build_readme_summary_system(repo_name)
        query = (
            f"Repository: {repo_name or 'Unknown'}\n\n"
            "README:\n"
            f"{readme_text}"
        )
        request_id = f"readme-{int(time.time())}"
        return self.call_groq_api(system_message, query, request_id)

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

