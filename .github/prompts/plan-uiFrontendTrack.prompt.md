## Plan: UI Frontend Track (Steps 5–8)

Four independent issues covering markdown standardisation, summary cache ownership, candidate-context enrichment, and deferred exploration tracks.

---

### Issue 5 — Standardise markdown summary presentation

**Goal**: Eliminate the duplicated `.markdown-content` CSS block and apply a consistent, accessible style to all summary surfaces.

**Background**: The `.markdown-content` rules in [ui/src/app/profile/profile.component.css](ui/src/app/profile/profile.component.css) and [ui/src/app/projects/project/project.component.css](ui/src/app/projects/project/project.component.css) are byte-for-byte identical. The AI response area in [ui/src/app/ai/ai.component.css](ui/src/app/ai/ai.component.css) has no equivalent rules, creating an inconsistent reading experience.

**Steps**
1. Move the shared `.markdown-content` block into [ui/src/styles.css](ui/src/styles.css) (global stylesheet, already loaded everywhere).
2. Remove the duplicate block from both component stylesheets.
3. Apply the same `.markdown-content` class to the AI response container in [ui/src/app/ai/ai.component.html](ui/src/app/ai/ai.component.html) so all three surfaces share the same typography rules.
4. Review and improve the rules while they are in one place:
   - Set an explicit heading scale (h1 → h6) using `rem`-based sizes relative to `--font-size-base`.
   - Ensure `blockquote` text contrast meets WCAG AA (replace `var(--muted)` with a higher-contrast token or an explicit `opacity` value).
   - Add `line-height` and `margin-bottom` rhythm for `p`, `li`, and `pre` so summaries don't feel cramped on mobile.

**Relevant files**
- [ui/src/styles.css](ui/src/styles.css)
- [ui/src/app/profile/profile.component.css](ui/src/app/profile/profile.component.css)
- [ui/src/app/projects/project/project.component.css](ui/src/app/projects/project/project.component.css)
- [ui/src/app/ai/ai.component.css](ui/src/app/ai/ai.component.css)
- [ui/src/app/ai/ai.component.html](ui/src/app/ai/ai.component.html)

**Verification**
1. Grep for `.markdown-content` in component CSS files — expect zero results.
2. Visually compare profile summary, repo detail summary, and AI response in browser: spacing, heading scale, and code block rendering should be equivalent.
3. Check blockquote contrast in both light and dark themes.

---

### Issue 6 — Consolidate summary caching into services

**Goal**: Move localStorage cache ownership for AI summaries out of page components and into the service layer so components only manage loading state.

**Background**: `loadProfileSummary()` in [ui/src/app/profile/profile.component.ts](ui/src/app/profile/profile.component.ts) owns the cache key `profile-summary-${username}-${jobId}` with a 24 h TTL and contains the full read-check → fetch → poll-fallback → write flow. The same pattern is duplicated in `loadReadmeSummary()` in [ui/src/app/projects/project/project.component.ts](ui/src/app/projects/project/project.component.ts) with key `readme-summary-${username}-${repoName}-${jobId}`. The services (`getCandidateSummary`, `getReadmeSummary`) currently re-throw errors for callers to handle, so caching today is entirely a component concern.

**Steps**
1. In [ui/src/app/services/profile.service.ts](ui/src/app/services/profile.service.ts): inject `CacheService`, build the cache key inside `getCandidateSummary(username, jobId)`, return the cached value when present, otherwise fetch and write to cache on success (24 h TTL). Keep the error path unchanged — callers still handle 404/503 for not-yet-ready polling.
2. In [ui/src/app/services/repo-bundle.service.ts](ui/src/app/services/repo-bundle.service.ts): same treatment for `getReadmeSummary(username, repoName, jobId)`. Remove the debug `console.log` calls already present in this method.
3. In both page components, strip the manual cache-check and cache-write blocks from `loadProfileSummary()` and `loadReadmeSummary()`. Components subscribe to the service observable; the service handles caching transparently.
4. Confirm that polling on `waitForFilesReady()` / `pollRepoReady()` still works — these poll until the service call succeeds (non-error), at which point caching is handled by the service automatically.

**Relevant files**
- [ui/src/app/services/profile.service.ts](ui/src/app/services/profile.service.ts)
- [ui/src/app/services/repo-bundle.service.ts](ui/src/app/services/repo-bundle.service.ts)
- [ui/src/app/services/cache.service.ts](ui/src/app/services/cache.service.ts)
- [ui/src/app/profile/profile.component.ts](ui/src/app/profile/profile.component.ts)
- [ui/src/app/projects/project/project.component.ts](ui/src/app/projects/project/project.component.ts)

**Verification**
1. Load a profile summary, reload the page — confirm the summary appears instantly (cache hit, no network request visible in DevTools).
2. Load a repo detail summary, reload — same check.
3. Confirm that invalidating cache (clear localStorage) and reloading triggers a fresh fetch and re-populates cache.
4. Confirm no `console.log` output appears when calling `getReadmeSummary()`.

---

### Issue 7 — Enrich CandidateContextService and move progress card

**Goal**: Extend `CandidateContext` to own per-candidate job/progress state, then surface a persistent progress card across route changes (no longer ephemeral in the landing component).

