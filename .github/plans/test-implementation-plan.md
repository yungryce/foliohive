# Test Implementation Plan

**Purpose**: Align test coverage with current architecture and prepare for client-cleanup implementation phases.

**Status**: Ready for implementation  
**Created**: 2026-02-21  
**Target Coverage**: 80%+ per module

---

## Executive Summary

This plan addresses:
1. **Stale tests**: Functions/methods tested that no longer exist
2. **Missing tests**: Core functionality with no coverage
3. **Phase alignment**: Tests needed to validate cleanup implementation
4. **Architecture drift**: Tests not reflecting current data flow

---

## Test File Status Matrix

| Module | File | Status | Priority | Phase Dependency |
|--------|------|--------|----------|------------------|
| **ai** | test_ai_assistant.py | Partial - needs extraction support | HIGH | Phase 1, 2 |
| **ai** | test_data_filter.py | Good - needs extractor tests | HIGH | Phase 1 |
| **ai** | test_summary_manager.py | Partial - missing micro-summary/aggregation | HIGH | Phase 2, 3 |
| **cache** | test_cache_manager.py | Partial - missing file retrieval tests | MEDIUM | Phase 1 |
| **cache** | test_fingerprint_manager.py | Good - minimal updates needed | LOW | - |
| **github** | test_github_api.py | Good - stable | LOW | - |
| **github** | test_repo_manager.py | **MISSING** | HIGH | Phase 1 |
| **github** | test_graphql_api.py | **MISSING** | MEDIUM | - |
| **github** | test_api_usage.py | **MISSING** | LOW | - |
| **queue** | test_queue_manager.py | Good - stable | LOW | - |
| **table** | test_table_manager.py | Good - needs discovered paths tests | MEDIUM | Phase 1 |

---

## Phase 1 — Config Extraction Foundation

### Critical Tests Required

#### `test_data_filter.py` — Add Extractor Tests

**Status**: Needs new test class for extraction registry

**New Tests Required**:
```python
class TestConfigExtractionRegistry:
    """Test CONFIG_EXTRACTION_SCHEMAS and dispatcher."""
    
    def test_registry_contains_priority_extractors(self):
        """Verify all priority config types are registered."""
        # requirements.txt, pyproject.toml, package.json, docker-compose.yml
        
    def test_get_extractor_returns_callable(self):
        """Registry returns valid extractor functions."""
        
    def test_extractor_dispatcher_routes_by_filename(self):
        """Dispatcher correctly maps filename to extractor."""
        
    def test_unknown_file_returns_none_extractor(self):
        """Unknown files have no extractor (graceful skip)."""


class TestPythonExtractors:
    """Test Python config extractors."""
    
    def test_extract_requirements_txt(self):
        """Parse requirements.txt format."""
        # Input: numpy>=1.21.0\npandas==1.3.0
        # Output: {"dependencies": [...], "constraints": [...]}
        
    def test_extract_pyproject_toml(self):
        """Parse pyproject.toml dependencies."""
        # Extract [tool.poetry.dependencies] or [project.dependencies]
        
    def test_extract_pyproject_handles_invalid_toml(self):
        """Malformed TOML returns error dict."""
        

class TestNodeExtractors:
    """Test Node.js config extractors."""
    
    def test_extract_package_json(self):
        """Parse package.json dependencies."""
        # Output: {"dependencies": {...}, "devDependencies": {...}, "scripts": {...}}
        
    def test_extract_package_json_handles_invalid_json(self):
        """Malformed JSON returns error dict."""


class TestContainerExtractors:
    """Test container config extractors."""
    
    def test_extract_dockerfile_commands(self):
        """Extract FROM, RUN, EXPOSE, ENV from Dockerfile."""
        # Output: {"base_images": [...], "exposed_ports": [...], "env_vars": {...}}
        
    def test_extract_docker_compose_services(self):
        """Parse docker-compose.yml service definitions."""
        # Output: {"services": [...], "networks": [...], "volumes": [...]}


class TestIaCExtractors:
    """Test IaC config extractors."""
    
    def test_extract_terraform_resources(self):
        """Parse main.tf resource blocks."""
        # Output: {"resources": [...], "providers": [...]}
        
    def test_extract_bicep_resources(self):
        """Parse main.bicep resource declarations."""
        # Output: {"resources": [...], "modules": [...]}
```

