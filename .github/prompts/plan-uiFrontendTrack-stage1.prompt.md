## Plan: UI Frontend Track

### Issue 1 — Enrich CandidateContextService and move progress card

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