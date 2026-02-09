# FolioHive API (v0.3.0)

**Azure Functions Backend with Blueprint Pattern**

The FolioHive API is a modular monolith Azure Functions application using the Blueprint pattern. It consolidates HTTP endpoints and queue-triggered workers into a single deployment unit for simplified management while maintaining logical separation.

---

## 📋 Table of Contents

- [Architecture Overview](#architecture-overview)
- [Blueprints Pattern](#blueprints-pattern)
- [Workers Deep Dive](#workers-deep-dive)
- [Shared Modules](#shared-modules)
- [Table Schema](#table-schema)
- [Queue Messages](#queue-messages)
- [Local Development](#local-development)
- [Testing](#testing)
- [API Reference](#api-reference)

---

## 🏗️ Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    function_app.py                           │
│              (Azure Functions Entry Point)                   │
└────────────┬────────────┬──────────────┬────────────────────┘
             │            │              │
   ┌─────────┴─────┐  ┌──┴──────┐  ┌────┴──────────┐
   │ API Gateway   │  │  Sync   │  │ Cache Worker  │
   │  Blueprint    │  │ Worker  │  │   Blueprint   │
   │ (HTTP Routes) │  │Blueprint│  │ (Queue Trig)  │
   └───────────────┘  └─────────┘  └───────────────┘
             │            │              │
   ┌─────────┴────────────┴──────────────┴────────────────┐
   │            Reconciliation Worker                      │
   │            (Timer: Every 3 minutes)                   │
   └───────────────────────────────────────────────────────┘
                          │
   ┌──────────────────────┴────────────────────────────────┐
   │              cloudfolio_shared Package                │
   │  ┌────────┬─────────┬────────┬─────────┬─────────┐   │
   │  │   ai/  │ cache/  │github/ │ queue/  │ table/  │   │
   │  └────────┴─────────┴────────┴─────────┴─────────┘   │
   └───────────────────────────────────────────────────────┘
                          │
   ┌──────────────────────┴────────────────────────────────┐
   │              Azure Storage Account                    │
   │  Table Storage | Blob Storage | Queue Storage         │
   └───────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Type | Trigger | Purpose |
|-----------|------|---------|---------|
| **API Gateway** | Blueprint | HTTP | REST endpoints for sync, polling, summaries, queries |
| **Sync Worker** | Blueprint | Queue | Fetch GitHub metadata, generate fingerprints, enqueue cache jobs |
| **Cache Worker** | Blueprint | Queue | Fetch file contents (README, configs), save to blob storage |
| **Reconciliation Worker** | Blueprint | Timer | Retry failed jobs, cleanup stale data, health checks |

---

## 🔌 Blueprints Pattern

Azure Functions Blueprints allow logical separation of concerns within a single Function App:

### Benefits
- **Single Deployment Unit**: One `host.json`, one `requirements.txt`, one deployment
- **Shared Resources**: Common connection pools, environment variables, dependencies
- **Logical Separation**: Each blueprint has clear boundaries and responsibilities
- **Local Development**: All workers run in one `func start` process

### Implementation

**function_app.py** (Main Entry Point)
```python
import azure.functions as func
from blueprints.api_gateway import bp as api_bp
from blueprints.sync_worker import bp as sync_bp
from blueprints.cache_worker import bp as cache_bp
from blueprints.reconciliation_worker import bp as recon_bp

app = func.FunctionApp()

# Register blueprints
app.register_functions(api_bp)
app.register_functions(sync_bp)
app.register_functions(cache_bp)
app.register_functions(recon_bp)
```

**blueprints/api_gateway.py**
```python
bp = func.Blueprint()

@bp.route(route="trigger_candidate_refresh", methods=["POST"])
def trigger_candidate_refresh(req: func.HttpRequest) -> func.HttpResponse:
    # HTTP endpoint logic
    pass
```

**blueprints/sync_worker.py**
```python
bp = func.Blueprint()

@bp.queue_trigger(arg_name="msg", queue_name="sync-jobs", connection="AzureWebJobsStorage")
def process_sync_job(msg: func.QueueMessage) -> None:
    # Queue processing logic
    pass
```

---

## ⚙️ Workers Deep Dive

### 1. API Gateway Blueprint (`api_gateway.py`)

**Purpose**: HTTP endpoints for UI interaction

**Key Endpoints**:
- `POST /trigger_candidate_refresh` - Start new candidate sync
- `GET /get_job_status` - Poll job progress
- `GET /get_candidate` - Retrieve candidate data
- `POST /get_profile` - Generate profile summary (AI)
- `POST /get_repo_readme_summary` - Generate repo summary (AI)
- `POST /portfolio_query` - AI assistant query

**Data Flow**:
```
1. UI sends POST /trigger_candidate_refresh with username
2. API Gateway checks for existing data (fingerprint lookup)
3. If new/changed, enqueue sync-job with correlation_id
4. Return job_id to UI for polling
5. UI polls GET /get_job_status until metadata_ready or completed
```

**Key Logic**:
- Session management via `correlation_id` (stored in localStorage)
- Fingerprint-based deduplication (avoid redundant GitHub API calls)
- Field normalization (repo_name → name for consistency)
- Filter repos to only those with cached content

### 2. Sync Worker Blueprint (`sync_worker.py`)

**Purpose**: Fetch GitHub metadata and prepare cache jobs

**Triggered By**: Messages in `sync-jobs` queue

**Processing Steps**:
1. Parse message: Extract `username`, `job_id`, `correlation_id`
2. Update job state: `queued` → `syncing`
3. Fetch GitHub data:
   - User profile metadata
   - Repository list with languages/topics/stars
   - Generate fingerprints (SHA-256 of metadata)
4. Store in Table Storage:
   - `JobMetadata` table: Job tracking
   - `RepoGitHubMetadata` table: Repo details
   - `RepoLanguages` table: Language breakdown
5. Enqueue cache jobs: One message per repo to `cache-jobs` queue
6. Update job state: `syncing` → `metadata_ready`
7. Track progress: `_update_job_progress()` with metrics

**Key Logic**:
- `_fetch_repo_metadata()`: Calls GitHubRepoManager (REST + GraphQL)
- `_generate_fingerprint()`: SHA-256 hash of canonical JSON
- Deduplication: Skip repos with unchanged fingerprints
- Error handling: Retry with exponential backoff

### 3. Cache Worker Blueprint (`cache_worker.py`)

**Purpose**: Fetch and cache file contents from GitHub

**Triggered By**: Messages in `cache-jobs` queue

**Processing Steps**:
1. Parse message: Extract `username`, `repo_name`, `job_id`
2. Determine files to fetch:
   - README files (README.md, README.rst, etc.)
   - Config files (package.json, pyproject.toml, Cargo.toml, etc.)
3. Fetch file contents via GitHub API
4. Generate content fingerprints (SHA-256)
5. Store in Blob Storage:
   - Container: `file-cache`
   - Path: `{username}/{repo}/{fingerprint}/{filename}`
6. Update Table Storage:
   - `CachedFiles` table: Track blob locations
   - `RepoGitHubMetadata` table: Update repo state to `cached`
7. Handle errors: Log and continue (cache is best-effort)

**Key Logic**:
- `CacheManager.cache_repo_files()`: Orchestrates file fetching
- Content-addressable storage: Same content = same fingerprint
- Skip unchanged files: Compare fingerprints before fetching
- Supported languages: Python, JavaScript/TypeScript, Rust, Go, Java, Ruby, PHP

### 4. Reconciliation Worker Blueprint (`reconciliation_worker.py`)

**Purpose**: Cleanup, retry, and health monitoring

**Triggered By**: Timer (every 3 minutes)

**Processing Steps**:
1. Find stale jobs: Jobs stuck in `syncing` for >10 minutes
2. Retry logic: Re-enqueue sync-jobs for failed attempts
3. Cleanup: Mark abandoned jobs as `failed`
4. Health checks: Verify queue depths, table counts
5. Metrics: Log reconciliation stats

**Key Logic**:
- Query `JobMetadata` for incomplete jobs beyond threshold
- Exponential backoff: Increase retry delay with each attempt
- Dead letter handling: Move to DLQ after max retries
- Telemetry: Custom metrics for monitoring

---

## 📦 Shared Modules

### cloudfolio_shared Package

Located in `shared/src/cloudfolio_shared/`, this package provides reusable logic:

#### 1. `ai/` - AI Integration
- **ai_assistant.py**: OpenAI API wrapper
  - Model selection: `gpt-5-nano` (default), `gpt-4o-mini` (balanced)
  - Response validation: Checks for None, truncation, empty responses
  - Token limits: 4000 output tokens for complex summaries
  - Error handling: Wraps errors in HTML for UI display

- **summary_manager.py**: Context orchestration
  - Token budget management (45k profile, 26k readme, 36k query)
  - Content chunking to fit within limits
  - Field normalization: Supports both `name` and `repo_name`
  - Summary methods: `build_profile_context()`, `build_readme_context()`, `build_query_context()`

#### 2. `cache/` - Caching Logic
- **cache_manager.py**: Blob storage operations
  - Fingerprint generation (SHA-256)
  - Content-addressable blob keys
  - TTL-based cleanup (future)
  - Fetch and store file contents

#### 3. `github/` - GitHub API Client
- **github_repo_manager.py**: Unified REST + GraphQL interface
  - User profile fetching
  - Repository metadata (languages, topics, stars, dates)
  - File content retrieval (README, configs)
  - Rate limit handling
  - Pagination support

#### 4. `queue/` - Queue Messaging
- **queue_manager.py**: Azure Queue Storage client
  - Message serialization (JSON)
  - Enqueue with correlation_id
  - Dequeue with visibility timeout
  - Batch operations

#### 5. `table/` - Table Storage Schema
- **table_manager.py**: 7-table normalized schema
  - CRUD operations with retry logic
  - Query builders with filters
  - Batch operations (up to 100 entities)
  - Partition/row key management

---

## 🗄️ Table Schema

### 7 Normalized Tables

#### 1. JobMetadata
**Purpose**: Track sync job lifecycle

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | string | `job#{job_id}` |
| RowKey | string | `metadata` |
| job_id | string | Unique job identifier (UUID) |
| username | string | GitHub username |
| correlation_id | string | Session tracking ID |
| state | string | `queued`, `syncing`, `metadata_ready`, `completed`, `failed` |
| created_at | datetime | Job creation timestamp |
| updated_at | datetime | Last state change |
| total_repos | int | Total repositories found |
| synced_repos | int | Repos with metadata fetched |
| cached_repos | int | Repos with files cached |

#### 2. RepoGitHubMetadata
**Purpose**: GitHub repository details

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | string | `user#{username}` |
| RowKey | string | `repo#{repo_name}` |
| repo_name | string | Repository name |
| description | string | Repo description |
| html_url | string | GitHub URL |
| created_at | datetime | Repo creation date |
| updated_at | datetime | Last push date |
| stargazers_count | int | Star count |
| topics | string | Comma-separated topics |
| default_branch | string | Main branch name |
| repo_state | string | `pending`, `synced`, `cached` |
| fingerprint | string | SHA-256 of metadata |

#### 3. RepoLanguages
**Purpose**: Language breakdown per repository

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | string | `user#{username}` |
| RowKey | string | `repo#{repo_name}#lang#{language}` |
| language | string | Language name (Python, JavaScript, etc.) |
| bytes | int | Bytes of code in that language |
| percentage | float | Percentage of total codebase |

#### 4. CachedFiles
**Purpose**: Track blob storage locations for cached files

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | string | `user#{username}` |
| RowKey | string | `repo#{repo_name}#file#{filename}` |
| filename | string | File name (README.md, package.json, etc.) |
| blob_path | string | Blob storage path |
| fingerprint | string | SHA-256 of file content |
| cached_at | datetime | Cache timestamp |
| file_type | string | `readme`, `config` |

#### 5. UserMetadata
**Purpose**: GitHub user profile information

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | string | `user#{username}` |
| RowKey | string | `metadata` |
| username | string | GitHub username |
| name | string | Display name |
| bio | string | User bio |
| company | string | Company name |
| location | string | Location |
| email | string | Public email |
| avatar_url | string | Profile picture URL |
| public_repos | int | Total public repos |
| followers | int | Follower count |
| following | int | Following count |

#### 6. PortfolioSummary
**Purpose**: Store generated AI summaries

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | string | `user#{username}` |
| RowKey | string | `summary#{type}` (profile, repo, query) |
| summary_html | string | Generated HTML summary |
| generated_at | datetime | Generation timestamp |
| token_count | int | Tokens used |
| model_used | string | AI model identifier |

#### 7. SessionContext
**Purpose**: Track user sessions and correlation IDs

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | string | `session#{correlation_id}` |
| RowKey | string | `metadata` |
| correlation_id | string | Session identifier (UUID) |
| username | string | Associated GitHub username |
| created_at | datetime | Session start |
| last_activity | datetime | Last API call |

---

## 📨 Queue Messages

### sync-jobs Queue

**Purpose**: Trigger metadata sync for candidate

**Message Schema**:
```json
{
  "job_id": "uuid-v4",
  "username": "github-username",
  "correlation_id": "uuid-v4",
  "enqueued_at": "ISO-8601 timestamp"
}
```

**Processed By**: `sync_worker.process_sync_job()`

### cache-jobs Queue

**Purpose**: Trigger file caching for specific repository

**Message Schema**:
```json
{
  "job_id": "uuid-v4",
  "username": "github-username",
  "repo_name": "repository-name",
  "correlation_id": "uuid-v4",
  "enqueued_at": "ISO-8601 timestamp"
}
```

**Processed By**: `cache_worker.process_cache_job()`

---

## 🛠️ Local Development

### Prerequisites

- Python 3.12–3.14
- Azure Functions Core Tools v4 (`func`)
- Azurite (Azure Storage Emulator)
- GitHub Personal Access Token (with `repo` scope)
- OpenAI API Key

### Setup Steps

#### 1. Clone and Install Dependencies

```bash
cd api/v0.3.0
./setup-dev.sh
```

This script:
- Creates Python virtual environment
- Installs `cloudfolio_shared` package in editable mode
- Installs function app dependencies
- Sets up test environment

#### 2. Start Azurite

```bash
cd api/v0.3.0
./ensure-azurite.sh
```

Azurite runs on:
- Table Storage: `http://localhost:10002`
- Blob Storage: `http://localhost:10000`
- Queue Storage: `http://localhost:10001`

#### 3. Configure Local Settings

```bash
cd function-app
cp local.settings.example.json local.settings.json
```

Edit `local.settings.json`:
```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "GITHUB_TOKEN": "ghp_YOUR_TOKEN_HERE",
    "OPENAI_API_KEY": "sk-YOUR_KEY_HERE",
    "OPENAI_MODEL_DEFAULT": "gpt-5-nano",
    "OPENAI_MODEL_BALANCED": "gpt-4o-mini"
  }
}
```

#### 4. Start Function App

```bash
cd function-app
source ../.venv/bin/activate
func start --python --port 7071
```

Local base URL: `http://localhost:7071/api`

#### 5. Verify Setup

```bash
# Health check (if implemented)
curl http://localhost:7071/api/health

# Trigger a sync (replace with actual endpoint)
curl -X POST http://localhost:7071/api/trigger_candidate_refresh \
  -H "Content-Type: application/json" \
  -d '{"username": "torvalds"}'
```

### Environment Variables

See [ENVIRONMENT_VARIABLES.md](./ENVIRONMENT_VARIABLES.md) for complete reference.

**Critical Variables**:
- `GITHUB_TOKEN`: GitHub API authentication
- `OPENAI_API_KEY`: AI summary generation
- `AzureWebJobsStorage`: Storage account connection string
- `OPENAI_MODEL_DEFAULT`: Default AI model (gpt-5-nano)
- `OPENAI_MODEL_BALANCED`: Balanced AI model (gpt-4o-mini)

---

## 🧪 Testing

### Test Structure

```
tests/
├── __init__.py
├── conftest.py                  # Pytest fixtures
├── pytest.ini                   # Pytest configuration
├── requirements.txt             # Test dependencies
├── run_tests.sh                 # Test runner script
├── test_reconciliation_worker.py # Unit tests
├── e2e_curl_tests.sh            # E2E curl tests
└── integration/
    ├── test_cache_sync.py       # Cache worker tests
    ├── test_e2e_flow.py         # End-to-end flow
    ├── test_queue_communication.py # Queue tests
    └── test_table_integration.py # Table tests
```

### Run All Tests

```bash
cd api/v0.3.0/tests
./run_tests.sh
```

### Run Specific Test Suites

```bash
# Unit tests
pytest test_reconciliation_worker.py -v

# Integration tests
pytest integration/ -v

# Specific integration test
pytest integration/test_e2e_flow.py::test_full_candidate_sync -v

# With coverage
pytest --cov=cloudfolio_shared --cov-report=html
```

### E2E Tests with Curl

```bash
cd api/v0.3.0/tests
./e2e_curl_tests.sh
```

This script tests:
1. Trigger candidate refresh
2. Poll job status
3. Retrieve candidate data
4. Generate profile summary
5. Generate repo summary
6. AI assistant query

### Test Fixtures

Located in `conftest.py`:
- `azurite_storage`: Mocked Azure Storage client
- `mock_github_client`: Mocked GitHub API responses
- `mock_openai_client`: Mocked OpenAI API responses
- `sample_repo_metadata`: Test data fixtures

---

## 📚 API Reference

### Sync Endpoints

#### POST /trigger_candidate_refresh
Start new candidate sync job

**Request Body**:
```json
{
  "username": "github-username",
  "correlation_id": "optional-uuid"
}
```

**Response**:
```json
{
  "job_id": "uuid-v4",
  "username": "github-username",
  "state": "queued",
  "created_at": "2026-02-09T12:00:00Z"
}
```

#### GET /get_job_status
Poll job progress

**Query Parameters**:
- `job_id`: Job identifier (UUID)

**Response**:
```json
{
  "job_id": "uuid-v4",
  "state": "metadata_ready",
  "total_repos": 50,
  "synced_repos": 50,
  "cached_repos": 35,
  "updated_at": "2026-02-09T12:05:00Z"
}
```

**Job States**:
- `queued`: Job created, waiting for worker
- `syncing`: Fetching GitHub metadata
- `metadata_ready`: Metadata complete, caching in progress
- `completed`: All repos cached
- `failed`: Error occurred

### Data Retrieval Endpoints

#### GET /get_candidate
Retrieve candidate repository list

**Query Parameters**:
- `username`: GitHub username

**Response**:
```json
{
  "username": "github-username",
  "repos": [
    {
      "name": "repo-name",
      "description": "Repo description",
      "html_url": "https://github.com/user/repo",
      "stargazers_count": 100,
      "topics": ["python", "api"],
      "languages": {"Python": 75.5, "JavaScript": 24.5},
      "repo_state": "cached"
    }
  ]
}
```

### AI Summary Endpoints

#### POST /get_profile
Generate candidate profile summary

**Request Body**:
```json
{
  "username": "github-username"
}
```

**Response**:
```json
{
  "summary_html": "<h3>Profile Summary</h3><p>...</p>",
  "token_count": 3500,
  "model_used": "gpt-4o-mini"
}
```

#### POST /get_repo_readme_summary
Generate repository summary

**Request Body**:
```json
{
  "username": "github-username",
  "repo_name": "repository-name"
}
```

**Response**:
```json
{
  "summary_html": "<h3>Repository Overview</h3><p>...</p>",
  "token_count": 2800,
  "model_used": "gpt-5-nano"
}
```

#### POST /portfolio_query
AI assistant query

**Request Body**:
```json
{
  "username": "github-username",
  "query": "What backend frameworks does this candidate use?"
}
```

**Response**:
```json
{
  "response_html": "<p>Based on the candidate's repositories...</p>",
  "token_count": 4000,
  "model_used": "gpt-4o-mini"
}
```

---

## 🚀 Deployment

Deployment is automated via Azure DevOps pipeline: `azure-functions-cd.yml`

### Manual Deployment

```bash
cd api/v0.3.0/function-app

# Build and deploy
func azure functionapp publish <function-app-name> --python
```

### Deployment Checklist
- [ ] Environment variables configured in Azure
- [ ] Managed Identity enabled for Storage/KeyVault
- [ ] VNet integration configured (if using private networking)
- [ ] Application Insights enabled
- [ ] Scaling limits set (max instances)

---

## 🔍 Monitoring

### Application Insights Queries

**Job Success Rate**:
```kusto
traces
| where message contains "Job completed"
| summarize total=count(), success=countif(message contains "success") by bin(timestamp, 1h)
| extend success_rate = success * 100.0 / total
```

**Cache Hit Ratio**:
```kusto
traces
| where message contains "Fingerprint" 
| summarize hits=countif(message contains "unchanged"), total=count() by bin(timestamp, 1h)
| extend hit_ratio = hits * 100.0 / total
```

**AI Token Usage**:
```kusto
customMetrics
| where name == "ai_token_usage"
| summarize avg(value), max(value), sum(value) by bin(timestamp, 1h)
```

### Custom Metrics

Emitted by `ai_assistant.py` and `summary_manager.py`:
- `ai_token_usage`: Tokens consumed per request
- `cache_hit_ratio`: Percentage of fingerprint matches
- `job_duration_seconds`: Time from queued to completed
- `queue_depth`: Current message count in queues

---

## 🐛 Troubleshooting

### Common Issues

**Issue**: "Connection refused" to Azurite  
**Solution**: Ensure Azurite is running: `./ensure-azurite.sh`

**Issue**: "GitHub API rate limit exceeded"  
**Solution**: Verify `GITHUB_TOKEN` is set and valid. Check rate limit: `curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/rate_limit`

**Issue**: "OpenAI API returned None content"  
**Solution**: Increase `max_completion_tokens` in `ai_assistant.py`. Default is 4000.

**Issue**: "Processing repo none"  
**Solution**: Field name mismatch resolved in v0.3.0. Ensure using normalized field names.

**Issue**: Jobs stuck in "syncing" state  
**Solution**: Check Reconciliation Worker logs. May need to manually re-enqueue.

### Debug Mode

Enable verbose logging in `local.settings.json`:
```json
{
  "Values": {
    "PYTHON_ISOLATE_WORKER_DEPENDENCIES": "1",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "FUNCTIONS_WORKER_LOG_LEVEL": "debug"
  }
}
```

---

## 📖 Additional Resources

- [Azure Functions Python Developer Guide](https://learn.microsoft.com/azure/azure-functions/functions-reference-python)
- [Azure Storage Python SDK](https://learn.microsoft.com/python/api/overview/azure/storage)
- [GitHub REST API Documentation](https://docs.github.com/en/rest)
- [OpenAI API Documentation](https://platform.openai.com/docs/api-reference)

---

**Questions or Issues?** Check the [root README](../../README.md) or submit a GitHub issue.
