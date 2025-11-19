# Data Processing Model Plan

**Date**: November 19, 2025  
**Status**: Planning Phase  
**Priority**: Core functionality for query-based repository analysis

---

## Executive Summary

Design and implement a data processing pipeline that transforms unstructured GitHub repository data into queryable, semantically searchable information to answer recruiter questions about developer skills and project portfolios.

### Problem Statement (from plan-main.prompt.md)

**Core Problem**: Data is unstructured. Various file types, file contents, directory depths and counts.

**Goal**: Process user query against username's repositories and provide the best fitting response.

**Sample Query**: "Does username have cloud skills" → Check for cloud-specific SDKs, implementations, file types, configurations, resources, match to repositories, and provide skills + repositories that demonstrate these skills and depth.

---

## Current Implementation Analysis

### What Works Well ✅

From `function_app.py` and `apps/shared/ai/`:

1. **Structured Extraction** (fetch_repo_context_bundle_activity):
   ```python
   {
       "name": "portfolio",
       "metadata": {...},
       "repoContext": {  # Custom structured metadata from .repo-context.json
           "project_identity": {...},
           "tech_stack": {"primary": [...], "secondary": [...]},
           "skills": [...]
       },
       "categorized_types": {  # From type_analyzer.py
           "code": ["*.ts", "*.py"],
           "config": ["*.json", "*.yaml"],
           "infrastructure": ["Dockerfile", "*.bicep"]
       },
       "readme": "...",
       "skills_index": "...",
       "fingerprint": "sha256-..."
   }
   ```

2. **File Type Classification** (type_analyzer.py):
   - Categorizes files into: code, config, documentation, infrastructure, data, assets
   - Maps file extensions to technology categories
   - Identifies cloud-specific patterns (Dockerfile, *.bicep, terraform)

3. **Semantic Model Training** (fine_tuning.py):
   - Fine-tunes SentenceTransformer on repository content
   - Generates training pairs from project identity, tech stack, README
   - Enables semantic similarity search

### What's Missing ❌

1. **Query Classification**: No logic to understand query intent
   - "Show Python projects" → language filter
   - "Cloud skills" → semantic search + keyword matching
   - "Web development" → tech stack filter

2. **Repository Scoring**: RepoScoringService exists but not integrated into query flow

3. **Skill Depth Analysis**: No quantification of skill depth
   - Number of projects using technology
   - Lines of code in language
   - Complexity indicators (architecture docs, tests, CI/CD)

4. **Response Generation**: AI assistant not optimized for structured queries

---

## Data Processing Architecture

### 3-Stage Pipeline

```
┌──────────────────────────────────────────────────────────────────┐
│ STAGE 1: Query Understanding & Classification                     │
│                                                                    │
│ Input: "Does username have cloud skills"                          │
│   ↓                                                                │
│ QueryClassifier                                                    │
│   → Intent: "skill_check"                                         │
│   → Entities: ["cloud"]                                           │
│   → Query Type: "semantic_search" + "keyword_filter"              │
│   → Keywords: ["azure", "aws", "docker", "kubernetes", "terraform"]│
└────────────────────────┬───────────────────────────────────────────┘
                         │
                         ↓
┌──────────────────────────────────────────────────────────────────┐
│ STAGE 2: Repository Filtering & Scoring                           │
│                                                                    │
│ Step 1: Keyword Pre-Filter (Fast)                                 │
│   → Filter repos by categorized_types, tech_stack, languages      │
│   → Result: 5/10 repos match "cloud" keywords                     │
│                                                                    │
│ Step 2: Semantic Ranking (Accurate)                               │
│   → Use RepoScoringService with fine-tuned model                  │
│   → Rank by cosine similarity to query embedding                  │
│   → Result: Top 3 repos with scores [0.89, 0.82, 0.76]           │
│                                                                    │
│ Step 3: Skill Depth Calculation                                   │
│   → Analyze matched repos for depth indicators                    │
│   → Score: project_count × complexity_factor                      │
│   → Result: {"aws": 0.85, "docker": 0.92, "kubernetes": 0.65}    │
└────────────────────────┬───────────────────────────────────────────┘
                         │
                         ↓
┌──────────────────────────────────────────────────────────────────┐
│ STAGE 3: Response Generation                                      │
│                                                                    │
│ Input: Ranked repos + skill scores + query context                │
│   ↓                                                                │
│ AIAssistant (Groq LLaMA)                                          │
│   → Generate natural language response                            │
│   → Include: skills found, repos demonstrating skills, depth      │
│   → Format: "Yes, username demonstrates strong cloud skills..."   │
│                                                                    │
│ Output: {                                                          │
│   "response": "natural language answer",                           │
│   "skills": {"aws": 0.85, "docker": 0.92, ...},                  │
│   "repositories_used": [...]                                       │
│ }                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Implementation Details

### Stage 1: Dynamic Keyword Extraction

**Location**: `apps/shared/ai/keyword_extractor.py` (NEW FILE)

```python
from typing import List, Dict, Optional
import yaml
import re
import logging