**Acceptance Criteria**:
- Each extractor returns structured dict (not raw text)
- Invalid input returns `{"error": "...", "raw_sample": "..."}` dict
- Extractors are deterministic (same input → same output)

---

#### `test_cache_manager.py` — Add File Retrieval Tests

**Status**: Missing tests for `get_repo_files()` extracted config behavior

**New Tests Required**:
```python
class TestGetRepoFiles:
    """Test get_repo_files() with extracted config payloads."""
    
    def test_returns_readme_and_extracted_configs(self):
        """Returns README blob + extracted config dicts."""
        # Mock: README blob exists, 2 config extractions exist
        # Verify: returns {"readme": {...}, "configs": {"package.json": {...}, ...}}
        
    def test_skips_missing_extractions_gracefully(self):
        """Missing extractions don't break retrieval."""
        # Mock: README exists, 1 config extraction, 1 missing
        # Verify: returns only available artifacts
        
    def test_missing_readme_returns_empty_readme_section(self):
        """Missing README blob returns empty/null readme."""
        # Mock: configs exist, README doesn't
        # Verify: returns {"readme": None, "configs": {...}}
        
    def test_respects_max_config_files_limit(self):
        """Honors max_config_files parameter."""
        # Mock: 10 config extractions available
        # Call: max_config_files=3
        # Verify: returns exactly 3 configs
        
    def test_extraction_failure_marked_in_result(self):
        """Failed extractions are indicated in response."""
        # Mock: extraction blob contains {"error": "..."}
        # Verify: result includes error indicator
```

**Acceptance Criteria**:
- No raw config text in responses (only extracted dicts)
- Graceful handling of partial availability
- Respects file budget limits

---

#### `test_table_manager.py` — Add Discovered Paths Tests

**Status**: Needs tests for `RepoDiscoveredPathsRow` extraction metadata

**New Tests Required**:
```python
class TestRepoDiscoveredPaths:
    """Test RepoDiscoveredPathsRow CRUD and queries."""
    
    def test_upsert_discovered_path_with_extraction_metadata(self):
        """Persist extraction success/failure state."""
        # Fields: repo_name, file_path, file_type, extractor_key, extraction_status
        
    def test_query_discovered_paths_by_repo_and_fingerprint(self):
        """Find paths for repo with matching fingerprint."""
        
    def test_delete_stale_paths_for_repo(self):
        """Cleanup paths with outdated fingerprints."""
        
    def test_extraction_status_transitions(self):
        """Track: pending → extracted | failed."""
```

**Acceptance Criteria**:
- Tracks per-file extraction metadata
- Supports fingerprint-based invalidation
- Cleanup operations work correctly

---

#### **NEW FILE**: `test_repo_manager.py`

**Status**: **MISSING** - critical for Phase 1

**Priority**: **HIGH**

**Scope**: Test `GitHubRepoManager` file discovery + extraction triggering

**Test Classes Required**:
```python
class TestRepoManagerFileDiscovery:
    """Test file discovery and path indexing."""
    
    def test_discover_standard_config_files(self):
        """Discovers common config files from tree."""
        
    def test_filters_irrelevant_paths(self):
        """Skips non-config files."""
        
    def test_persists_discovered_paths_to_table(self):
        """Writes RepoDiscoveredPathsRow entries."""


class TestRepoManagerExtractionIntegration:
    """Test extraction triggering during cache phase."""
    
    def test_triggers_extractor_for_discovered_config(self):
        """Calls appropriate extractor based on filename."""
        
    def test_persists_extracted_artifact_to_blob(self):
        """Saves extraction result as JSON blob."""
        
    def test_updates_discovered_path_extraction_status(self):
        """Marks extraction success/failure in table."""
        
    def test_skips_files_without_registered_extractor(self):
        """Files with no extractor are skipped gracefully."""
```

