# Frontend Split & Cloudfolio UI Alignment Plan

**Date**: December 6, 2025  
**Status**: Living Plan (kept in lockstep with queue/table/blob backend)  
**Scope**: `portfolio/` (developer showcase SWA), `cloudfolio-ui/` (recruiter SWA), shared Azure Static Web App + Function App wiring

---

## 1. Why This Exists

- The backend already runs on the queue-first architecture captured in `plan-dataProcessingArchitecture.prompt.md` (API Gateway + sync/merge/training workers, unified `table_manager` + `cache_manager`).
- The legacy Angular app still mixes a static portfolio with recruiter tooling, leaking old synchronous assumptions and confusing personas.
- This plan keeps the frontends aligned with the **current** architecture: Cloudfolio UI orchestrates asynchronous bundle builds (via `/bundles/{username}/refresh` → queues → Tables) while the personal portfolio stays frozen to `yungryce`.

Key objectives:
- Separate branding and release cadence (portfolio stability vs recruiter iteration).
- Ensure Cloudfolio UI speaks the same storage/queue language as the Functions (session headers, job polling, table-backed metadata).
- Strip obsolete steps (legacy orchestrator, embedded assistant component) and document only what is required to ship against today’s platform.

---

## 2. Current Constraints (trimmed to what still matters)

| Issue | Impact | Fix in this plan |
|-------|--------|------------------|
| Monolithic Angular app mixes portfolio + recruiter UX | Risky deployments, confusing navigation | Extract dedicated SWAs with independent pipelines |
| Hardcoded username in recruiter chat | Recruiters cannot evaluate other candidates | New Cloudfolio UI search + job orchestration pipeline |
| Frontend unaware of async queue flow | Loading spinners lie, no job IDs | Adopt shared polling pattern + `X-Session-Id` headers |
| Analytics blind spots | Cannot prove engagement | Use Application Insights with anonymous session IDs |

---

## 3. Target Architecture (aligned to current backend)

```
User ↴
 ├─ portfolio.yungryce.dev  ─► Portfolio SWA  ─► /api/bundles/yungryce (read-only Tables)
 └─ cloudfolio.app          ─► Cloudfolio SWA ─► API Gateway /bundles/{username} + /chat/{username}
                                                       │
                                                       ▼
                         Queue-backed Functions (api-gateway, sync-worker, merge-worker, training-worker)
                             │         │            │
                             ▼         ▼            ▼
                   Azure Storage Queues  Azure Tables via table_manager  Azure Blobs (ephemeral + model artifacts)
```

Design rules:
- Both SWAs live on the Azure Static Web App free tier, linked to the **same** Function App backend via `linkedBackends`.
- Cloudfolio UI never talks directly to GitHub; it sends refresh/chat/status calls to the API Gateway, which already orchestrates queues + Tables.
- Portfolio SWA is deliberately simple: cached bundle fetch, no refresh, no chat.

---

## 4. Implementation Phases 

### Phase 1 – Extract Portfolio SWA (done once, minimal churn)
- Delete recruiter routes/components from `portfolio/src/app`.
- Hardcode `RepoBundleService` to `yungryce` and rely on cached Table data via API Gateway.
- Keep SWA config simple: `/api/*` rewrites to Function App, 1h cache headers for HTML assets only.
- Outcome: the developer site deploys independently and never interferes with recruiter UX.

### Phase 2 – Scaffold Cloudfolio UI (Angular 18)
- Create `cloudfolio-ui/` workspace with standalone components.
- Core features in this phase:
  1. **Search view** with username validation and call to `POST /bundles/{username}/refresh?force=true`.
  2. **SessionService** storing `{sessionId, recentSearches, favorites}` in `localStorage`.
  3. **HTTP interceptor** adding `X-Session-Id` to every request (mirrors backend telemetry expectations).
- Bump `staticwebapp.config.json` to allow anonymous `/api/bundles/*`, `/api/status/*`, `/api/chat/*` routes and to disable caching for HTML.