logger = logging.getLogger('portfolio.api')

class DynamicKeywordExtractor:
    """
    Extracts technology keywords from queries using languages.yml.
    ZERO manual maintenance - GitHub maintains the keyword database.
    """
    
    def __init__(self, linguist_data_path: str = "linguist/languages.yml"):
        """Load languages.yml and build keyword indexes."""
        with open(linguist_data_path, 'r') as f:
            self.languages_data = yaml.safe_load(f)
        
        # Build indexes for fast lookup
        self.language_names = set()
        self.alias_to_language = {}
        self.extension_to_language = {}
        
        for lang_name, lang_data in self.languages_data.items():
            # Language names are keywords (e.g., "Python", "JavaScript")
            self.language_names.add(lang_name.lower())
            
            # Aliases map to canonical names (e.g., "js" → "javascript")
            for alias in lang_data.get('aliases', []):
                self.alias_to_language[alias.lower()] = lang_name.lower()
            
            # Extensions map to languages (e.g., ".py" → "python")
            for ext in lang_data.get('extensions', []):
                self.extension_to_language[ext.lower()] = lang_name.lower()
        
        logger.info(f"Loaded {len(self.language_names)} languages from linguist/languages.yml")
    
    def extract_keywords_from_query(self, query: str) -> List[str]:
        """
        Extract technology keywords from query using languages.yml.
        
        Example:
            Query: "Show me Python and Docker projects"
            Returns: ['python', 'docker']
        """
        query_lower = query.lower()
        matched_keywords = []
        
        # Match language names (e.g., "python", "javascript")
        for lang_name in self.language_names:
            # Use word boundaries to avoid partial matches
            if re.search(rf'\b{re.escape(lang_name)}\b', query_lower):
                matched_keywords.append(lang_name)
        
        # Match aliases (e.g., "js" → "javascript", "py" → "python")
        for alias, lang_name in self.alias_to_language.items():
            if re.search(rf'\b{re.escape(alias)}\b', query_lower):
                matched_keywords.append(lang_name)
        
        return list(set(matched_keywords))
    
    def get_extensions_for_language(self, language: str) -> List[str]:
        """Get all file extensions for a language."""
        for lang_name, lang_data in self.languages_data.items():
            if lang_name.lower() == language.lower():
                return [ext.lower() for ext in lang_data.get('extensions', [])]
        return []
    
    def match_repositories_by_keywords(self, query: str, repos: List[Dict]) -> List[Dict]:
        """
        Fast keyword-based filtering using:
        1. GitHub language statistics
        2. Repository topics (user-tagged)
        3. File extensions
        4. README/description mentions
        """
        keywords = self.extract_keywords_from_query(query)
        
        if not keywords:
            # No keywords found → use all repos (semantic search will rank)
            return repos
        
        matched_repos = []
        for repo in repos:
            # Source 1: GitHub languages (auto-detected by GitHub)
            repo_languages = [lang.lower() for lang in repo.get('languages', {}).keys()]
            
            # Source 2: GitHub topics (user-tagged)
            topics = [t.lower() for t in repo.get('metadata', {}).get('topics', [])]
            
            # Source 3: File extensions
            file_types = repo.get('file_types', {})
            repo_extensions = [ext.lower() for ext in file_types.keys()]
            
            # Source 4: README and description
            readme = repo.get('readme', '').lower()
            description = repo.get('metadata', {}).get('description', '').lower()
            
            # Check if any keyword matches
            for keyword in keywords:
                # Direct language match
                if keyword in repo_languages or keyword in topics:
                    matched_repos.append(repo)
                    break
                
                # Extension match (e.g., ".py" files for Python)
                lang_extensions = self.get_extensions_for_language(keyword)
                if any(ext in repo_extensions for ext in lang_extensions):
                    matched_repos.append(repo)
                    break
                
                # README/description mention
                if keyword in readme or keyword in description:
                    matched_repos.append(repo)
                    break
        
        return matched_repos
