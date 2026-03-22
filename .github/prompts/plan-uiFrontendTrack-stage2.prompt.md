## Plan: UI Frontend Track


### Issue 2 — Track: chat history and AI latency (exploration only)

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
