# Test Implementation Checklist

Quick reference for tracking test implementation progress aligned with client-cleanup phases.

**Last Updated**: 2026-02-22  
**Status**: Phase 1-4 complete · 158/158 tests passing · stale tests removed

---

## Legend

- [ ] Not started
- [◐] In progress
- [x] Complete
- [!] Blocked

---

## Phase 1: Config Extraction Foundation

### test_data_filter.py
- [x] `TestConfigExtractionRegistry`
  - [x] test_registry_contains_priority_extractors
  - [x] test_get_extractor_returns_callable
  - [x] test_extractor_dispatcher_routes_by_filename
  - [x] test_unknown_file_returns_none_extractor

- [x] `TestPythonExtractors`
  - [x] test_extract_requirements_txt
  - [x] test_extract_pyproject_toml
  - [x] test_extract_pyproject_handles_invalid_toml

- [x] `TestNodeExtractors`
  - [x] test_extract_package_json
  - [x] test_extract_package_json_handles_invalid_json

- [x] `TestContainerExtractors`
  - [x] test_extract_dockerfile_commands
  - [x] test_extract_docker_compose_services

- [x] `TestIaCExtractors`
  - [x] test_extract_terraform_resources
  - [x] test_extract_bicep_resources

**Phase 1 Exit**: All extractors return structured dicts, invalid inputs handled gracefully

---

### test_cache_manager.py
- [x] `TestGetRepoFiles`
  - [x] test_returns_readme_and_extracted_configs
  - [x] test_skips_missing_extractions_gracefully
  - [x] test_missing_readme_returns_empty_readme_section
  - [x] test_respects_max_config_files_limit
  - [x] test_extraction_failure_marked_in_result

**Phase 1 Exit**: Config retrieval returns only extracted dicts (no raw text)

---

### test_table_manager.py
- [x] `TestRepoDiscoveredPaths`
  - [x] test_upsert_discovered_path_with_extraction_metadata
  - [x] test_query_discovered_paths_by_repo_and_fingerprint
  - [x] test_delete_stale_paths_for_repo
  - [x] test_extraction_status_transitions

**Phase 1 Exit**: Extraction metadata persisted and queryable

---

### test_repo_manager.py (NEW FILE)
- [x] **File created**
- [x] `TestRepoManagerFileDiscovery`
  - [x] test_discover_standard_config_files
  - [x] test_filters_irrelevant_paths
  - [x] test_persists_discovered_paths_to_table

- [x] `TestRepoManagerExtractionIntegration`
  - [x] test_triggers_extractor_for_discovered_config
  - [x] test_persists_extracted_artifact_to_blob
  - [x] test_updates_discovered_path_extraction_status
  - [x] test_skips_files_without_registered_extractor

**Phase 1 Exit**: File discovery + extraction triggering validated

---

## Phase 2: Repo Micro Summary Pipeline

### test_summary_manager.py
- [x] `TestRepoMicroSummaryGeneration`
  - [x] test_builds_context_from_readme_and_configs
  - [x] test_enforces_token_budget
  - [x] test_returns_structured_json_output
  - [x] test_validates_json_schema_before_caching
  - [x] test_caches_successful_micro_summary
  - [x] test_handles_api_errors_gracefully

- [x] `TestRepoMicroSummaryCaching`
  - [x] test_cache_key_includes_repo_fingerprint
  - [x] test_retrieves_cached_micro_summary
  - [x] test_cache_miss_returns_none

**Phase 2 Exit**: Micro-summaries generated and cached per-repo

---

### test_ai_assistant.py
- [x] `TestMicroSummaryPrompts`
  - [x] test_system_prompt_enforces_json_output
  - [x] test_user_prompt_includes_readme_and_config
  - [x] test_prompt_includes_token_cap_instruction
  - [x] test_prompt_specifies_output_schema

**Phase 2 Exit**: Micro-summary prompts validated

---

## Phase 3: Profile Aggregation + Formatter Split

### test_summary_manager.py
- [x] `TestProfileAggregation`
  - [x] test_aggregates_multiple_micro_summaries
  - [x] test_deduplicates_skills_across_repos
  - [x] test_scores_skill_frequency
  - [x] test_skips_repos_without_micro_summary
  - [x] test_caches_profile_aggregate_json

- [x] `TestProfileHTMLFormatting`
  - [x] test_renders_html_from_aggregate_json_only
  - [x] test_html_includes_all_aggregate_sections
  - [x] test_html_is_valid_structure
  - [x] test_caches_final_html_separately

**Phase 3 Exit**: Profile aggregation and formatting decoupled

---

### test_ai_assistant.py
- [x] `TestProfileAggregationPrompts`
  - [x] test_system_prompt_enforces_json_output
  - [x] test_context_is_micro_summaries_only
  - [x] test_deduplication_instructions_present

**Phase 3 Exit**: Profile aggregation uses only micro-summaries

---

## Phase 4: Query From Summaries