**Acceptance Criteria**:
- Mock GitHub API responses
- Verify table writes
- Verify blob storage calls
- Test extraction error paths

---

## Phase 2 — Repo Micro Summary Pipeline

### Critical Tests Required

#### `test_summary_manager.py` — Add Micro Summary Tests

**Status**: Missing micro-summary generation tests

**New Tests Required**:
```python
class TestRepoMicroSummaryGeneration:
    """Test generate_repo_micro_summary() pipeline."""
    
    def test_builds_context_from_readme_and_configs(self):
        """Combines README + extracted configs into prompt."""
        
    def test_enforces_token_budget(self):
        """Stays within ~10-12k input token limit."""
        
    def test_returns_structured_json_output(self):
        """Output schema: {overview, key_features, tech_stack, architecture_patterns, skill_signals}."""
        
    def test_validates_json_schema_before_caching(self):
        """Invalid JSON is rejected."""
        
    def test_caches_successful_micro_summary(self):
        """Persists micro-summary artifact to blob storage."""
        
    def test_handles_api_errors_gracefully(self):
        """API failures return error dict (not crash)."""


class TestRepoMicroSummaryCaching:
    """Test micro-summary cache operations."""
    
    def test_cache_key_includes_repo_fingerprint(self):
        """Cache keys are fingerprint-versioned."""
        
    def test_retrieves_cached_micro_summary(self):
        """get_repo_micro_summary() returns cached artifact."""
        
    def test_cache_miss_returns_none(self):
        """Missing micro-summary returns None (not error)."""
```

**Acceptance Criteria**:
- JSON-only output (no raw HTML)
- Token budget enforcement
- Cache invalidation via fingerprint
- Error handling doesn't crash pipeline

---

#### `test_ai_assistant.py` — Add Micro Summary Prompt Tests

**Status**: Needs new method tests

**New Tests Required**:
```python
class TestMicroSummaryPrompts:
    """Test _build_repo_micro_summary_system() and user prompts."""
    
    def test_system_prompt_enforces_json_output(self):
        """System prompt includes JSON-only instructions."""
        
    def test_user_prompt_includes_readme_and_config(self):
        """User message contains README + extracted configs."""
        
    def test_prompt_includes_token_cap_instruction(self):
        """Instructs model to stay within output limit."""
        
    def test_prompt_specifies_output_schema(self):
        """Defines expected JSON structure."""
```

**Acceptance Criteria**:
- Prompts are deterministic
- Token limits are explicit
- Schema is machine-verifiable

---

## Phase 3 — Profile Aggregation + Formatter Split

### Critical Tests Required

#### `test_summary_manager.py` — Add Aggregation Tests

**Status**: Missing aggregation + formatting tests

**New Tests Required**:
```python
class TestProfileAggregation:
    """Test aggregate_profile_from_summaries() logic."""
    
    def test_aggregates_multiple_micro_summaries(self):
        """Combines 5-10 micro-summaries into profile JSON."""
        # Input: List[micro_summary_dict]
        # Output: {"skills": {...}, "domains": {...}, "experience_signals": {...}}
        
    def test_deduplicates_skills_across_repos(self):
        """Same skill from multiple repos is deduplicated."""
        
    def test_scores_skill_frequency(self):
        """Skill prominence based on occurrence count."""
        
    def test_skips_repos_without_micro_summary(self):
        """Missing micro-summaries don't break aggregation."""
        
    def test_caches_profile_aggregate_json(self):
        """Stores profile JSON artifact separately."""


class TestProfileHTMLFormatting:
    """Test format_profile_html() rendering."""
    
    def test_renders_html_from_aggregate_json_only(self):
        """HTML generation uses cached aggregate (not raw files)."""
        
    def test_html_includes_all_aggregate_sections(self):
        """Skills, domains, experience_signals are all rendered."""
        
    def test_html_is_valid_structure(self):
        """Output is parseable HTML (not truncated)."""
        
    def test_caches_final_html_separately(self):
        """HTML artifact is cached after generation."""
```

