# Cloudfolio UI

<p align="center">
  <img src="https://img.shields.io/badge/Angular-17+-red?style=for-the-badge&logo=angular" alt="Angular">
  <img src="https://img.shields.io/badge/Azure-Functions-blue?style=for-the-badge&logo=microsoft-azure" alt="Azure Functions">
  <img src="https://img.shields.io/badge/Status-Production--Ready-green?style=for-the-badge" alt="Status">
</p>

## Overview

Cloudfolio UI is the recruiter-focused frontend that drives the queue-first backend (API Gateway + sync/merge/training workers). It reuses the same styling tokens as the portfolio app, but its UX is centered around evaluating any GitHub username.

Key flow:
- Landing page accepts a GitHub username + optional skills/keywords.
- It triggers `POST /api/bundles/{username}/refresh` with `force_refresh=true`.
- It navigates to the AI page, which polls `GET /api/bundles/{username}/status?job_id=...` and then loads:
  - bundle data from `GET /api/bundles/{username}?job_id=...`
  - repo detail from `GET /api/bundles/{username}/{repo}?job_id=...`
  - AI responses from `POST /api/ai`

## Routes

- `/` (Landing)
- `/ai` (AI review + candidate tracking)
- `/projects` and `/projects/:repo` (candidate repositories)

---

## Local development

1. Start backend workers with `./apps/run-dev-session.sh` (api-gateway must be on `http://localhost:7071`).
2. In this folder:
  - `npm install`
  - `npm start`

The app uses an HTTP interceptor to attach `X-Session-Id` to every API call.

## 🏗️ Architecture

- **Frontend**: Angular standalone application (17+) styled with Tailwind CSS.
  - Entry point: `src/main.ts`
  - Routing: `src/app/app.routes.ts`
  - Services: `src/app/services/*`
- **Backend**: Azure Functions (Python) serving GitHub data bundles and AI endpoints.
  - Entry point: `api/function_app.py`
  - Key modules: `api/config/github_repo_manager.py`, `api/ai/repo_scoring_service.py`
- **Integration**: REST API communication between frontend and backend.

## 🔧 Technologies

- **Frontend**: Angular 17+, Tailwind CSS, TypeScript
- **Backend**: Azure Functions, Python 3.11+
- **Libraries**: DOMPurify, Marked.js, RxJS
- **DevOps**: Azure CLI, GitHub Actions

## ⚙️ Configuration

### Environment Variables

| Variable              | Description                          | Example                     |
|-----------------------|--------------------------------------|-----------------------------|
| `AzureWebJobsStorage` | Azure storage connection string      | `DefaultEndpointsProtocol...` |
| `GITHUB_TOKEN`        | GitHub API token                     | `ghp_1234567890abcdef`      |

### Frontend

- Update `src/environments/environment.ts` with the API base URL.

### Backend

- Configure `api/local.settings.json` with the required environment variables.

## 🚀 Deployment

### Frontend

```bash
# Install dependencies
npm install

# Build the application
npm run build

# Start the development server
npm run start
```

### Backend

```bash
# Navigate to the API folder
cd api

# Install dependencies
pip install -r requirements.txt

# Start Azure Functions locally
func start
```

## 💡 Usage

1. Navigate to the Projects page to view repositories as cards.
2. Click on a repository card to view detailed information, including the rendered `README.md`.
3. Use the AI Assistant to query repository insights.
4. Toggle between dark and light themes using the theme switcher.

## 📋 Skills Demonstrated

- **Frontend Development**: Angular, Tailwind CSS, RxJS
- **Backend Development**: Azure Functions, Python
- **AI Integration**: Semantic scoring, AI-driven recommendations
- **Markdown Rendering**: Sanitized Markdown with Table of Contents
- **DevOps**: CI/CD pipelines, Azure resource management

## 🔍 Monitoring

- **Frontend**: Use browser developer tools for debugging.
- **Backend**: Check logs in `api/api_function_app.log` or Azure Portal.

## 📄 License

This project is licensed under the MIT License.