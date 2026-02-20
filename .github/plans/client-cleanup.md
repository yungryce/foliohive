Summary functions: 
 - `api_gateway.get_profile_summary()` 
 - `api_gateway.get_repo_summary()`
 - `api_gateway.portfolio_query()`

Issue: We are over-feeding context ~45k and under-constraining output ~6k.

Bug: Response truncated (hit max_completion_tokens=6000).

Input Context for `foliohive_shared/ai/summary_manager.py` capturing sample config file contents, readme contents and metadata: 
- `.github/plans/get_profile_summary.log`: This has logs summary inputs from `api_gateway._build_repo_statistics()` for aggregated metadata and `api_gateway._get_repo_files` for file contents (readme and config)
- `.github/plans/get_repo_summary.log`: This has logs summary inputs from `api_gateway._get_portfolio_bundle()` for repo metadata and `api_gateway._get_repo_files` for file contents (readme and config)


### Phase 1: Config Extraction Layer (Prep)
1. Extend data_filter.py with extraction schemas
    - Add CONFIG_EXTRACTION_SCHEMAS dict mapping file patterns to extraction functions
    - Implement extractors for config types (prioritized by top repo language):
        - Python: Extract dependency names only from requirements.txt, core fields from pyproject.toml
        - Node/JS: Extract dependencies from package.json (extract top-level deps only)
        - Java: Extract dependencies from pom.xml (<dependencies> section), build.gradle (dependencies block)
        - Docker: Dockerfile extraction; extend for docker-compose.yml (services, images, volumes)
        - Cloud/IaC: Extract resources from main.tf, pipeline stages from azure-pipelines.yml
        - pattern repeated for other common languages
    - Each extractor returns structured dict, not raw text (e.g., {"dependencies": [...], "scripts": {...}})

2. Modify cache_worker.py _cache_blob_file() function
    - After downloading blob content, check if file matches extraction schema
    - If match found, apply extractor and store only extracted data. discard raw content
    - Store extracted data in new blob path: extracted/{fingerprint}/{repo_name}/{filename}.json
    - Update discoverable paths table to track extraction status

3. Update cache_manager.py get_repo_files()
    - Retrieve extracted config files
    - skip if extraction not available
    - Return structured dict: {"filename": str, "content": str, "extracted": dict | None}

4. Update summary_manager.py chunking methods
    - Modify chunk_config_file() to use extracted data when available
    - Format extracted data compactly (e.g., dependency names as comma-separated list)
    - Reduces config token budget usage by 60-80%


### Phase 2: Repo Summary Pipeline (Stage 1 Interpretation)

1. Add summary_manager.py method generate_repo_micro_summary()
    - Input: Single repo context (README + metadata + extracted configs)
    - Token budget: 10-12k input → 1-2k output
    - Generates structured JSON summary (not HTML):
        ```Json
        {
        "overview": "2-3 sentence description",
        "key_features": ["bullet", "points"],
        "tech_stack": {"languages": [...], "frameworks": [...], "tools": [...]},
        "architecture_patterns": ["observed patterns with evidence"],
        "skill_signals": [{"skill": str, "confidence": float, "evidence": str}]
        }
        ```
    - Cache result in blob storage at summaries/{fingerprint}/{repo_name}/micro-summary.json

2. Update cache_worker.py to generate micro-summaries
    - After caching all blobs for a repo, trigger generate_repo_micro_summary() 
    - Store summary alongside cached files (Consideration: caching blobs when summaries exist. possibilities for reusing blobs?)
    - Mark repo as "summary_ready" in table_schema.py RepoSyncStatus table

3. Add ai_assistant.py prompt for micro-summary
    - Strict output format constraints (JSON only, no markdown)
    - Focus on signal extraction, not presentation
    - Explicit token limit: "Output must not exceed 2000 tokens"

### Phase 3: Profile Aggregation Pipeline (Stage 2 Interpretation)
1. Add summary_manager.py method aggregate_profile_from_summaries()
    - Input: Collection of repo micro-summaries (NOT raw content)
    - Token budget: 5-8k input (compressed summaries) → 2-3k output
    - Two-stage process:
        - Stage 2a - Skill Aggregation: Deduplicate skills across repos, rank by confidence × frequency
        - Stage 2b - Profile Evaluation: Assess code quality, patterns, strengths based on aggregated signals
    - Generates structured profile data (JSON), not HTML yet
2. Add summary_manager.py method format_profile_html()
    - Input: Aggregated profile JSON from step 1
    - Token budget: ~3k input → ~2k output (HTML only)
    - Pure presentation layer - no new analysis
    - Strict constraints in prompt: "Maximum 5 sections, 4 repos mentioned, 3 sentences per repo"
3. Refactor api_gateway.py get_profile_summary()
    - Check if all repos have cached micro-summaries
    - If yes: Load summaries → aggregate → format HTML
    - if no: skip repo for aggregated summary
    - Cache final profile HTML separately for fast retrieval

### Phase 4: Query Pipeline (Stage 3 Query Evaluation)
1. Add summary_manager.py method query_from_summaries()
    - Input: User query + profile JSON (not HTML) + selected repo micro-summaries
    - Token budget: 5-10k input → 1-2k output
    - Filter relevant repos based on query keywords before loading summaries
    - Return markdown answer with repo references
2. Refactor api_gateway.py portfolio_query()
    - Use cached profile aggregation + repo micro-summaries
    - Avoid re-reading raw config files or READMEs


### Phase 5: Token Budget Optimization
1. Update summary_manager.py TOKEN_BUDGETS
    Rebalance budgets per stage:
    Stage	Metadata	README	Config	Reserve	Total
    Repo micro-summary	2k	8k	2k	1k	13k
    Profile aggregation	1k	0	0	1k	2k (summaries only)
    Profile HTML formatting	3k	0	0	500	3.5k
    Query	2k	0	0	1k	3k (summaries only)
2. Update ai_assistant.py output token limits
    - Repo micro-summary: max_completion_tokens=2000
    - Profile aggregation: max_completion_tokens=3000
    - Profile HTML: max_completion_tokens=2500
    - Query: max_completion_tokens=2000
3. Add output constraints to prompts in ai_assistant.py
    - Explicitly state token limits in system messages
    - Add counting instructions: "Use bullet lists. Maximum 3 sentences per repository."
    - Add truncation warnings: "If you approach token limit, prioritize overview and top skills."



- Output must be concise and fit within a short profile card.
- Prefer summarization over completeness.
- Never describe every repository individually unless critical.
- The final HTML must not exceed 2000 tokens.
- Limit project descriptions to 3–4 sentences per project.
- Do not repeat information.
- Be concise and structured.
- Maximum total output length: 2500 tokens.
- Maximum 5 sections.
- Maximum 4 repositories mentioned.
- Maximum 3 sentences per repository.
- Use concise bullet lists when possible.
- Do NOT repeat technologies.
- Do NOT explain obvious concepts.
- Do NOT restate input metadata.
- Prefer aggregation over enumeration.
- Only infer a skill if explicit evidence exists in the provided repository data.
If uncertain, lower confidence.
- Based strictly on provided data, list observed architecture patterns

