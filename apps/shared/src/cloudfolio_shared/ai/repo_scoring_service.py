import datetime
import logging
import re
from typing import Dict, List, Any, Set

import numpy as np

from .data_filter import extract_language_terms, technical_terms_structured
from .fine_tuning import SemanticModel
from .type_analyzer import FileTypeAnalyzer

logger = logging.getLogger(__name__)

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
        self.technical_terms = technical_terms_structured
        
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
        repo_languages = repo_bundle.get("languages", {})
        file_types = repo_bundle.get("file_types", {})
        categorized = repo_bundle.get("categorized_types", {})
        repo_name = repo_bundle.get("name", "Unknown")

        # Safety checks
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
        skill_score = float(self.score_skill_signals(query, repo_bundle))
        
        # Aggregate total score
        if language_score > 0 or skill_score > 0:
            total_score = float(
                (context_score * 0.45)
                + (language_score * 0.20)
                + (type_score * 0.15)
                + (skill_score * 0.20)
            )
        else:
            total_score = float((context_score * 0.85) + (type_score * 0.15))

        # Return score components and metadata
        return {
            "context_score": context_score,
            "language_score": language_score,
            "type_score": type_score,
            "skill_score": skill_score,
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

    def score_skill_signals(self, query: str, repo_bundle: Dict[str, Any]) -> float:
        """Score how well the repository demonstrates advanced skills from the query."""
        skill_tokens = self._extract_skill_tokens(query)
        if not skill_tokens:
            return 0.0

        # Deprecated: repoContext / skills_index / architecture were user-maintained
        # and not sustainable across all users.
        config_files = repo_bundle.get("config_files") if isinstance(repo_bundle.get("config_files"), dict) else {}
        metadata = repo_bundle.get("metadata") if isinstance(repo_bundle.get("metadata"), dict) else {}
        topics = metadata.get("topics")
        topics_text = " ".join(t for t in topics if isinstance(t, str)) if isinstance(topics, list) else ""

        config_text = " ".join(
            str(value) for value in config_files.values() if isinstance(value, str)
        )
        repo_corpus = " ".join(
            filter(None, [repo_bundle.get("readme"), metadata.get("description"), topics_text, config_text])
        ).lower()

        if not repo_corpus:
            return 0.0

        matches = sum(1 for token in skill_tokens if token in repo_corpus)
        complexity_matches = sum(
            1 for indicator in self.technical_terms.get("complexity_indicators", []) if indicator in repo_corpus
        )

        base_score = matches / len(skill_tokens)
        complexity_bonus = min(complexity_matches / 10, 0.3)
        return min(base_score + complexity_bonus, 1.0)

    def _extract_skill_tokens(self, query: str) -> List[str]:
        query_lower = query.lower()
        tokens: Set[str] = set()

        for term in self.technical_terms.get("advanced_skills", []):
            if re.search(rf"\b{re.escape(term)}\b", query_lower):
                tokens.add(term)

        for term in self.technical_terms.get("domain", []):
            if re.search(rf"\b{re.escape(term)}\b", query_lower):
                tokens.add(term)

        return list(tokens)
    
    
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

        metadata = repo_bundle.get("metadata") if isinstance(repo_bundle.get("metadata"), dict) else {}
        repo_name = repo_bundle.get("name") or metadata.get("name")
        description = metadata.get("description") or repo_bundle.get("description")
        topics = metadata.get("topics")
        languages = repo_bundle.get("languages") if isinstance(repo_bundle.get("languages"), dict) else {}
        config_files = repo_bundle.get("config_files") if isinstance(repo_bundle.get("config_files"), dict) else {}

        if repo_name:
            lines.append(f"Project Name: {repo_name}.")
        if isinstance(description, str) and description.strip():
            lines.append(f"Description: {description.strip()}.")
        if isinstance(topics, list) and topics:
            topic_terms = [t for t in topics if isinstance(t, str) and t]
            if topic_terms:
                lines.append(f"Topics: {', '.join(topic_terms)}.")

        if languages:
            lines.append(f"Languages: {', '.join(sorted(languages.keys()))}.")

        if config_files:
            filenames = [name for name in config_files.keys() if isinstance(name, str) and name]
            if filenames:
                lines.append(f"Config files: {', '.join(sorted(filenames))}.")

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

        return "\n".join(lines)