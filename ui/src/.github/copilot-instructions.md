
# AI Coding Agent Guide for This Codebase

Purpose: Enable AI agents to quickly ship safe, maintainable changes by documenting essential architecture, workflows, and conventions. This is an Angular SPA (17+) that communicates with an Azure Functions API.

## Big Picture Architecture
- Angular 17+ SPA, entry: `main.ts` → `bootstrapApplication(AppComponent, appConfig)`
- Routing (`app/app.routes.ts`):
  - `/` → `HomeComponent`
  - `/projects` → `ProjectsComponent` (repo list/grid)
  - `/projects/:repo` → `ProjectComponent` (single repo detail)
  - `/assistant` → `AssistantComponent` (AI Q&A)
  - `**` → redirect to `/`
- API base URL is set in `ConfigService.apiUrl` from `environments/*`:
  - dev: `http://localhost:7071/api`
  - prod: `/api`
- Styling: Tailwind v4 via `@import 'tailwindcss'` in `styles.css`, with CSS variables for design tokens. Use utility classes like `bg-[var(--bg)]`.

## Refactoring Guide (Current Direction)
- All repo bundle logic is in `src/app/projects` (deprecating `src/projects-old`).
- Single repo bundle logic is in `src/app/projects/project` (deprecating `src/projects-old/project-about`).
- AI assistant logic is in `src/app/assistant` (deprecating `src/portfolio-assistant`).
- API endpoints:
  - `api/function-app.get_repo_bundle` → consumed by `ProjectsComponent` (list)
  - `api/function-app.get_single_repo_bundle` → consumed by `ProjectComponent` (detail)
  - `api/function-app.portfolio_query` → consumed by `AssistantComponent`

## Key Files and Directories
- Bootstrapping: `main.ts`, `app/app.config.ts`
- Routing: `app/app.routes.ts`
- Shell: `app/app.component.*`
- Environments: `environments/environment(.development).ts`
- Services: `app/services/*` (Config, RepoBundle, Assistant, Cache)
- Features:
  - Projects list: `app/projects/projects.component.*`
  - Project detail: `app/projects/project/project.component.*`
  - AI assistant: `app/assistant/assistant.component.*`

## Service/Data Patterns
- `RepoBundleService`:
  - `getUserBundle(username, useCache?)` → `{ username, data: any[] }`, cached ~10 min
  - `getUserSingleRepoBundle(username, repo, useCache?)` → `{ username, repo, data: any }`, cached
  - Always unwrap `{ status, data }` responses, fallback to safe defaults
- `AIAssistantService`:
  - `askPortfolio({ query, username? })` → POST `${apiUrl}/ai`, returns `{ response: markdown, repositories_used, total_repositories, query }`
  - Errors return canned message, never throw
- `ConfigService`: Source of truth for `apiUrl` (never hardcode)
- `CacheService`: In-memory TTL cache, default 15 min, keys like `bundle-${username}`

## UI/ViewModel Conventions
- Projects list/grid:
  - View model via `toCardVM`: title from `name`, updated from `metadata.updated_at||pushed_at`, type/description from `repoContext.*` or `project_identity.*` (fallbacks), primary stack from `repoContext.tech_stack.primary`, language percentages from `languages` (bytes → %)
  - Filters: search, language, technology, show/hide forks; sorting by updated/name with asc/desc toggle
  - Accessibility: theme toggle uses `aria-pressed`, language bar uses `role="progressbar"`, cards are `<article>` with clear titles
- Project detail:
  - Single repo bundle mapped to `RepoDetailVM`, README rendered as markdown, TOC built from headings with stable slug IDs
- Assistant:
  - Render markdown using `marked` → `DOMPurify` → `bypassSecurityTrustHtml` (always sanitize)

## Theming
- Global toggle via `dark` class on `<html>`, persisted in `localStorage.theme`
- Per-component: read `localStorage.theme` or `prefers-color-scheme`, set `this.theme`, call `applyTheme`
- Tokens: `--bg`, `--fg`, `--muted`, `--border`, `--card`, `--soft`, `--primary`, `--primary-foreground`, `--chip-bg`, `--chip-fg`, `--ring` (see `styles.css`)

## Integration Points (API Contracts)
- Bundles:
  - `GET {apiUrl}/bundles/{username}` → `{ status?: 'success', data: { username, data: any[] } | any[] }`
  - `GET {apiUrl}/bundles/{username}/{repo}` → `{ status?: 'success', data: { username, repo, data } | any }`
- AI:
  - `POST {apiUrl}/ai` with `{ query, username? }` → `{ response: markdown, repositories_used: {name,relevance_score}[] }`

## Patterns for New Code
- API calls: inject `HttpClient` + `ConfigService`, build URLs from `config.apiUrl`, unwrap `{ status, data }`, fallback on error, cache for 10–15 min if useful
- List UIs: derive typed VM with explicit fallbacks, use `async` pipe for observable streams, keep filters/sorting pure
- Markdown: always sanitize with DOMPurify, generate slug IDs for anchors/TOC
- Styles: use CSS vars with Tailwind utilities, never hardcode

## Developer Workflows
- Build tooling (package.json/angular.json) may be outside `src/`; verify in parent repo
  - Dev server: `ng serve` or `npm run dev` from project root
  - Production build: `ng build` or `npm run build`
  - API during dev: Azure Functions at `http://localhost:7071/api` (see `environment.development.ts`)
- Tailwind v4 via `@import 'tailwindcss'` in `styles.css`; tailwind.config.js may not exist

## Examples
- Use `RepoBundleService` in a component:
  - `this.repoBundle$ = this.repoSvc.getUserBundle('yungryce');`
  - `this.filtered$ = this.repoBundle$.pipe(map(b => b.data.map(toCardVM)))`
- Normalize language map to percentages:
  - `const total = Object.values(langs).reduce((a,b)=>a+Number(b),0)||1; Object.entries(langs).map(([k,v])=>({k,pct:Math.round(Number(v)/total*100)})).sort((a,b)=>b.pct-a.pct)`
- Toggle theme:
  - `this.theme = this.theme==='dark'?'light':'dark'; localStorage.setItem('theme', this.theme); document.documentElement.classList.toggle('dark', this.theme==='dark');`

## Gotchas and Guardrails
- API responses may be raw or `{ status, data }`; always unwrap
- `repoContext` can be missing/partial; apply safe fallbacks for `type`, `description`, `tech_stack`
- Cache keys must be unique/stable (`bundle-${username}`, `repo-bundle-${username}-${repo}`), respect TTL, clear on data shape changes
- Preserve accessibility: keep aria attributes and roles in templates

## Open Questions / Feedback Needed
- Build/test commands live outside `src/`; confirm exact scripts in repo root (Angular CLI vs. Vite)
- MarkdownModule is provided app-wide, but most rendering uses `marked` + `DOMPurify`. If you standardize, update this guide

If any section is unclear or incomplete (especially dev commands or API nuances), please provide feedback to refine this doc.