### Phase 3 – Chat + Polling Experience
- Implement chat component that:
  - Polls `GET /bundles/{username}` first. If cache missing, fallbacks to job status polling (`GET /status/{job_id}`) using the queue-based pattern from `plan-dataProcessingArchitecture`.
  - Shows deterministic progress (based on Table completion counts) rather than fake timers.
  - Sends prompts via `POST /chat/{username}` and renders markdown responses (sanitized via `dompurify`).
- Track `candidate_search` and `chat_query` events with Application Insights using the same telemetry schema as the Functions (`session_id`, `username`, `job_id`).

### Phase 4 – Deploy & Wire Observability
- Add SWA Bicep modules for both apps plus `linkedBackends` references to the API Gateway.
- CI/CD: GitHub Actions builds Angular artifacts and calls `Azure/static-web-apps-deploy@v1`; no API build step.
- Observability checklist:
  - Application Insights workbook for DAU, search→chat conversion, queue latency overlay (pulls from existing telemetry).
  - SWA diagnostics to monitor 429s or backend connectivity issues.

### Phase 5 – Portfolio API Alignment
- Update portfolio `RepoBundleService` to use the same retry + polling helpers as Cloudfolio UI, ensuring the showcase stays compatible if the backend invalidates caches.
- Keep UI copy simple (“Last refreshed via recruiter pipeline”).

### Phase 6 – Optional Auth (only if recruiters demand persistence)
- Cloudfolio UI already sends anonymous session IDs. If premium features are requested, enable Google auth in SWA config and add authenticated-only API routes (`/api/history/*`, `/api/export/*`).
- This phase is a toggle, not part of the baseline rollout.

---

## 5. Deliverables Per App

| Area | Portfolio SWA | Cloudfolio UI SWA |
|------|---------------|-------------------|
| Persona | Developer branding only | Recruiters evaluating any GitHub username |
| Data sources | `GET /bundles/yungryce` (Tables via API Gateway) | Full bundle lifecycle + chat (`/bundles`, `/status`, `/chat`) |
| Storage touchpoints | None beyond HTTP | Anonymous session storage + Application Insights events |
| Release cadence | Monthly | Weekly or faster |
| Dependencies | Static assets, no queues | Must follow queue/table contracts defined in shared prompts |

---

## 6. Testing & Validation
- **Local**: `npm run test` within each Angular app plus `func start` for API Gateway. Use Azurite to simulate Table + Queue dependencies; Cloudfolio UI should deal with delayed bundles the same way as production.
- **Integration**: `apps/tests/integration/test_cache_sync.py` already validates queue/Tables; add frontend cURL smoke tests in `tests/e2e_curl_tests.sh` to verify SWA → API Gateway paths.
- **Telemetry audits**: verify `session_id` flow by correlating Cloudfolio UI events with API Gateway traces in Application Insights.

---

## 7. Risks (current-only)

| Risk | Mitigation |
|------|------------|
| Recruiter UI forgets to honor queue latency (infinite spinner) | Shared polling utility that inspects `JobSessions` row timestamps before claiming completion |
| SWA ↔ Function App drift (routes renamed) | Keep route declarations in `staticwebapp.config.json` sourced from shared constants file; CI fails if env vars missing |
| Telemetry gaps if `localStorage` blocked | Gracefully fall back to in-memory session IDs; log warning banner for private browsing |
| Duplicate deployments | Each SWA pipeline scoped to app directory paths to avoid needless builds |

---

## 8. Next Actions
1. Confirm `cloudfolio-ui/` repo structure aligns with Angular CLI 18 and integrate Tailwind once (avoid duplicative styling guidance here).
2. Implement shared TypeScript SDK inside `cloudfolio-ui/src/app/services/cloudfolio-api.service.ts` that mirrors backend DTOs (reuse Pydantic schemas as reference to avoid drift).
3. Land Bicep modules + SWA pipelines so deployments are reproducible.
4. Update `plan-sharedArchitecture.prompt.md` references (already noted in data-processing plan) once `table_manager` TypeScript typings are consumed client-side, if ever needed.

This trimmed plan intentionally references only the modern queue/table/blob architecture; no steps rely on the deprecated monolithic orchestrator.
