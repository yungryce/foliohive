# Cloudfolio UI

Angular frontend for Cloudfolio (deployed to Azure Static Web Apps).

## What it does

- Collects a GitHub username and triggers a refresh via the API Gateway.
- Polls job/status endpoints and renders bundled repo/project data.
- Provides an “assistant” experience backed by the API Gateway.

## Local development

### Option A (recommended): run the full stack

From the repo root:

```bash
./run-dev-session.sh
```

That script starts Azurite + local Azure Functions + the UI dev server.

### Option B: run only the UI

```bash
cd ui
npm install
npm start
```

By default, the API Gateway runs locally at `http://localhost:7071`.

## Configuration

Frontend environment files live under:

- `src/environments/environment.development.ts`
- `src/environments/environment.ts`

If you need to point the UI at a different backend (deployed Function App), update the API base URL in the appropriate environment file.

## Backend pointers

- API Gateway Function App (local/dev): `api/v0.2.0/api-gateway/`
- Function entrypoint: `api/v0.2.0/api-gateway/function_app.py`

## CI/CD

- UI CI: `.ado/ci-swa.yml`
- UI CD: `.ado/cd-swa.yml`

The build output published as an artifact is `ui/dist/browser/`.