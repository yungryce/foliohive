# Candidate Profile Summary Page (MVP, on-demand)

Date: 2026-02-04

## Goal
Add a minimal candidate profile/summary capability that:
- Fetches GitHub user profile via `GET https://api.github.com/users/{username}` on-demand.
- Aggregates existing repo/job metadata already stored in Azure Tables.
- Uses a **fingerprint** to avoid redundant GitHub profile refetches.
- Exposes API endpoints for UI consumption (profile + optional AI summary).

## Non-goals (MVP)
- No background refresh pipeline for user profiles.
- No complex “diff” logic across historical profiles.
- No heavy UI polish; just a functional profile page.

## On-demand strategy (minimum viable)
1. API gateway receives `GET /candidate/{username}/profile`.
2. Try to read cached profile from Table Storage (`UserProfile` table).
3. If missing or stale (fingerprint mismatch/TTL), call GitHub `GET /users/{username}`.
4. Persist fresh profile into `UserProfile` and return aggregated payload.

Notes:
- “Stale” for MVP can be defined as **time-based TTL** (e.g., 6h) *and/or* fingerprint mismatch.
- Fingerprint is computed from stable user profile fields (see Fingerprint strategy).

## Data model changes (Table Storage)
### A) Add `UserProfile` table (new)
Rationale: integrate with `table_manager.py`, keep UI reads fast, reduce GitHub API calls.

#### Table name
- Add to `TableNames`:
  - `user_profile = "UserProfile"`

#### Row schema
PartitionKey / RowKey pattern:
- `PartitionKey = username`
- `RowKey = "profile"`

Fields (keep minimal, align with GitHub API fields):
- `username` (PartitionKey)
- `profile_key` (RowKey)
- `github_id` (int)
- `name` (str?)
- `bio` (str?)
- `company` (str?)
- `location` (str?)
- `blog` (str?)
- `email` (str?)
- `twitter_username` (str?)
- `avatar_url` (str?)
- `html_url` (str?)
- `public_repos` (int)
- `public_gists` (int)
- `followers` (int)
- `following` (int)
- `github_created_at` (iso str?)
- `github_updated_at` (iso str?)
- `fingerprint` (str)
- `cached_at` (iso str)
- `updated_at` (iso str)

Implementation notes:
- Keep string payload sizes small (fits Azure Table string caps).
- Do not store full JSON blob as a single field in MVP.

### TableManager API (new methods)
Add to `TableManager`:
- `upsert_user_profile(row: UserProfileRow) -> None`
- `get_user_profile(username: str) -> Optional[Dict[str, Any]]`
- `_deserialize_user_profile(entity: Dict[str, Any]) -> Dict[str, Any]`

## Fingerprint strategy (required)
Use `FingerprintManager` to compute a user-profile fingerprint.

### B) Extend `FingerprintManager` (minimal)
Add method:
- `generate_user_profile_fingerprint(user_profile: Dict[str, Any]) -> str`

Recommended fingerprint inputs (stable + change-indicating):
- `id`
- `updated_at`
- `public_repos`, `followers`, `following` (optional)
- `name`, `company`, `location` (optional; include if you want UI to refresh when these change)

Example fingerprint payload:
```python
{
  "id": profile.get("id"),
  "updated_at": profile.get("updated_at"),
  "public_repos": profile.get("public_repos"),
  "followers": profile.get("followers"),
  "following": profile.get("following"),
}
```

MVP staleness check:
- If no row in table ⇒ fetch from GitHub.
- If row exists and `now - cached_at < TTL` ⇒ use row.
- Else fetch from GitHub, recompute fingerprint; if fingerprint unchanged, just refresh `cached_at`.

## GitHub integration changes
### GitHubAPI (`github_api.py`)
Add:
- `get_user_profile(username: str) -> Optional[Dict[str, Any]]`
  - Calls endpoint `users/{username}`.

### GitHubRepoManager (`github_repo_manager.py`)
Add:
- `get_user_profile(username: Optional[str] = None) -> Optional[Dict[str, Any]]`
  - Simple wrapper around `GitHubAPI.get_user_profile()`.
  - Optional caching via `cache_manager.cache_decorator` is acceptable but not required if Table Storage caching is implemented.

## API Gateway changes
### Endpoint: `GET /candidate/{username}/profile`
Responsibilities:
- Correlation tracing via `_get_trace_context`.
- Fetch latest job metadata via `_fetch_candidate_jobs`.
- Query repo rows via `_query_repo_rows`.
- Query repo languages via `table_manager.query_repo_languages(job_id)`.
- Load or refresh `UserProfile` from Table Storage using fingerprint + TTL logic.
- Return aggregated profile payload.

Aggregation output (MVP):
- `username`
- `github_profile` (subset from table)
- `job_metadata` (latest)
- `statistics`:
  - `repo_count`
  - `stars_total`
  - `forks_total`
  - `top_languages` (by bytes)
  - `topics` (distinct)

### Endpoint: `GET /candidate/{username}/summary` (optional but aligns with workflow)
Responsibilities:
- Calls the profile aggregation logic above.
- Selects top repos (e.g., by stars) from repo rows.
- Calls `AIAssistant.generate_profile_summary(...)` (new method) to produce HTML.
- Cache-control: `no-cache` for now (or add separate summary table later).

## UI (minimal)
- Add route: `/profile/:username`.
- Add service methods:
  - `getCandidateProfile(username)` → hits `/candidate/{username}/profile`.
  - `getCandidateSummary(username)` → hits `/candidate/{username}/summary`.
- Render:
  - avatar, name, bio, location/company, stats, top languages.
  - summary HTML (when available).

## Implementation steps (ordered)
1. Update `cloudfolio_shared/table/table_manager.py`
   - Add `UserProfileRow`, `TableNames.user_profile`, CRUD methods.
2. Update `cloudfolio_shared/cache/fingerprint_manager.py`
   - Add `generate_user_profile_fingerprint`.
3. Update `cloudfolio_shared/github/github_api.py`
   - Add `get_user_profile`.
4. Update `cloudfolio_shared/github/github_repo_manager.py`
   - Add `get_user_profile` wrapper.
5. Update API gateway `function-app/blueprints/api_gateway.py`
   - Add `/candidate/{username}/profile`.
   - Add `/candidate/{username}/summary` (optional).
   - Add shared helper `_build_profile_statistics`.
6. UI
   - Add profile component + route.
   - Add service methods and minimal template.

## Testing / verification
- Add/extend integration tests (if feasible) to hit new endpoints with mocked GitHub calls.
- Local manual test:
  - Trigger refresh → wait for metadata.
  - Call `/candidate/{username}/profile` and confirm payload includes job + repo stats.
  - Call twice and confirm second call avoids GitHub call when within TTL (log-based verification).

## Future enhancements (post-MVP)
- Persist AI summary in a new `CandidateSummary` table with TTL + fingerprint.
- Background refresh job (daily) for active candidates.
- Add richer aggregation (recent activity, pinned repos via GraphQL, etc.).
