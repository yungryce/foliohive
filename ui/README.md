# FolioHive UI

Angular frontend for recruiters to inspect a candidate's GitHub portfolio, browse repository metadata, and read AI-generated summaries.

## Overview

The UI is a standalone Angular application that talks to the Azure Functions backend under `/api`.

Main user flows:

1. Enter a GitHub username and start a build.
2. Poll until metadata is ready.
3. Browse candidate profile and repositories.
4. Open an individual repository summary.
5. Ask AI questions against the candidate portfolio.

## Current Route Structure

Defined in `src/app/app.routes.ts`:

- `/` -> landing page
- `/profile` -> candidate profile
- `/projects` -> repository list
- `/projects/:repo` -> single repository detail
- `/ai` -> AI portfolio query page
- `/dashboard` -> dashboard, protected by `authGuard`
- `/admin/*` -> admin area, protected by `adminGuard`

Important: the active candidate username is carried by client-side context and storage, not by route params.

## App Structure

Application root:

`src/app/`

Key areas:

- `landing/`: candidate search and refresh trigger
- `profile/`: candidate profile and aggregated summary
- `projects/`: repository list
- `projects/project/`: single repo detail and summary
- `ai/`: AI portfolio query UI
- `admin/`: admin shell and monitoring area
- `dashboard/`: dashboard view
- `guards/`: route guards
- `services/`: API and state-related services

## UI Services

Services live under `src/app/services/`.

### repo-bundle.service.ts

Responsibilities:

- trigger a build via `startBuild()`
- poll job status via `getJobStatus()`
- load repo list via `getCandidateMetadata()`
- load single repo metadata via `getCandidateRepoMetadata()`
- load repo summary via `getReadmeSummary()`
- load recent session candidates via `getSessionCandidates()`

Backend routes used:

- `POST /candidate/{username}/refresh`
- `GET /candidate/{username}/status`
- `GET /candidate/{username}`
- `GET /candidate/{username}/{repo}/metadata`
- `GET /candidate/{username}/{repo}/readme-summary`
- `GET /session/candidates`

### profile.service.ts

Responsibilities:

- load candidate profile metadata
- load aggregated profile summary

Backend routes used:

- `GET /candidate/{username}/profile`
- `GET /candidate/{username}/summary`

### assistant.service.ts

Responsibility:

- send a portfolio query with `{ username, query }`

Backend route used:

- `POST /ai`

Note: the backend currently expects query params while this service sends JSON body data. That mismatch is a known backend bug.

### job-polling.service.ts

Responsibilities:

- poll until a job is terminal
- wait for `metadata_ready`
- wait for `summary_ready`
- wait for a specific repo to reach `summary_ready`

Default polling options:

- `intervalMs: 3000`
- `maxAttempts: 40`
- `timeoutMs: 120000`

### candidate-context.service.ts

Responsibilities:

- track the active candidate username
- persist candidate context in `localStorage`
- synchronize recent candidates from the session endpoint

### Other Services

- `cache.service.ts`: in-memory client cache
- `auth.service.ts`: auth-related state
- `config.service.ts`: runtime config handling
- `session-id.service.ts`: session ID management
- `request-id.interceptor.ts`: injects `X-Request-Id`
- `session-id.interceptor.ts`: injects `X-Session-Id`

## Page Responsibilities

### Landing

- validates GitHub username input
- starts the refresh flow
- stores and reuses recent candidate context

### Profile

- displays GitHub profile metadata
- displays portfolio statistics
- renders the aggregated markdown summary returned by the backend

### Projects

- waits for metadata readiness
- displays repository cards and repo-level metadata
- navigates to an individual repo view

### Project Detail

- displays a single repo's metadata
- polls until the repo summary is ready
- renders the expanded repo summary returned by the backend

### AI

- submits portfolio-wide AI questions
- renders query responses derived from cached repo micro-summaries

### Admin and Dashboard

- provide internal monitoring and protected views

## Local Development

Prerequisites:

- Node.js 18+
- npm
- running backend at `http://localhost:7071/api`

Install and run:

```bash
cd ui
npm install
npm start
```

Or start the full local stack from the repo root:

```bash
./run-dev-session.sh
```

Local UI URL:

`http://localhost:4200`

## Environment

Environment files live in `src/environments/`.

Relevant frontend settings:

- `apiBaseUrl`
- `enableDebugLogging`
- `pollingIntervalMs`
- `maxPollingAttempts`

Development defaults point at:

`http://localhost:7071/api`

## Build and Test

Common commands:

```bash
npm start
npm run build
npm test
```

Production build output is generated under Angular's configured `dist` folder.

Current testing is still lightweight and mostly manual, aligned with the project's proof-of-concept stage.

## Deployment

The UI is intended for Azure Static Web Apps.

Relevant files:

- `angular.json`
- `staticwebapp.config.json`

## Known Limitations

- The backend AI query endpoint currently disagrees with the UI request shape.
- Some admin child routes still point to placeholder monitoring components.
- Candidate identity is context-driven rather than encoded in route params, so direct deep-link behavior depends on client-side stored state.

## Related Files

- App routes: `src/app/app.routes.ts`
- Services: `src/app/services/`
- Environment config: `src/environments/`