**Background**: `CandidateContext` in [ui/src/app/services/candidate-context.service.ts](ui/src/app/services/candidate-context.service.ts) currently holds only `{ username, skillsText? }`. Build progress (`buildProgress`, `statusMessage`, `jobStatus`) is ephemeral component state in [ui/src/app/landing/landing.component.ts](ui/src/app/landing/landing.component.ts) and disappears on navigation. The candidate list in [ui/src/app/shared/candidate-list.component.ts](ui/src/app/shared/candidate-list.component.ts) shows no build status or actions (refresh/delete).

**Steps**
1. Extend the `CandidateContext` interface to add optional fields: `jobId`, `buildStatus` (`'idle' | 'building' | 'ready' | 'failed'`), `buildProgress` (0–100), `buildMessage`.
2. Add `updateProgress(username, patch: Partial<CandidateContext>)` to `CandidateContextService`. On each call, upsert into the candidates list and persist to localStorage.
3. In `landing.component.ts`, replace the component-local `buildProgress` / `statusMessage` / `jobStatus` state updates with calls to `candidateContext.updateProgress()`. The landing component still owns the polling loop — it just delegates state storage to the service.
4. Update `candidate-list.component.ts` (or create a companion `candidate-card.component.ts`):
   - Show a progress bar or status badge when `buildStatus === 'building'`.
   - Show refresh and delete action icons per candidate.
   - Clicking refresh re-triggers `startBuild()` for that username.
   - Clicking delete calls `candidateContext.removeCandidate(username)`.
5. Ensure the progress card is visible on routes other than `/landing` (e.g., in the sidebar or nav) so recruiters can monitor background jobs while browsing results.

**Relevant files**
- [ui/src/app/services/candidate-context.service.ts](ui/src/app/services/candidate-context.service.ts)
- [ui/src/app/landing/landing.component.ts](ui/src/app/landing/landing.component.ts)
- [ui/src/app/shared/candidate-list.component.ts](ui/src/app/shared/candidate-list.component.ts)

**Verification**
1. Start a build from the landing page, navigate to `/profile` — progress card should still show live status.
2. Reload mid-build — progress state is restored from localStorage on boot.
3. Complete a build, then click the refresh icon from the candidate list — a new build should start without visiting landing.
4. Click delete — candidate disappears from list and localStorage.

---

### Issue 8 — Track: chat history and AI latency (exploration only)

**Goal**: Define the scope of changes needed for a stateful chat experience and streaming AI responses. No implementation in this issue — output is a focused discovery note for each track.

**Background**: `ask()` in [ui/src/app/ai/ai.component.ts](ui/src/app/ai/ai.component.ts) is fully stateless — each call replaces the previous answer with no history. The backend AI calls use `stream=False` in [api/v0.4.0/shared/src/foliohive_shared/ai/ai_assistant.py](api/v0.4.0/shared/src/foliohive_shared/ai/ai_assistant.py). Two separate exploration tracks:

**Track A — Chat history**
- Scope: in-component message list (prior Q+A pairs shown above the input), no backend changes required for MVP.
- Storage: localStorage array keyed by `username`.
- Conversation turns sent to backend: last N turns appended to the prompt as additional context (avoids backend session management).
- Risk: token budget — aggregate context size grows with history length; needs a rolling window or truncation strategy.
- Recommendation: implement as a localStorage-first feature with a max 10-turn rolling window before deciding whether backend session management is worth it.

**Track B — Streaming responses**
- Scope: change `stream=True` in `call_ai_api()` in `ai_assistant.py`, add a new SSE endpoint in `api_gateway.py`, update `assistant.service.ts` to consume the event stream via `EventSource` or `fetch`+`ReadableStream`.
- SSR (Angular Universal): already present as a dependency in `package-lock.json` but not configured. Low ROI for this architecture since the UI is a SPA + poll pattern — skip SSR in favour of streaming.
- Risk: Azure Functions Flex Consumption plan has a 230-second HTTP response timeout; streaming responses that exceed this will be cut off. Pin model + max-tokens to stay within the window.
- Recommendation: prototype streaming on the AI query endpoint first (most user-visible latency); extend to profile/readme summaries only if that succeeds.

**Relevant files**
- [ui/src/app/ai/ai.component.ts](ui/src/app/ai/ai.component.ts)
- [ui/src/app/ai/ai.component.html](ui/src/app/ai/ai.component.html)
- [ui/src/app/services/assistant.service.ts](ui/src/app/services/assistant.service.ts)
- [api/v0.4.0/shared/src/foliohive_shared/ai/ai_assistant.py](api/v0.4.0/shared/src/foliohive_shared/ai/ai_assistant.py)
- [api/v0.4.0/function-app/blueprints/api_gateway.py](api/v0.4.0/function-app/blueprints/api_gateway.py)

**Verification** (exploration criteria)
1. Document max token budget available for history context given current micro-summary sizes.
2. Confirm Azure Functions Flex Consumption HTTP timeout value and assess streaming viability.
3. Write a throwaway prototype for `stream=True` locally and measure latency improvement before committing to the endpoint change.
