# Candidate Profile Summary Page — API Contract (MVP)

Date: 2026-02-04

## Overview
This doc defines the minimal response shapes for the candidate profile/summary feature.

Base envelope matches existing API gateway:
- `status`: `"success" | "error"`
- `ok`: boolean
- `data`: payload
- `meta`: `{ api_version, schema_version, request_id, server_time }`

## GET /candidate/{username}/profile
### Request
- Method: `GET`
- Route: `/candidate/{username}/profile`
- Headers (optional):
  - `X-Session-Id`
  - `X-Request-Id`

### Response: 200
`data`:
```json
{
  "username": "octocat",
  "github_profile": {
    "username": "octocat",
    "github_id": 1,
    "name": "The Octocat",
    "bio": "...",
    "company": "...",
    "location": "...",
    "blog": "...",
    "twitter_username": "...",
    "avatar_url": "...",
    "html_url": "...",
    "public_repos": 8,
    "followers": 100,
    "following": 0,
    "github_created_at": "...",
    "github_updated_at": "...",
    "fingerprint": "md5...",
    "cached_at": "..."
  },
  "job_metadata": {
    "job_id": "...",
    "status": "metadata_ready",
    "created_at": "...",
    "updated_at": "..."
  },
  "statistics": {
    "repo_count": 12,
    "stars_total": 42,
    "forks_total": 7,
    "top_languages": [
      {"language": "Python", "bytes": 123456},
      {"language": "TypeScript", "bytes": 65432}
    ],
    "topics": ["azure", "angular", "functions"]
  }
}
```

### Errors
- 400: username missing
- 404: candidate has no synced data (optional; or return empty `job_metadata`)
- 500: internal

## GET /candidate/{username}/summary (optional in MVP)
### Response: 200
`data`:
```json
{
  "username": "octocat",
  "summary_html": "<h3>...</h3><p>...</p>",
  "based_on": {
    "profile_fingerprint": "md5...",
    "job_id": "..."
  }
}
```

## Notes for UI
- UI should treat `job_metadata` as optional (candidate may not have run sync yet).
- UI should treat `github_profile` as optional (GitHub API may fail/rate-limit); still show repo/job data.
