# Client Cleanup Implementation Guide

This guide is intended to be used side-by-side with `client-cleanup.md` during implementation.

## How to Use This Guide

- Treat each phase as a mergeable unit.
- Keep endpoint response contracts stable unless explicitly noted.
- Use feature flags to roll out summary-first paths safely.
- Run tests at the end of each phase before proceeding.

## Preconditions

1. Confirm baseline logs are available:
   - `.github/plans/get_profile_summary.log`
   - `.github/plans/get_repo_summary.log`
2. Confirm env vars for OpenAI and storage are set.
3. Confirm cache worker queue processing is healthy.

## Phase 1 Implementation — Config Extraction Foundation

### Files to Update

- `api/v0.3.0/shared/src/foliohive_shared/ai/data_filter.py`
- `api/v0.3.0/function-app/blueprints/cache_worker.py`
- `api/v0.3.0/shared/src/foliohive_shared/cache/cache_manager.py`
- `api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py` (discovered path metadata fields)
- tests under:
  - `api/v0.3.0/shared/src/foliohive_shared/ai/tests/`
  - `api/v0.3.0/shared/src/foliohive_shared/cache/tests/`

### Implementation Steps

1. Add extraction registry:
   - Add `CONFIG_EXTRACTION_SCHEMAS` and dispatcher in `data_filter.py`.
   - Implement deterministic extractors for required file types.
2. Add extraction call in cache worker:
   - During config file caching, run extractor and save extracted JSON artifact.
   - Skip raw config save path for extracted-only strategy.
3. Extend discovered path row payload:
   - Track extraction success/failure and extractor key per file.
4. Extend cache retrieval:
   - `get_repo_files()` returns extracted config map with stable keys.

### Phase Exit Criteria

- Extracted artifacts are persisted and retrievable.
- Config retrieval path no longer depends on raw config text.
- Unit tests cover extractor happy path + parser failures.

## Phase 2 Implementation — Repo Micro Summaries

### Files to Update

- `api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`
- `api/v0.3.0/shared/src/foliohive_shared/ai/ai_assistant.py`
- `api/v0.3.0/function-app/blueprints/cache_worker.py`
- `api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py` (status enum)
- tests under:
  - `api/v0.3.0/shared/src/foliohive_shared/ai/tests/`
  - `api/v0.3.0/tests/integration/`

### Implementation Steps

1. Add micro-summary prompt + API method in `ai_assistant.py`:
   - JSON-only instructions, token cap, schema-constrained output.
2. Add `generate_repo_micro_summary()` in `summary_manager.py`.
3. Trigger micro-summary generation in `cache_worker.py` post extraction.
4. Add new repo status transition to `summary_ready`.

### Phase Exit Criteria

- For successful repos, `summary_ready` is set and micro-summary artifact exists.
- Invalid micro-summary JSON is handled safely with retry + failure mark.

## Phase 3 Implementation — Profile Aggregation + Formatter Split

### Files to Update

- `api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`
- `api/v0.3.0/shared/src/foliohive_shared/ai/ai_assistant.py`
- `api/v0.3.0/function-app/blueprints/api_gateway.py`

### Implementation Steps

1. Add `aggregate_profile_from_summaries()`:
   - Input: repo micro-summary set.
   - Output: profile aggregate JSON.
2. Add `format_profile_html()`:
   - Input: aggregate JSON only.
   - Output: HTML only.
3. Refactor `get_profile_summary()` path:
   - Load summaries.
   - Skip missing repos.
   - Cache final profile HTML artifact.

### Phase Exit Criteria

- Profile summary endpoint uses summary-based context instead of raw config files.
- No truncation warnings in normal test payloads.

## Phase 4 Implementation — Query From Summaries

### Files to Update

- `api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`
- `api/v0.3.0/function-app/blueprints/api_gateway.py`
- `api/v0.3.0/shared/src/foliohive_shared/ai/ai_assistant.py`

### Implementation Steps

1. Add `query_from_summaries()` in `summary_manager.py`.
2. Add query-to-repo relevance filter before loading summaries.
3. Refactor `portfolio_query()` to use aggregate + micro-summary cache inputs.

### Phase Exit Criteria

- Query path does not pull raw README/config files for context generation.
- Query output includes references to repos used.

## Phase 5 Implementation — Budget + Prompt Hardening

### Files to Update

- `api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`
- `api/v0.3.0/shared/src/foliohive_shared/ai/ai_assistant.py`
- tests under `api/v0.3.0/shared/src/foliohive_shared/ai/tests/`

### Implementation Steps

1. Rebalance `TOKEN_BUDGETS` by stage.
2. Apply per-stage `max_completion_tokens` values.
3. Add stricter prompt output constraints (section/sentence caps, truncation prioritization).

### Phase Exit Criteria

- Measured reduction in prompt token estimates.
- Truncation warning rate drops significantly vs baseline.

## Validation Matrix

### Unit Tests

- Extraction parsing for each file type.
- Summary JSON schema validation.
- Token budget/chunking behavior.
- Status transition validation (`pending -> synced -> cached -> summary_ready`).

### Integration Tests

- Cache worker end-to-end writes extraction + micro-summary artifacts.
- Profile summary endpoint works when some repos are missing micro-summaries.
- Query endpoint uses summary artifacts and returns response with repo metadata.

### Manual Verification

1. Trigger candidate refresh.
2. Confirm repo statuses progress to `summary_ready` where possible.
3. Call:
   - `GET /candidate/{username}/summary`
   - `GET /candidate/{username}/{repo}/readme-summary`
   - `POST /ai`
4. Validate latency and output completeness against baseline logs.

## Rollout Checklist

- [ ] Feature flag for extraction-enabled cache path.
- [ ] Feature flag for summary-first profile path.
- [ ] Feature flag for summary-first query path.
- [ ] Canary rollout enabled for selected users.
- [ ] SLO review complete (latency, truncation, error rate).
- [ ] General availability flip.

## Risks and Mitigations

1. **Extractor brittleness**
   - Mitigation: deterministic parser tests + graceful skip behavior.
2. **Cache worker latency increase**
   - Mitigation: metrics + optional queue split only if needed.
3. **Partial summary coverage**
   - Mitigation: skip-missing strategy and clear metadata in response.
4. **Schema transition bugs**
   - Mitigation: update status validation tests before rollout.

## Suggested Work Breakdown (PR Sequence)

1. PR-1: Extraction registry + cache/retrieval plumbing + tests.
2. PR-2: Micro-summary generation + `summary_ready` status + tests.
3. PR-3: Profile aggregation/format split + endpoint refactor.
4. PR-4: Query-from-summaries refactor.
5. PR-5: Budget/prompt hardening + observability tuning.