```

---

### Stage 2: Repository Scorer (Enhanced)

**Location**: `apps/shared/ai/repo_scoring_service.py` (ENHANCE EXISTING)

```python
class RepoScoringService:
    """Enhanced repository scoring with hybrid search and skill depth analysis."""
    
    def __init__(self, repos_bundle: list):
        self.repos_bundle = repos_bundle
        self.semantic_model = SemanticModel()
        self.semantic_model.ensure_model_ready(repos_bundle, train_if_missing=False)
        self.keyword_extractor = DynamicKeywordExtractor()  # Uses languages.yml
    
    def score_repos_by_query(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Hybrid scoring: keyword filter + semantic ranking.
        
        Args:
            query: Original user query
            top_k: Number of top results to return
            
        Returns:
            List of {repo, score, match_reasons}
        """
        # Stage 1: Keyword pre-filter (fast, reduces search space)
        filtered_repos = self.keyword_extractor.match_repositories_by_keywords(query, self.repos_bundle)
        
        if not filtered_repos:
            # No keyword matches → use all repos for semantic search
            filtered_repos = self.repos_bundle
        
        # Stage 2: Semantic scoring (accurate, ranks by relevance)
        scored_repos = self._semantic_score(filtered_repos, query)
        
        # Step 3: Sort and return top K
        sorted_repos = sorted(scored_repos, key=lambda x: x[1], reverse=True)[:top_k]
        
        return [
            {
                'repo': repo,
                'score': score,
                'match_reasons': reasons
            }
            for repo, score, reasons in sorted_repos
        ]
    
    def _semantic_score(self, repos: List[Dict], query: str) -> List[Tuple[Dict, float, List[str]]]:
        """Score repos using semantic similarity."""
        query_embedding = self.semantic_model.encode(query)
        
        scored = []
        for repo in repos:
            # Combine repo content for embedding
            content = self._create_searchable_content(repo)
            repo_embedding = self.semantic_model.encode(content)
            
            # Calculate cosine similarity
            score = self._cosine_similarity(query_embedding, repo_embedding)
            
            # Identify match reasons
            reasons = self._identify_match_reasons(repo, query)
            
            scored.append((repo, score, reasons))
        
        return scored
    
    def _create_searchable_content(self, repo: Dict) -> str:
        """
        Create rich searchable text from STANDARD repository files.
        NO custom .repo-context.json required.
        """
        parts = []
        
        # Repository metadata (GitHub API)
        metadata = repo.get('metadata', {})
        if metadata.get('description'):
            parts.append(f"Project: {metadata['description']}")
        
        if metadata.get('topics'):
            parts.append(f"Topics: {', '.join(metadata['topics'])}")
        
        # Language statistics (GitHub calculates automatically)
        languages = repo.get('languages', {})
        if languages:
            top_langs = sorted(languages.items(), key=lambda x: x[1], reverse=True)[:5]
            lang_list = [f"{lang} ({int(bytes/1024)}KB)" for lang, bytes in top_langs]
            parts.append(f"Languages: {', '.join(lang_list)}")
        
        # README content (first 2000 chars = rich context)
        readme = repo.get('readme', '')
        if readme:
            # Extract first 2 paragraphs (usually project summary)
            summary = ' '.join(readme.split('\n\n')[:2])
            parts.append(f"Summary: {summary[:2000]}")
        
        return ' '.join(parts)
    
    def _identify_match_reasons(self, repo: Dict, query: str) -> List[str]:
        """Identify why this repo matches the query."""
        reasons = []
        query_lower = query.lower()
        
        # Check languages (GitHub API)
        languages = repo.get('languages', {})
        for lang in languages.keys():
            if lang.lower() in query_lower:
                reasons.append(f'language:{lang}')
        
        # Check topics (user-tagged)
        topics = repo.get('metadata', {}).get('topics', [])
        for topic in topics:
            if topic.lower() in query_lower:
                reasons.append(f'topic:{topic}')
        
        # Check README/description
        readme = repo.get('readme', '').lower()
        description = repo.get('metadata', {}).get('description', '').lower()
        if any(word in readme or word in description for word in query_lower.split()):
            reasons.append('content_match')
        
        return reasons if reasons else ['semantic_similarity']
    
    def calculate_skill_depth(self, matched_repos: List[Dict], skill_keywords: List[str]) -> Dict[str, float]:
        """
        Calculate skill depth scores based on matched repositories.
        
        Score factors:
        - Number of projects (breadth)
        - Lines of code (volume)
        - Complexity indicators (architecture docs, tests, CI/CD)
        - Recency (updated_at)
        
        Returns:
            Dict mapping skill → depth score (0.0-1.0)
        """
        skill_scores = {}
        
        for keyword in skill_keywords:
            projects_count = 0
            total_code_size = 0
            complexity_bonus = 0
            
            for repo in matched_repos:
                # Check if repo uses this skill
                repo_text = self._create_searchable_content(repo).lower()
                if keyword.lower() not in repo_text:
                    continue
                
                projects_count += 1
                
                # Language code size
                languages = repo.get('metadata', {}).get('languages', {})
                for lang, size in languages.items():
                    if lang.lower() == keyword.lower():
                        total_code_size += size
                
                # Complexity indicators
                if repo.get('architecture'):
                    complexity_bonus += 0.2
                if repo.get('has_documentation'):
                    complexity_bonus += 0.1
                if 'ci' in repo.get('categorized_types', {}).get('config', []):
                    complexity_bonus += 0.1
            
            if projects_count > 0:
                # Normalized score (0.0-1.0)
                breadth_score = min(projects_count / 5, 1.0)  # Max at 5 projects
                volume_score = min(total_code_size / 100000, 1.0)  # Max at 100K LOC
                complexity_score = min(complexity_bonus, 0.5)
                
                # Weighted average
                skill_scores[keyword] = (
                    breadth_score * 0.4 +
                    volume_score * 0.3 +
                    complexity_score * 0.3
                )
        
        return skill_scores
    
    @staticmethod
    def _cosine_similarity(vec1, vec2):
        """Calculate cosine similarity between two vectors."""
        import numpy as np
        return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
```

---

### Stage 3: AI Assistant (Enhanced)

**Location**: `apps/shared/ai/ai_assistant.py` (ENHANCE EXISTING)

```python
class AIAssistant:
    """Enhanced AI assistant with structured response generation."""
    
    def query_with_context(self, query: str, scored_repos: List[Dict], 
                          skill_scores: Dict[str, float]) -> Dict:
        """
        Generate response using scored repositories and skill depth analysis.
        
        Args:
            query: Original user query
            scored_repos: Output from RepoScoringService.score_repos_by_query
            skill_scores: Output from RepoScoringService.calculate_skill_depth
            
        Returns:
            {
                'response': str,
                'skills': Dict[str, float],
                'repositories_used': List[Dict],
                'confidence': float
            }
        """
        # Build context for AI
        context = self._build_structured_context(scored_repos, skill_scores)
        
        # Generate prompt
        prompt = self._create_prompt(query, context)
        
        # Call Groq API
        response_text = self._call_groq_api(prompt)
        
        return {
            'response': response_text,
            'skills': skill_scores,
            'repositories_used': [
                {
                    'name': r['repo']['name'],
                    'score': r['score'],
                    'match_reasons': r['match_reasons']
                }
                for r in scored_repos
            ],
            'confidence': self._calculate_confidence(scored_repos)
        }
    
    def _build_structured_context(self, scored_repos: List[Dict], 
                                 skill_scores: Dict[str, float],
                                 query_metadata: Dict) -> str:
        """Build structured context for AI prompt."""
        context_parts = []
        
        # Summary of matches
        context_parts.append(f"Found {len(scored_repos)} relevant repositories.")
        
        # Skill depth summary
        if skill_scores:
            skills_text = ', '.join([
                f"{skill} (depth: {score:.2f})"
                for skill, score in sorted(skill_scores.items(), key=lambda x: x[1], reverse=True)
            ])
            context_parts.append(f"Skills identified: {skills_text}")
        
        # Repository details
        context_parts.append("\nRepository Details:")
        for i, repo_data in enumerate(scored_repos[:3], 1):  # Top 3
            repo = repo_data['repo']
            score = repo_data['score']
            reasons = repo_data['match_reasons']
            
            context_parts.append(f"\n{i}. {repo['name']} (relevance: {score:.2f})")
            context_parts.append(f"   - Technologies: {', '.join(repo.get('repoContext', {}).get('tech_stack', {}).get('primary', []))}")
            context_parts.append(f"   - Languages: {', '.join(repo.get('metadata', {}).get('languages', {}).keys())}")
            context_parts.append(f"   - Match reasons: {', '.join(reasons)}")
            
            if repo.get('readme'):
                readme_snippet = repo['readme'][:200]
                context_parts.append(f"   - Description: {readme_snippet}...")
        
        return '\n'.join(context_parts)
    
    def _create_prompt(self, query: str, context: str, intent: QueryIntent) -> str:
        """Create optimized prompt based on query intent."""
        base_prompt = f"""You are analyzing a developer's GitHub portfolio.

User Question: {query}

Context:
{context}

Instructions:
"""
        
        if intent == QueryIntent.SKILL_CHECK:
            base_prompt += """- Answer whether the developer has the requested skills
- Provide specific evidence from their repositories
- Rate skill depth as beginner/intermediate/advanced
- Be concise but specific"""
        
        elif intent == QueryIntent.PROJECT_LIST:
            base_prompt += """- List the matching projects clearly
- Briefly describe what each project does
- Highlight key technologies used
- Order by relevance"""
        
        elif intent == QueryIntent.TECHNOLOGY_SEARCH:
            base_prompt += """- Identify which projects use the requested technology
- Explain how the technology is used in each project
- Note any advanced usage patterns"""
        
        else:
            base_prompt += """- Provide a comprehensive summary of the developer's work
- Highlight key strengths and areas of expertise
- Mention notable projects"""
        
        base_prompt += "\n\nResponse:"
        return base_prompt
    
    def _calculate_confidence(self, scored_repos: List[Dict]) -> float:
        """Calculate confidence based on match quality."""
        if not scored_repos:
            return 0.0
        
        # Average of top 3 scores
        top_scores = [r['score'] for r in scored_repos[:3]]
        return sum(top_scores) / len(top_scores) if top_scores else 0.0
```

---

## Integration with API Endpoint

**Update**: `portfolio/api/function_app.py` (line ~514)

```python
@app.route(route="ai", methods=["POST"])
def portfolio_query(req: func.HttpRequest) -> func.HttpResponse:
    """
    Enhanced portfolio query with simplified 2-stage processing pipeline.
    Uses languages.yml for keyword extraction (zero manual maintenance).
    """
    logger.info("Received portfolio query request")
    try:
        request_body = req.get_json()
        query = request_body.get('query')
        username = request_body.get('username')
        
        if not query or not username:
            return create_error_response("query and username required", 400)
        
        # Get cached bundle
        bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
        cache_result = cache_manager.get(bundle_cache_key)
        
        if cache_result['status'] != 'valid':
            return create_error_response("Repository data not cached. Call /orchestrator_start first.", 404)
        
        repos_bundle = cache_result['data']
        
        # ============================================================
        # STAGE 1: Repository Scoring (Hybrid: Keyword + Semantic)
        # ============================================================
        from ai.repo_scoring_service import RepoScoringService
        
        scorer = RepoScoringService(repos_bundle)
        scored_repos = scorer.score_repos_by_query(query, top_k=5)
        
        if not scored_repos:
            return create_success_response({
                'response': f"No repositories found matching '{query}' for user {username}.",
                'repositories_used': [],
                'skills': {}
            })
        
        # Calculate skill depth
        keywords = scorer.keyword_extractor.extract_keywords_from_query(query)
        skill_scores = scorer.calculate_skill_depth(
            [r['repo'] for r in scored_repos],
            keywords
        )
        
        logger.info(f"Found {len(scored_repos)} relevant repos, skill scores: {skill_scores}")
        
        # ============================================================
        # STAGE 2: Response Generation
        # ============================================================
        from ai.ai_assistant import AIAssistant
        
        ai_assistant = AIAssistant()
        response = ai_assistant.query_with_context(query, scored_repos, skill_scores)
        
        return create_success_response(response)
        
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}", exc_info=True)
        return create_error_response(f"Query processing failed: {str(e)}", 500)
    """
    Enhanced portfolio query with 3-stage processing pipeline.
    """
    logger.info("Received portfolio query request")
    try:
        request_body = req.get_json()
        query = request_body.get('query')
        username = request_body.get('username')
        
        if not query or not username:
            return create_error_response("query and username required", 400)
        
        # Get cached bundle
        bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
        cache_result = cache_manager.get(bundle_cache_key)
        
        if cache_result['status'] != 'valid':
            return create_error_response("Repository data not cached. Call /orchestrator_start first.", 404)
        
        repos_bundle = cache_result['data']
        
        # ============================================================
        # STAGE 1: Query Classification
        # ============================================================
        from ai.query_classifier import QueryClassifier
        
        classifier = QueryClassifier()
        query_metadata = classifier.classify_query(query)
        
        logger.info(f"Query classified: intent={query_metadata['intent']}, "
                   f"type={query_metadata['query_type']}, "
                   f"entities={query_metadata['entities']}")
        
        # ============================================================
        # STAGE 2: Repository Scoring
        # ============================================================
        from ai.repo_scoring_service import RepoScoringService
        
        scorer = RepoScoringService(repos_bundle)
        scored_repos = scorer.score_repos_by_query(query, query_metadata, top_k=5)
        
        if not scored_repos:
            return create_success_response({
                'response': f"No repositories found matching '{query}' for user {username}.",
                'repositories_used': [],
                'skills': {}
            })
        
        # Calculate skill depth
        skill_scores = scorer.calculate_skill_depth(
            [r['repo'] for r in scored_repos],
            query_metadata['keywords']
        )
        
        logger.info(f"Found {len(scored_repos)} relevant repos, skill scores: {skill_scores}")
        
        # ============================================================
        # STAGE 3: Response Generation
        # ============================================================
        from ai.ai_assistant import AIAssistant
        
        ai_assistant = AIAssistant()
        response = ai_assistant.query_with_context(query, scored_repos, skill_scores, query_metadata)
        
        return create_success_response(response)
        
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}", exc_info=True)
        return create_error_response(f"Query processing failed: {str(e)}", 500)
```

---

## Performance Optimization

### Caching Strategy

1. **Semantic Model Cache**:
   - Key: `model_{bundle_fingerprint}`
   - TTL: None (invalidated by bundle fingerprint change)
   - Saves 60-90s training time per request

2. **Query Result Cache** (Future):
   - Key: `query_result_{username}_{query_hash}`
   - TTL: 1 hour
   - Saves 500ms-2s processing time for repeated queries

### Batch Processing

For bulk queries (e.g., "Compare 10 developers"):
- Process in parallel using ThreadPoolExecutor
- Share semantic model across requests
- Return results as stream

---

## Success Metrics

### Accuracy Targets

| Query Type | Target Precision | Target Recall |
|------------|------------------|---------------|
| Skill Check | >90% | >85% |
| Project List | >95% | >90% |
| Technology Search | >85% | >80% |

### Performance Targets

| Stage | Target Latency | Current Baseline |
|-------|---------------|------------------|
| Keyword Extraction | <10ms | N/A (new) |
| Keyword Pre-Filter | <20ms | N/A (new) |
| Semantic Ranking | <500ms | ~2-5s (current) |
| Response Generation | <2s | ~5s (current) |
| **Total** | **<600ms** | **~10s** |

---

## Testing Plan

### Unit Tests

```python
# tests/test_keyword_extractor.py
def test_keyword_extraction():
    extractor = DynamicKeywordExtractor()
    
    # Test language name extraction
    keywords = extractor.extract_keywords_from_query("Show me Python and JavaScript projects")
    assert 'python' in keywords
    assert 'javascript' in keywords
    
    # Test alias extraction
    keywords = extractor.extract_keywords_from_query("Projects using js and py")
    assert 'javascript' in keywords  # js → javascript
    assert 'python' in keywords      # py → python

def test_repository_matching():
    extractor = DynamicKeywordExtractor()
    repos = load_test_repos()
    
    # Test keyword matching
    matched = extractor.match_repositories_by_keywords("Python projects", repos)
    assert all(
        'python' in [lang.lower() for lang in repo.get('languages', {}).keys()]
        for repo in matched
    )

# tests/test_repo_scoring.py
def test_hybrid_scoring():
    repos = load_test_repos()
    scorer = RepoScoringService(repos)
    results = scorer.score_repos_by_query("Python projects", top_k=3)
    assert len(results) <= 3
    assert all(r['score'] > 0 for r in results)
    assert all('repo' in r and 'match_reasons' in r for r in results)
```

### Integration Tests

```bash
# Test full pipeline
curl -X POST http://localhost:7071/api/ai \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Does yungryce have cloud skills",
    "username": "yungryce"
  }'

# Expected response structure
{
  "response": "Yes, yungryce demonstrates strong cloud skills...",
  "skills": {"azure": 0.89, "docker": 0.92, "kubernetes": 0.67},
  "repositories_used": [
    {"name": "aks-cluster", "score": 0.89, "match_reasons": ["tech:kubernetes"]},
    {"name": "portfolio", "score": 0.82, "match_reasons": ["tech:azure", "tech:docker"]}
  ],
  "confidence": 0.85
}
```

---

## Migration Path

### Phase 1: Dynamic Keyword Extraction (Day 1)

- Create `DynamicKeywordExtractor` class (uses existing languages.yml)
- Build language name, alias, and extension indexes
- Implement `extract_keywords_from_query()` method
- Implement `match_repositories_by_keywords()` method
- **Result**: Zero-maintenance keyword extraction for 650+ languages

### Phase 2: Hybrid Search Integration (Day 2)

- Enhance `RepoScoringService` with `DynamicKeywordExtractor`
- Update `score_repos_by_query()` to use keyword pre-filter + semantic ranking
- Add GitHub Topics matching (already available in metadata)
- Simplify training data (README + metadata only, remove full file contents)
- **Result**: <600ms query latency (vs 2-5s current)

### Phase 3: API Integration (Day 3)

- Update `/api/ai` endpoint to use simplified 2-stage pipeline
- Remove QueryClassifier dependency
- Add performance logging (keyword filter time vs semantic ranking time)
- **Result**: Production-ready endpoint with hybrid search

### Phase 4: Testing & Validation (Day 4)

- Unit tests for `DynamicKeywordExtractor`
- Integration tests with real queries:
  - "Python projects" → keyword filter only
  - "Cloud infrastructure skills" → hybrid search
  - "React and TypeScript experience" → multiple keyword matches
- Performance benchmarks (target: 90% of queries <600ms)
- **Result**: Validated implementation ready for deployment

---

## Summary

**Key Innovation**: Hybrid search using `languages.yml` (650+ languages, zero maintenance) + semantic ranking.

**Benefits**:
- ✅ **Zero Manual Maintenance**: GitHub maintains languages.yml (650+ languages, aliases, extensions)
- ✅ **Fast Keyword Pre-Filter**: Reduces search space from 100 repos → 15 repos (<20ms)
- ✅ **Accurate Semantic Ranking**: Fine-tuned model ranks filtered repos (<500ms)
- ✅ **GitHub Topics Integration**: Leverages user-tagged keywords (no work for you)
- ✅ **Quantifies Skill Depth**: Project count × complexity × recency
- ✅ **<600ms Total Latency**: vs 2-5s pure semantic search (83% faster)
- ✅ **Works with ANY Repository**: No custom .repo-context.json required

**Next Steps**:
1. Implement DynamicKeywordExtractor using languages.yml (priority: HIGH)
2. Enhance RepoScoringService with hybrid scoring (priority: HIGH)
3. Simplify training data (README + metadata only, not full file contents) (priority: HIGH)
4. Update `/api/ai` endpoint to use 2-stage pipeline (priority: MEDIUM)
5. Add comprehensive tests (priority: MEDIUM)
