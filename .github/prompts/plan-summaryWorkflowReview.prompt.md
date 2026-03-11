# Summary Workflow Review & Bug Fixes

Three distinct workflows share similar patterns but each has its own critical bugs and grey areas.

---

## Workflow 1: `loadReadmeSummary` → `get_repo_summary` → `expand_repo_micro_summary` → `expand_repo`

**Bug 1 — Critical (Known): Field name mismatch**
Backend `get_repo_summary` returns `summary_html` in the payload. Both UI definitions of `ReadmeSummaryResponse` — in `assistant.service.ts` and `repo-bundle.service.ts` — declare only `readme_summary_html`. `loadReadmeSummary` always reads `undefined`, meaning nothing ever renders.
Fix: rename the payload key in `get_repo_summary` from `summary_html` → `readme_summary_html`.

**Bug 2 — Critical: `expand_repo_micro_summary` silently drops `username`**
`expand_repo_micro_summary` calls `ai_assistant.expand_repo()` without the required `username` keyword argument. `expand_repo` declares `username: str` as mandatory (`*` forces keyword-only). This raises a `TypeError` at runtime on every call that isn't a cache hit.
Fix: pass `username=self.username` in the call inside `expand_repo_micro_summary`.

**Bug 3 — Minor: Wrong error message in `expand_repo`**
A successful-but-empty result returns `"_Failed to generate profile summary._"` — copy-pasted from `summarize_profile`. Should say repo/readme summary.

**Smell: Duplicate `ReadmeSummaryResponse`**
The interface is declared twice — in `assistant.service.ts` and `repo-bundle.service.ts`. `project.component.ts` uses the one from `repo-bundle.service.ts`. Consolidate to one canonical location.

---

## Workflow 2: `loadSummary` → `get_profile_summary` → `get_or_generate_profile_summary` → `summarize_profile`

**Bug 4 — Stale docstring**
`get_or_generate_profile_summary` docstring states "Returns: Dict with summary_html" — the method actually returns `summary_markdown`. Misleading for callers.

**Opportunity: No server-side caching**
`get_or_generate_profile_summary` regenerates the full AI narrative on **every** `GET /candidate/{username}/summary` request. The client has a 24-hour TTL cache, but the server has none. A forced refresh, a second device, or a cache miss triggers a full `aggregate_micro_summaries` + AI generation round-trip. Should cache by `(username, job_id)` or fingerprint hash.

This workflow is otherwise structurally sound — the `summarize_profile` return type (`str`) is consistent with how it's consumed, and `username` is correctly passed through `self.username`.

---

## Workflow 3: `ask()` → `portfolio_query` → `get_or_generate_query_response` → `summarize_query`

**Bug 5 — Critical: `summarize_query` return type is wrong, breaking `get_or_generate_query_response`**
`summarize_query` is annotated `-> Dict[str, Any]`, but:
- Happy path: `return result` where `result` is the raw `str` from `call_ai_api`
- Empty result: `return "_Failed to generate query summary._"` — a `str`
- Exception: `return f"_Error generating query summary: {str(e)}_"` — a `str`
- Only the `not self.client` branch correctly returns a `dict`

Then `get_or_generate_query_response` does `result["metadata"] = metadata` — which always throws `TypeError: 'str' object does not support item assignment` on a successful AI call. **The query workflow never returns a result.**

**Bug 6 — Critical: `username` never passed to `summarize_query`**
`get_or_generate_query_response` calls `self.ai_assistant.summarize_query(query=..., profile=..., aggregate=..., ...)` — omitting the required `username` parameter. This is a second `TypeError` that would fire before Bug 5 even activates.
Fix: pass `username=self.username`.

**Bug 7 — Critical: Backend response shape doesn't match UI contract**
`AIAssistantResponse` expects `{ response: string, repositories_used: [...], total_repositories, query }`. The backend:
- Never sets a `response` key anywhere in `get_or_generate_query_response`
- Never sets `repositories_used` or `total_repositories`
- `portfolio_query` just does `response.update({"username": ..., "job_id": ...})` and returns whatever the AI raw text is

Even if Bugs 5 and 6 are fixed and `summarize_query` returns markdown text, it would need to be wrapped into `{ "response": <text>, "repositories_used": [], ... }` before being sent. The UI reads `res.response` which would be `undefined`.

**Opportunity: No query caching**
Same as profile: repeated identical queries regenerate every time. Cache by `(username, hash(query), job_id)` with a reasonable TTL.

---

## Summary of Issues by Severity

| # | Severity | Location | Issue |
|---|----------|----------|-------|
| 1 | Critical | `api_gateway.py` | `summary_html` vs `readme_summary_html` field name mismatch |
| 2 | Critical | `summary_manager.py` | `expand_repo_micro_summary` missing `username` kwarg for `expand_repo` |
| 5 | Critical | `summary_manager.py` | `get_or_generate_query_response` crashes on `result["metadata"]` — `result` is a `str` |
| 6 | Critical | `summary_manager.py` | `username` not passed to `summarize_query` |
| 7 | Critical | `api_gateway.py` + `assistant.service.ts` | Query response shape never matches `AIAssistantResponse` contract |
| 3 | Minor | `ai_assistant.py` | Wrong copy-pasted error message in `expand_repo` |
| 4 | Minor | `summary_manager.py` | Stale docstring `summary_html` → `summary_markdown` |
| — | Smell | `assistant.service.ts` / `repo-bundle.service.ts` | `ReadmeSummaryResponse` defined twice |
| — | Opportunity | `summary_manager.py` | No server-side caching for profile summary or query responses |

---

## Steps

1. Fix field name: rename `summary_html` → `readme_summary_html` in `get_repo_summary` payload in `api_gateway.py`
2. Fix missing `username`: in `expand_repo_micro_summary`, add `username=self.username` to the `expand_repo` call
3. Fix `summarize_query` return type: change it to consistently return a structured dict `{"response": <text>}` instead of a raw string, and fix all error branches to match
4. Fix missing `username` in query: in `get_or_generate_query_response`, add `username=self.username` to the `summarize_query` call
5. Fix query response shape: `get_or_generate_query_response` must assemble `{"response": ..., "repositories_used": [], "total_repositories": ..., "query": ...}` before returning to match `AIAssistantResponse`
6. Fix wrong error message in `expand_repo`
7. Fix stale docstring in `get_or_generate_profile_summary`
8. Remove duplicate `ReadmeSummaryResponse` — keep in `repo-bundle.service.ts`, remove from `assistant.service.ts`
9. (Optional) Add server-side caching in `get_or_generate_profile_summary` and `get_or_generate_query_response` keyed by `(username, job_id)`

---

## Verification

- Workflow 1: `GET /candidate/{username}/{repo}/readme-summary` → response contains `readme_summary_html` → project detail view renders HTML
- Workflow 3: `POST /api/ai` → response contains `response`, `repositories_used`, `total_repositories`, `query` → AI view renders markdown