**Acceptance Criteria**:
- Aggregation is JSON-only (no HTML generation)
- Formatting is HTML-only (no AI calls)
- Both stages are cacheable independently
- Partial micro-summary availability is handled

---

#### `test_ai_assistant.py` — Add Aggregation Prompt Tests

**Status**: Needs aggregation API method tests

**New Tests Required**:
```python
class TestProfileAggregationPrompts:
    """Test _build_profile_aggregation_system() prompts."""
    
    def test_system_prompt_enforces_json_output(self):
        """Aggregation returns JSON (not HTML)."""
        
    def test_context_is_micro_summaries_only(self):
        """No raw README or config in aggregation prompt."""
        
    def test_deduplication_instructions_present(self):
        """Prompt instructs model to deduplicate skills."""
```

**Acceptance Criteria**:
- Aggregation prompt uses only micro-summaries
- Output is JSON-constrained
- No raw file context in prompt

---

## Phase 4 — Query From Summaries

### Critical Tests Required

#### `test_summary_manager.py` — Add Query Tests

**Status**: Needs query context builder tests

**New Tests Required**:
```python
class TestQueryFromSummaries:
    """Test query_from_summaries() filtering + context building."""
    
    def test_filters_repos_by_query_relevance(self):
        """Query filters micro-summaries before building context."""
        
    def test_builds_context_from_profile_aggregate_and_filtered_summaries(self):
        """Context = profile JSON + relevant repo micro-summaries."""
        
    def test_respects_max_repos_limit(self):
        """Query context limited to top N relevant repos."""
        
    def test_no_raw_file_access_during_query(self):
        """Query uses only cached summaries (no README fetch)."""
        
    def test_caches_query_response(self):
        """Query results are cached with query fingerprint."""
```

**Acceptance Criteria**:
- Query uses only cached artifacts (no live file reads)
- Relevance filtering works correctly
- Token budget is respected
- Cache keys include query fingerprint

---

## Missing Test Files — Creation Required

### `test_repo_manager.py` (HIGH PRIORITY)

**Purpose**: Test file discovery, extraction triggering, blob persistence

**Estimated Lines**: ~400-500

**Key Areas**:
- File tree traversal
- Config file identification
- Extractor dispatch
- Extraction result persistence
- Error handling

**Dependencies**: 
- Mock `GitHubAPI`
- Mock `CacheManager`
- Mock `TableManager`

---

### `test_graphql_api.py` (MEDIUM PRIORITY)

**Purpose**: Test batch blob fetching via GraphQL

**Estimated Lines**: ~200-300

**Key Areas**:
- Query construction
- Batch blob responses
- Rate limit handling
- Error scenarios

**Dependencies**:
- Mock `requests.Session`
- Mock GraphQL responses

---

### `test_api_usage.py` (LOW PRIORITY)

**Purpose**: Test API usage tracking

**Estimated Lines**: ~150-200

**Key Areas**:
- Request counting
- File target tracking
- Rate limit detection

**Dependencies**: None (pure data structure tests)

---

## Stale Test Cleanup

### Functions/Methods to Remove from Tests

#### `test_summary_manager.py`
- **Remove**: Any tests for removed chunking methods (replaced by extraction)
- **Remove**: Tests assuming raw config text input
- **Update**: Token budget tests to reflect new limits

#### `test_cache_manager.py`
- **Remove**: Tests for removed bundle-related methods
- **Update**: Cache key generation tests to match new schema

#### `test_fingerprint_manager.py`
- **Verify**: All methods tested still exist (likely stable)

---

## Test Execution Strategy

### Pre-Implementation Baseline
```bash
cd api/v0.3.0/tests
./run_tests.sh

# Expected failures:
# - test_data_filter.py::TestConfigExtractionRegistry (not implemented)
# - test_summary_manager.py::TestRepoMicroSummaryGeneration (not implemented)
# - test_summary_manager.py::TestProfileAggregation (not implemented)
```