### test_summary_manager.py
- [x] `TestQueryFromSummaries`
  - [x] test_filters_repos_by_query_relevance
  - [x] test_builds_context_from_profile_aggregate_and_filtered_summaries
  - [x] test_respects_max_repos_limit
  - [x] test_no_raw_file_access_during_query
  - [x] test_caches_query_response

**Phase 4 Exit**: Query uses only cached artifacts

---

## Missing Test Files (Optional/Lower Priority)

### test_graphql_api.py (NEW FILE)
- [ ] **File created**
- [ ] Query construction tests
- [ ] Batch blob response parsing
- [ ] Rate limit handling
- [ ] Error scenarios

---

### test_api_usage.py (NEW FILE)
- [ ] **File created**
- [ ] Request counting validation
- [ ] File target tracking
- [ ] Rate limit detection

---

## Cleanup Tasks

### Remove Stale Tests
- [x] test_summary_manager.py
  - [x] Remove tests for deleted chunking methods (TestCacheKeys, TestBundleContextBuilding, TestRepoSelectionStrategies, TestCacheManagement, TestCacheKeyBuilding, TestHighLevelAPIMethods, test_build_profile_context)
  - [x] Remove tests assuming raw config text input
  - [x] Update token budget test values

- [x] test_cache_manager.py
  - [x] No stale tests found — already clean

- [x] test_fingerprint_manager.py
  - [x] Removed stale generate_content_fingerprint + generate_bundle_fingerprint test bodies
  - [x] Fixed duplicate parametrize block
  - [x] Fixed misnamed method discover_repo_file → delete_repo_languages in table_manager.py

---

## Validation Commands

### Phase 1 Validation
```bash
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_data_filter.py::TestConfigExtractionRegistry -v
pytest api/v0.3.0/shared/src/foliohive_shared/cache/tests/test_cache_manager.py::TestGetRepoFiles -v
pytest api/v0.3.0/shared/src/foliohive_shared/github/tests/test_repo_manager.py -v
pytest api/v0.3.0/shared/src/foliohive_shared/table/tests/test_table_manager.py::TestRepoDiscoveredPaths -v
```

### Phase 2 Validation
```bash
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestRepoMicroSummaryGeneration -v
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_ai_assistant.py::TestMicroSummaryPrompts -v
```

### Phase 3 Validation
```bash
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestProfileAggregation -v
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestProfileHTMLFormatting -v
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_ai_assistant.py::TestProfileAggregationPrompts -v
```

### Phase 4 Validation
```bash
pytest api/v0.3.0/shared/src/foliohive_shared/ai/tests/test_summary_manager.py::TestQueryFromSummaries -v
```

### Full Suite
```bash
cd api/v0.3.0/tests
./run_tests.sh --cov=foliohive_shared --cov-report=html
```

---

## Success Criteria

### Coverage Targets (measured 2026-02-22 · 158 tests)

> Coverage is for unit tests only (mocked external services). Integration paths in `cache_manager.py`, `github_repo_manager.py`, and `github_graphql_api.py` require live-service or e2e tests.

| Module | Target | Actual | Status |
|--------|--------|--------|---------|
| ai/ai_assistant.py | ≥85% | 71% | below |
| ai/data_filter.py | ≥85% | 94% | ✓ |
| ai/summary_manager.py | ≥85% | 65% | below |
| ai (composite) | ≥85% | **73%** | below |
| cache/fingerprint_manager.py | ≥80% | 100% | ✓ |
| cache/cache_manager.py | ≥80% | 35% | below (integration paths) |
| cache (composite) | ≥80% | **42%** | below |
| github/github_api.py | ≥75% | 81% | ✓ |
| github/github_repo_manager.py | ≥75% | 39% | below (integration paths) |
| github (composite) | ≥75% | **44%** | below |
| table/table_manager.py | ≥80% | 74% | below |
| queue/queue_manager.py | ≥80% | 70% | below |

- [ ] ai module ≥85% — **73%** actual (gap: ai_assistant 71%, summary_manager 65%)
- [ ] cache module ≥80% — **42%** actual (gap: cache_manager 35% — integration-heavy)
- [ ] github module ≥75% — **44%** actual (gap: repo_manager 39% — integration-heavy)
- [ ] table module ≥80% — **74%** actual (gap: 6pp)
- [ ] queue module ≥80% — **70%** actual (gap: 10pp)

### Quality Gates
- [x] All Phase 1 tests pass before Phase 2 starts
- [x] All Phase 2 tests pass before Phase 3 starts
- [x] All Phase 3 tests pass before Phase 4 starts
- [x] Full suite passes — **158/158 passed, 0 failed** (2026-02-22)

---

## Notes

**Quick start for contributors:**
1. Pick a test class from current phase
2. Review test pattern in main plan
3. Implement tests following pattern
4. Run validation command for that class
5. Move to next test class

**Test requirements:**
- Mock all external services
- Test happy path + error path
- Keep tests fast (< 5s per class)
- Use fixtures for complex setup
