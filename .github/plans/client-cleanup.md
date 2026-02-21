# Client Profile Summary Optimization Plan (Refactored)

This plan targets summary quality and latency by replacing raw file-heavy prompts with extracted signals and staged summaries.

Reference architecture and flow in `.github/copilot-instructions.md`:
- `api_gateway.py -> sync_worker.py -> cache_worker.py`
- Summary entry points:
  - `api_gateway.get_profile_summary()`
  - `api_gateway.get_repo_summary()`
  - `api_gateway.portfolio_query()`

## Problem Statement

- Current context input is overfed (~45k tokens for profile paths).
- Output is under-constrained (`max_completion_tokens=6000`), causing truncation.
- Raw README/config payloads are noisy for skill inference and increase latency.

Logs to baseline before/after:
- `.github/plans/get_profile_summary.log`
- `.github/plans/get_repo_summary.log`

## Finalized Architecture Decisions

1. **Config storage strategy**: extracted-only for config files (no raw config blob persistence).
2. **Micro-summary generation timing**: asynchronous in `cache_worker.py` (no additional queue initially).
3. **Summary storage**: blob cache only (micro summaries + profile aggregate cache artifacts).
4. **Missing summary behavior**: skip repo and continue for profile/query endpoints.
5. **Primary objective**: reduce latency and truncation risk first, then iterate on richer extraction coverage.

## Non-Goals (Current Phase)

- No immediate migration to Foundry/OpenAI host changes.
- No new queue unless cache-worker latency breaches SLO.
- No UI contract changes for existing endpoint response shapes.

## Phase 1 — Config Extraction Layer (Foundation)

### Scope
1. Extend `foliohive_shared/ai/data_filter.py`:
   - Add `CONFIG_EXTRACTION_SCHEMAS` mapping filename/pattern -> extractor function.
   - Extractors return structured dicts only.

2. Initial extractor set (priority order):
   - Python: `requirements.txt`, `pyproject.toml`
   - Node/JS: `package.json` (top-level deps)
   - Java: `pom.xml`, `build.gradle`
   - Docker: `Dockerfile`, `docker-compose.yml`
   - IaC/Cloud: `main.tf`, `azure-pipelines.yml`, `host.json`, `serverless.yml`

3. Update `cache_worker.py`:
   - During file cache step, run extractors for config candidates.
   - Persist extracted JSON artifact under cache key namespace for extracted config.
   - Persist extraction metadata in discovered-path records (success/failure + extractor type).

4. Update `cache_manager.py` retrieval API:
   - `get_repo_files()` returns extracted config payloads.
   - If extraction unavailable, skip config payload (do not fetch raw config fallback).

### Acceptance Criteria
- At least 80% of discovered config files in sampled repos return parseable extraction output.
- Retrieval contract supports extracted config objects without breaking README retrieval.

## Phase 2 — Repo Micro Summary Pipeline

### Scope
1. Add `summary_manager.py::generate_repo_micro_summary()`:
   - Input: repo metadata + README + extracted config objects.
   - Output: strict JSON structure (analysis-only; no HTML).
   - Budget: ~10-12k input, <=2k output.

2. Update `cache_worker.py`:
   - After cache/extraction completes for a repo, generate/store micro summary.
   - Mark status progression in `RepoSyncStatus` to include `summary_ready`.

3. Add `ai_assistant.py` prompt builder for micro summaries:
   - JSON-only output rules.
   - Hard output constraints and truncation-safe behavior.

### Acceptance Criteria
- Repo micro-summary artifact exists for successful repos.
- Invalid JSON responses are rejected/retried once, then marked as failed with reason.

## Phase 3 — Profile Aggregation Pipeline

### Scope
1. Add `summary_manager.py::aggregate_profile_from_summaries()`:
   - Input: repo micro-summary collection (no raw files).
   - Stage 2a: skill aggregation and scoring.
   - Stage 2b: profile-level evaluation signals.
   - Output: structured profile JSON.

2. Add `summary_manager.py::format_profile_html()`:
   - Pure rendering from aggregated profile JSON.
   - Max 5 sections, capped repo mentions, concise per-repo text.

3. Refactor `api_gateway.get_profile_summary()`:
   - Load cached micro summaries.
   - Skip repos without micro-summary.
   - Cache final profile HTML separately.

### Acceptance Criteria
- `get_profile_summary()` no longer depends on raw config content path.
- Profile output avoids truncation and maintains endpoint response shape.

## Phase 4 — Query Pipeline from Summaries

### Scope
1. Add `summary_manager.py::query_from_summaries()`:
   - Input: user query + aggregated profile JSON + selected micro summaries.
   - Repo prefilter by query relevance before summary load.

2. Refactor `api_gateway.portfolio_query()`:
   - Use cached aggregated profile + micro summaries.
   - Avoid re-reading raw README/config blobs for query context.

### Acceptance Criteria
- Query path executes with summary-first context.
- Query latency decreases relative to baseline for same repo count.

## Phase 5 — Budget and Prompt Hardening

### Token Budget Targets

| Stage | Metadata | README | Config | Reserve | Total |
|---|---:|---:|---:|---:|---:|
| Repo micro-summary | 2k | 8k | 2k | 1k | 13k |
| Profile aggregation | 1k | 0 | 0 | 1k | 2k |
| Profile HTML formatting | 3k | 0 | 0 | 500 | 3.5k |
| Query from summaries | 2k | 0 | 0 | 1k | 3k |

### Output Token Caps
- Repo micro-summary: `max_completion_tokens=2000`
- Profile aggregation: `max_completion_tokens=3000`
- Profile HTML: `max_completion_tokens=2500`
- Query: `max_completion_tokens=2000`

### Prompt Constraints
- Explicit token and structure constraints in all summary prompts.
- Prioritization instruction when nearing token limit.
- Enforced sentence/section caps for profile formatting stage.

## Reliability, Migration, and Observability

### Required Reliability Rules
- All new generation steps must be idempotent by `username + repo + fingerprint`.
- Failed extraction/summarization must not block job completion for other repos.
- Endpoint fallback remains partial-but-successful when some repos are missing summaries.

### Required Schema/Validation Updates
- Add `summary_ready` to allowed `RepoSyncStatus` values.
- Extend discovered-path metadata fields for extraction status.
- Update related tests for status transitions and retrieval contracts.

### Observability Requirements
- Per-stage metrics: estimated input tokens, completion tokens, duration, retries, truncation warnings.
- Counters: repos processed, repos skipped, summary-ready ratio.

## Rollout Strategy

1. Behind feature flags for extraction and summary-first query/profile paths.
2. Canary with limited users/jobs.
3. Validate SLOs and quality against baseline logs.
4. Enable by default after thresholds are stable.

## Priorities

1. **Clean signal quality** from structured extraction and micro summaries.
2. **Latency reduction** through smaller prompt contexts and cache reuse.
3. **Low-risk migration** through compatibility-preserving endpoint behavior.