import logging
import datetime
from typing import Dict, List, Any
from ai.type_analyzer import FileTypeAnalyzer
from config.fine_tuning import SemanticModel
import numpy as np
from data_filter import extract_language_terms, technical_terms_structured

logger = logging.getLogger('portfolio.api')

class RepoScoringService:
    """
    Service for scoring repositories against queries with different algorithms.
    Decoupled from AI processing to allow reuse in different contexts.
    """
    
    def __init__(self, username: str = None):
        """Initialize the repository scoring service with required components."""
        self.username = username
        self.file_type_analyzer = FileTypeAnalyzer()
        self.semantic_model = SemanticModel()
        
    def score_repositories(self, query: str, repo_bundles: List[Dict]) -> List[Dict]:
        """
        Score repositories based on their relevance to the user query.
        
        Args:
            query: The user query string
            repo_bundles: List of repository data bundles from cache or orchestration
            
        Returns:
            List of repositories with scores added
        """
        if not repo_bundles:
            logger.warning("No repositories to score")
            return []
        logger.info(f"Scoring {len(repo_bundles)} repositories against query: {query[:50]}...")

        # Filter repositories with documentation for model usage
        documented_repos = [repo for repo in repo_bundles if repo.get("has_documentation", False)]

        # Load model without training (training happens in background activity)
        self.semantic_model.ensure_model_ready(documented_repos, train_if_missing=False)

        scored_repos = []
        for repo in repo_bundles:
            try:
                # Calculate scores
                scored_repo = repo.copy()  # Don't modify original data
                scores = self.calculate_repository_score(scored_repo, query)
                
                # Add scores to repository
                scored_repo.update(scores)
                scored_repos.append(scored_repo)
                
            except Exception as e:
                repo_name = repo.get("name", "Unknown")
                logger.error(f"Error scoring repository '{repo_name}': {str(e)}", exc_info=True)
                # Add to list anyway with zero scores to maintain repo count
                repo_copy = repo.copy()
                repo_copy.update({
                    "context_score": 0.0,
                    "language_score": 0.0,
                    "type_score": 0.0,
                    "total_relevance_score": 0.0,
                    "error": str(e)
                })
                scored_repos.append(repo_copy)
                
        # Sort by total relevance score
        scored_repos.sort(key=lambda r: r.get("total_relevance_score", 0), reverse=True)
        
        return scored_repos
        
    def calculate_repository_score(self, repo_bundle: Dict[str, Any], query: str) -> Dict[str, Any]:
        """
        Calculate the relevance scores for a single repository.
        
        Args:
            repo_bundle: Repository bundle with metadata and content
            query: The user query to score against
            
        Returns:
            Dictionary with all score components
        """
        repo_context = repo_bundle.get("repoContext", {})
        repo_languages = repo_bundle.get("languages", {})
        file_types = repo_bundle.get("file_types", {})
        categorized = repo_bundle.get("categorized_types", {})
        repo_name = repo_bundle.get("name", "Unknown")

        # Safety checks
        if not isinstance(repo_context, dict):
            repo_context = {}
        if not isinstance(repo_languages, dict):
            repo_languages = {}
        if not isinstance(file_types, dict):
            file_types = {}
        if not isinstance(categorized, dict):
            categorized = {}

        # Calculate individual scores
        context_score = float(self.score_context_similarity(query, repo_bundle))
        language_score = float(self.score_language_matches(query, repo_languages))
        type_score = float(self.file_type_analyzer.calculate_type_score(categorized))
        
        # Aggregate total score
        if language_score > 0:
            total_score = float((context_score * 0.6) + (language_score * 0.25) + (type_score * 0.15))
        else:
            total_score = float((context_score * 0.85) + (type_score * 0.15))

        # Return score components and metadata
        return {
            "context_score": context_score,
            "language_score": language_score,
            "type_score": type_score,
            "total_relevance_score": total_score,
            "scoring_timestamp": datetime.datetime.now().isoformat()
        }
        
    def score_context_similarity(self, query: str, repo_bundle: Dict) -> float:
        """
        Scores the similarity between the query and the repository context using semantic embeddings.
        """
        if not repo_bundle.get("has_documentation", False):
            logger.info(f"Skipping context scoring for {repo_bundle.get('name', 'Unknown')} due to lack of documentation.")
            return 0.0
        
        context_str = self.flatten_repo_context_to_natural_language(repo_bundle)
        if not context_str or context_str.strip() == "" or "None" in context_str:
            logger.info(f"Skipping context scoring for {repo_bundle.get('name', 'Unknown')} due to empty or meaningless context string.")
            return 0.0

        # Encode with whitening + L2 normalization for better spread
        q_emb = self.semantic_model.encode([query], apply_whitening=True, normalize=True)
        c_emb = self.semantic_model.encode([context_str], apply_whitening=True, normalize=True)

        # Dot product equals cosine for normalized vectors
        similarity = float(np.dot(q_emb[0], c_emb[0]))

        return similarity

    def score_language_matches(self, query: str, repo_languages: Dict) -> float:
        """
        Returns a normalized score [0, 1] based on the proportion of query language matches
        to the number of query language terms. Ignores size.
        """
        query_terms = [t.lower() for t in extract_language_terms(query)]
        if not query_terms:
            return 0.0
        repo_langs = [lang.lower() for lang in repo_languages.keys()]
        matches = set(query_terms) & set(repo_langs)
        score = len(matches) / len(query_terms)
        logger.info(f"Language match score for query '{query}': {score} (matches: {matches})")
        return min(score, 1.0)

    def score_language_size(self, query: str, repo_languages: Dict) -> float:
        """
        Returns a normalized score [0, 1] based on the total size of matching languages
        divided by the total size of all languages in the repo.
        """
        query_terms = [t.lower() for t in extract_language_terms(query)]
        if not query_terms or not repo_languages:
            return 0.0
        total_size = sum(repo_languages.values())
        if total_size == 0:
            return 0.0
        match_size = sum(size for lang, size in repo_languages.items() if lang.lower() in query_terms)
        logger.info(f"Language size score for query '{query}': {match_size}/{total_size} = {match_size / total_size if total_size > 0 else 0.0}")
        score = match_size / total_size
        return min(score, 1.0)
    
    
    def flatten_repo_context_to_natural_language(self, repo_bundle: Dict) -> str:
        """
        Converts the repository context bundle (including .repo-context.json, README.md,
        SKILLS-INDEX.md, ARCHITECTURE.md) into a natural-language-like paragraph
        for sentence-transformer embedding and fine-tuning.
    
        Note: This method is similar to SemanticModel._flatten_repo_bundle_for_training
        but maintained separately to allow flexible field inclusion. Keep the core
        fields in sync with the corresponding method in fine_tuning.py.

        Args:
            repo_bundle (Dict): Repository context bundle.

        Returns:
            str: Flattened natural-language representation of the repository context.
        """
        lines = []

        # Extract structured fields from repo-context.json
        identity = repo_bundle.get("repoContext", {}).get("project_identity", {})
        tech_stack = repo_bundle.get("repoContext", {}).get("tech_stack", {})
        # skills = repo_bundle.get("repoContext", {}).get("skill_manifest", {})
        # outcomes = repo_bundle.get("repoContext", {}).get("outcomes", {})
        # metadata = repo_bundle.get("repoContext", {}).get("metadata", {})
        # assessment = repo_bundle.get("repoContext", {}).get("assessment", {})

        # Build natural language representation
        if identity.get('name'):
            lines.append(f"Project Name: {identity['name']}.")
        if identity.get('description'):
            lines.append(f"Description: {identity['description']}.")
        if identity.get('type'):
            lines.append(f"Type: {identity['type']}.")
        if identity.get('scope'):
            lines.append(f"Scope: {identity['scope']}.")

        if tech_stack.get("primary"):
            lines.append(f"Primary technologies include {', '.join(tech_stack['primary'])}.")
        if tech_stack.get("secondary"):
            lines.append(f"Secondary tools include {', '.join(tech_stack['secondary'])}.")
        if tech_stack.get("key_libraries"):
            lines.append(f"Key libraries: {', '.join(tech_stack['key_libraries'])}.")
        if tech_stack.get("development_tools"):
            lines.append(f"Development tools used: {', '.join(tech_stack['development_tools'])}.")

        # if skills.get("technical"):
        #     lines.append(f"Technical skills demonstrated include {', '.join(skills['technical'])}.")
        # if skills.get("domain"):
        #     lines.append(f"Domain-specific knowledge areas: {', '.join(skills['domain'])}.")
        # if skills.get("competency_level"):
        #     lines.append(f"Competency level: {skills['competency_level']}.")

        # if outcomes.get("deliverables"):
        #     lines.append(f"Deliverables include {', '.join(outcomes['deliverables'])}.")
        # if outcomes.get("skills_acquired"):
        #     lines.append(f"Skills acquired: {', '.join(outcomes['skills_acquired'])}.")
        # if outcomes.get("primary"):
        #     lines.append(f"Primary outcomes: {', '.join(outcomes['primary'])}.")

        # if assessment.get("difficulty"):
        #     lines.append(f"Difficulty level: {assessment['difficulty']}.")
        # if assessment.get("evaluation_criteria"):
        #     lines.append(f"Evaluation criteria: {', '.join(assessment['evaluation_criteria'])}.")

        # if metadata.get("tags"):
        #     lines.append(f"Tags: {', '.join(metadata['tags'])}.")
        # if metadata.get("maintainer"):
        #     lines.append(f"Maintainer: {metadata['maintainer']}.")
        # if metadata.get("license"):
        #     lines.append(f"License: {metadata['license']}.")

        # # Add README, SKILLS-INDEX, and ARCHITECTURE content
        # for key in ["readme", "skills_index", "architecture"]:
        #     content = repo_bundle.get(key)
        #     if content:
        #         lines.append(f"{key.capitalize()}: {content.strip()}")

        flattened_context = "\n".join(lines)
        return flattened_context