### Phase 1 Validation
```bash
# After Phase 1 implementation:
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_data_filter.py::TestConfigExtractionRegistry -v
pytest api/v0.3.0/shared/src/foliohive_shared/cache/tests/test_cache_manager.py::TestGetRepoFiles -v
pytest api/v0.3.0/shared/src/foliohive_shared/github/tests/test_repo_manager.py -v
pytest api/v0.3.0/shared/src/foliohive_shared/table/tests/test_table_manager.py::TestRepoDiscoveredPaths -v

# All Phase 1 tests must pass before proceeding to Phase 2
```

### Phase 2 Validation
```bash
# After Phase 2 implementation:
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestRepoMicroSummaryGeneration -v
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_ai_assistant.py::TestMicroSummaryPrompts -v

# Phase 2 tests must pass before proceeding to Phase 3
```

### Phase 3 Validation
```bash
# After Phase 3 implementation:
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestProfileAggregation -v
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestProfileHTMLFormatting -v
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_ai_assistant.py::TestProfileAggregationPrompts -v

# Phase 3 tests must pass before proceeding to Phase 4
```

### Phase 4 Validation
```bash
# After Phase 4 implementation:
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestQueryFromSummaries -v

# All tests must pass before production deployment
```

### Full Suite
```bash
# Run full test suite after all phases:
cd api/v0.3.0/tests
./run_tests.sh --cov=foliohive_shared --cov-report=html

# Target: 80%+ coverage across all modules
```

---

## Success Metrics

### Coverage Targets
- **ai module**: 85%+ (up from current ~60%)
- **cache module**: 80%+ (up from current ~70%)
- **github module**: 75%+ (new repo_manager adds coverage)
- **table module**: 80%+ (stable, minimal additions)
- **queue module**: 80%+ (stable)

### Quality Gates
- All Phase 1 tests pass before Phase 2 implementation starts
- All Phase 2 tests pass before Phase 3 implementation starts
- All Phase 3 tests pass before Phase 4 implementation starts
- Full test suite passes before production deployment
- No test mocking of current timestamp (use fixtures)
- All extractors have happy path + error path tests
- All cache operations have hit/miss/error tests

---

## Appendix: Test Patterns

### Extractor Test Pattern
```python
def test_extract_<format>_<scenario>():
    """Test extractor for <format> with <scenario>."""
    raw_content = """
    <sample file content>
    """
    
    result = extract_<format>(raw_content)
    
    # Verify structure
    assert isinstance(result, dict)
    assert "error" not in result  # or assert "error" in result for error cases
    
    # Verify content
    assert result["<expected_key>"] == <expected_value>
```

### Cache Test Pattern
```python
def test_cache_<operation>_<scenario>(mock_blob_client):
    """Test cache <operation> with <scenario>."""
    cache = CacheManager()
    cache._blob_service_client = mock_blob_client
    
    # Setup
    key = "test_key"
    data = {"test": "data"}
    
    # Execute
    result = cache.<operation>(key, data)
    
    # Verify
    assert result is not None
    mock_blob_client.<expected_call>.assert_called_once()
```

### Summary Test Pattern
```python
@patch('foliohive_shared.ai.ai_assistant.OpenAI')
def test_summary_<type>_<scenario>(mock_openai):
    """Test <type> summary generation with <scenario>."""
    # Mock AI response
    mock_client = MagicMock()
    mock_openai.return_value = mock_client
    mock_client.chat.completions.create.return_value = <mock_response>
    
    # Execute
    manager = SummaryManager(username="test")
    result = manager.<method>(<inputs>)
    
    # Verify
    assert result is not None
    assert "<expected_content>" in result
```

---

## Notes

- All tests must be runnable in CI/CD without external dependencies
- Use pytest fixtures for complex setup
- Mock external services (GitHub API, OpenAI API, Azure Storage)
- Test both happy paths and error scenarios
- Avoid testing implementation details (focus on contracts)
- Keep tests fast (< 5 seconds per test class)

---

**End of Test Implementation Plan**
