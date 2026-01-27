# Recruiter-Focused Schema + API Standardization Plan

Date: 2026-01-27
Scope: `api/v0.3.0` + `ui`
Goal: Make the default candidate experience recruiter-friendly (fast scroll, low effort) while keeping diagnostics (API usage, traces) on an authenticated admin/monitoring page.

---

## What a recruiter wants (quick scroll)
Per repository card (minimum viable set):
- **Name**
- **Last pushed** (recency signal)
- **Top languages** (2–4) with percentages
- **Topics** (tags)
- **Popularity**: stars + forks (optionally watchers)
- **1–2 key links**: GitHub URL + homepage/demo (if present)
- Optional but useful: short description, archived/fork badges, license badge

Non-goals (default recruiter view):
- API usage diagnostics, trace IDs, raw errors, rate limits
- Full readme/config contents on list pages (only for repo drill-down)

---

## Current state (observed)
- Candidate bundle output is now usable (see logs), but fields are incomplete for recruiter UX.
- Table schema already stores some useful GitHub fields (e.g., topics, primary_language) but they are not returned in the candidate bundle.
- Build pipeline has intermediate states (`metadata_ready` vs `completed`). Recruiter view should show partial data early, but clearly label when still processing.

---

## Decisions needed (you said you need more info to decide)
Answer these to lock the design:
1. **Backwards compatibility**: do we keep legacy fields (`expected_repos`, etc.) for a transition period, or break cleanly? : No backward compatibility needed; recruiters are new users.
2. **Timestamp format**: should API return strict ISO-8601 everywhere? (recommended) : Yes, standardize to ISO-8601 in all API responses.
3. **Recruiter vs admin split**:
   - Do we add a separate `/admin/*` route group? : already in-place `ui/src/app/app.routes.ts` has admin routes.
   - Auth mechanism: SWA built-in auth, AAD, or custom JWT? : SWA built-in auth + linked backend. already in-place `ui/staticwebapp.config.json`
4. **Schema versioning**: prefer simple `schema_version: "v1"` or date-based (e.g., `2026-01-27`)? : Date-based versioning is more informative; use `2026-01-27`.
5. **Topics + languages limits**: max topics returned per repo (e.g., 10) and max languages (e.g., top 4)? : yes, limit topics to 10 and languages to top 4.

---

## Proposed standard API response envelope (simple)
Use the same envelope for all endpoints.

### Success
```json
{
  "ok": true,
  "data": { /* payload */ },
  "meta": {
    "api_version": "0.3.0",
    "schema_version": "v1",
    "request_id": "...",
    "server_time": "2026-01-27T08:26:32Z"
  }
}
```

### Error
```json
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "..."
  },
  "meta": { "api_version": "0.3.0", "schema_version": "v1", "request_id": "..." }
}
```

Notes:
- Keep `error.details` optional and only in non-prod / debug.
- Avoid returning trace IDs + API usage by default.

---

## Client-facing payloads (recruiter UX)

### 1) Candidate bundle (projects list)
Endpoint: `GET /candidate/{username}?job_id=...`

Return:
- `username`
- `job_id`
- `status`: `queued | syncing | metadata_ready | completed | failed`
- `fingerprint` (only non-null when `completed`)
- `last_modified`
- `repos`: array of `RepoCard`

`RepoCard` (minimum recruiter set):
- `name`
- `description`
- `urls`: `{ github, homepage? }`
- `stats`: `{ stars, forks }`
- `flags`: `{ fork, archived }`
- `timestamps`: `{ pushed_at, updated_at, created_at }`
- `topics`: string[]
- `languages`: `{ top: [{ name, pct }], total_bytes? }`

Implementation note: this can be built entirely from existing table rows (RepoGitHubMetadata + RepoLanguages).

### 2) Job status (polling)
Endpoint: `GET /candidate/{username}/status?job_id=...`

Return:
- `job_id`, `username`, `status`
- `metadata_ready`, `files_ready`
- `progress`: `{ total, completed, percentage, pending, synced, cached, failed }`
- `timestamps`: `{ created_at, completed_at? }`
- `repo_details_preview`: first N repo names by status

### 3) Repo files (project detail)
Endpoint: `GET /candidate/{username}/{repo}/files?type=readme|config|all`

Return:
- `username`, `repo`, `fingerprint`
- `files`:
  - `primary_readme` (string)

Rename intention (later): this endpoint should not claim “AI summary” unless it actually returns a summary.

### 4) AI query (ai page)
Endpoint: `POST /ai`

Return:
- `answer` (string)
- `citations`: list of repos/fields used (for trust)
- `used_repos`: list of repo names (optional)
- `generated_at`

Note: current AI code is stale; this plan only defines a target contract.

---

## Admin/monitoring data (authenticated): 
`ui/staticwebapp.config.json` , `ui/src/app/app.routes.ts`
Keep behind auth (SWA + linked backend).
Suggested admin endpoints/panels:
- API usage rows (RepoAPIUsage)
- Traces/request correlation
- Queue backlog + worker failures
- Job timelines and per-repo errors

---

## Table schema opportunities (fields to expose or add)

### Already stored but not returned (high value for recruiters)
- `topics`
- `primary_language`
- `license_name`

### Useful fields often available from GitHub API (may already be in fetch payload; decide whether to store)
- `default_branch`
- `visibility` / `is_private`
- `repo_size_kb`
- `open_issues_count` (already)
- `has_pages`, `is_template`, `has_discussions` (nice-to-have)

### Languages
- Return percentages (already computed in table rows) instead of only bytes.

### Timestamps
- Standardize to strict ISO-8601 in API responses.

---

## Work breakdown (later implementation steps)
1. Define TypeScript interfaces for `RepoCard` and common envelope.
2. Update API gateway endpoints to return the envelope + recruiter payload shape.
3. Update UI services to unwrap the envelope consistently.
4. Update projects list card mapping to use the new fields (topics, languages pct, pushed_at).
5. Create admin-only monitoring page for API usage + traces.
6. Add minimal tests for schema shape and table deserialize/serialize.

---

## Open questions / risks
- Current timestamp sanitization (Azure-safe) leaks into responses; should be fixed.
- Some endpoints and cache keys appear mismatched (repo files caching vs retrieval). This plan does not fix it yet.
- RepoFileTypes row shape appears inconsistent between dataclass intent and stored JSON; needs alignment before using for recruiter “signals”.
