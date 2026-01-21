# Environment Variables Reference

This document describes all environment variables required to run Cloudfolio v0.3.0 API.

## Quick Setup

To generate a local development configuration:

```bash
cp function-app/local.settings.example.json function-app/local.settings.json
# Edit function-app/local.settings.json with your actual values
```

## Configuration Variables

### Azure Functions & Runtime

| Variable | Description | Development Value | Required |
|----------|-------------|-------------------|----------|
| `AzureWebJobsStorage` | Azure Storage connection string or `UseDevelopmentStorage=true` for Azurite | `UseDevelopmentStorage=true` | ✓ |
| `FUNCTIONS_WORKER_RUNTIME` | Python runtime for Azure Functions | `python` | ✓ |
| `AzureWebJobsFeatureFlags` | Feature flags for Azure Functions | `EnableWorkerIndexing` | ✓ |

### GitHub Integration

| Variable | Description | Example | Required |
|----------|-------------|---------|----------|
| `GITHUB_TOKEN` | GitHub Personal Access Token for API calls | `ghp_xxxxxxxxxxxx` | ✓ |
| `GITHUB_USERNAME` | GitHub username (used as fallback in code) | `your-username` | Optional |

### Storage Endpoints

These variables configure connections to Azure Storage services. For local development with Azurite, use the provided values.

#### Blob Storage (Cache)

| Variable | Description | Development Value | Used By |
|----------|-------------|-------------------|---------|
| `BLOB_SERVICE_URI` | Blob service endpoint URL | `http://127.0.0.1:10000/devstoreaccount1` | `cache_manager` |
| `AzureWebJobsStorage__blobServiceUri` | Alternative blob service URI (fallback) | `http://127.0.0.1:10000/devstoreaccount1` | `cache_manager` |

#### Queue Storage

| Variable | Description | Development Value | Used By |
|----------|-------------|-------------------|---------|
| `AZURE_STORAGE_QUEUE_URL` | Queue service endpoint URL | `http://127.0.0.1:10001/devstoreaccount1` | `queue_manager` |
| `AzureWebJobsStorage__queueServiceUri` | Alternative queue service URI (fallback) | `http://127.0.0.1:10001/devstoreaccount1` | `queue_manager` |

#### Table Storage

| Variable | Description | Development Value | Used By |
|----------|-------------|-------------------|---------|
| `AZURE_TABLES_ENDPOINT` | Table service endpoint URL | `http://127.0.0.1:10002/devstoreaccount1` | `table_manager` |
| `AzureWebJobsStorage__tableServiceUri` | Alternative table service URI (fallback) | `http://127.0.0.1:10002/devstoreaccount1` | `table_manager` |
| `TABLE_SERVICE_URI` | Primary table service URI | `http://127.0.0.1:10002/devstoreaccount1` | `table_manager` |
| `TABLE_STORAGE_CONNECTION_STRING` | Table storage connection string | `UseDevelopmentStorage=true` | `table_manager` |

### Table Names

| Variable | Description | Default | Used By |
|----------|-------------|---------|---------|
| `TABLE_JOB_METADATA` | Table name for job tracking sessions | `JobMetadata` | `table_manager` |
| `TABLE_REPO_METADATA` | Table name for repository metadata | `RepoMetadata` | `table_manager` |
| `TABLE_MODEL_METADATA` | Table name for model metadata | `ModelMetadata` | `table_manager` |

### Queue Configuration

| Variable | Description | Default | Used By |
|----------|-------------|---------|---------|
| `ENABLE_QUEUE_MODE` | Enable/disable queue-based processing | `true` | `api_gateway`, `queue_manager` |

### Cache Configuration

| Variable | Description | Default | Used By |
|----------|-------------|---------|---------|
| `CF_HOT_CACHE_ENABLED` | Enable/disable in-memory hot cache | `true` | `cache_manager` |
| `CF_CACHE_CLEANUP_ENABLED` | Enable/disable scheduled cache cleanup | `true` | `reconciliation_worker` |
| `CF_CACHE_CLEANUP_MAX_AGE_HOURS` | Max age in hours for cache cleanup | `24` | `reconciliation_worker` |

### Reconciliation & Job Processing

| Variable | Description | Default | Used By |
|----------|-------------|---------|---------|
| `CF_RECONCILE_REQUEUE_COOLDOWN_SECONDS` | Cooldown between requeuing failed jobs | `600` (10 minutes) | `reconciliation_worker` |
| `CF_RECONCILE_MIN_AGE_SECONDS` | Minimum age of jobs before reconciliation | `180` (3 minutes) | `reconciliation_worker` |

### Repository Discovery

| Variable | Description | Default | Used By |
|----------|-------------|---------|---------|
| `ENABLE_CONFIG_DISCOVERY_GRAPHQL` | Use GraphQL API for config file discovery (experimental) | `false` | `sync_worker` |

### AI & Training

| Variable | Description | Example | Required |
|----------|-------------|---------|----------|
| `GROQ_API_KEY` | API key for Groq LLM service | `gsk_xxxxxxxxxxxx` | Optional (AI features) |
| `TRAINING_EXPERIMENT` | Name of the ML experiment | `default` | Optional |

### Build & Monitoring

| Variable | Description | Development Value | Used By |
|----------|-------------|-------------------|---------|
| `BUILD_BUILDNUMBER` | Build number for version tracking | `dev` | `api_gateway` health endpoint |

## Environment Variable Priority

For variables with multiple sources, the priority is:

1. **Explicit environment variable** (e.g., `BLOB_SERVICE_URI`)
2. **Fallback environment variable** (e.g., `AzureWebJobsStorage__blobServiceUri`)
3. **Default hardcoded value** (if applicable)

## Local Development Setup

### Using Azurite

For local development, use Azurite (Azure Storage emulator):

```bash
# Install Azurite
npm install -g azurite

# Start Azurite
azurite --silent --location ./data
```

Set these in `local.settings.json`:
```json
{
  "AzureWebJobsStorage": "UseDevelopmentStorage=true",
  "AZURE_STORAGE_QUEUE_URL": "http://127.0.0.1:10001/devstoreaccount1",
  "AZURE_TABLES_ENDPOINT": "http://127.0.0.1:10002/devstoreaccount1"
}
```

### GitHub Personal Access Token

Create a token with these scopes:
- `repo` (full control of private repositories)
- `user:email` (read user profile and email)
- `read:user` (read user profile data)

[Create token](https://github.com/settings/tokens)

## Production Deployment

For production (Azure-hosted Functions):

1. **Remove development storage URLs** - Use managed identity or connection strings
2. **Set real GitHub token** - Use secure secret management
3. **Enable appropriate features** - Set `ENABLE_QUEUE_MODE`, `CF_HOT_CACHE_ENABLED` based on needs
4. **Configure table names** - Use production table names if different
5. **Set training parameters** - Adjust `CF_RECONCILE_REQUEUE_COOLDOWN_SECONDS`, etc.

## Troubleshooting

### "GITHUB_TOKEN environment variable is not set"
- Ensure `GITHUB_TOKEN` is set in `local.settings.json`
- Check that the token is valid and has appropriate scopes

### "Table manager disabled"
- Verify `TABLE_SERVICE_URI` or `AzureWebJobsStorage__tableServiceUri` is set
- Ensure Azurite table endpoint is running
- Check table names are configured

### "Queue manager disabled"
- Verify `AzureWebJobsStorage__queueServiceUri` is set
- Ensure Azurite queue endpoint is running
- Check `ENABLE_QUEUE_MODE` is not set to `false`

### "Cache errors"
- Check blob storage endpoint is accessible
- Verify `BLOB_SERVICE_URI` or `AzureWebJobsStorage__blobServiceUri` is set
- Ensure Azurite blob endpoint is running

## Variable Sources in Codebase

### Core Managers
- **cache_manager.py**: `CF_HOT_CACHE_ENABLED`, `BLOB_SERVICE_URI`, `AzureWebJobsStorage`
- **table_manager.py**: `TABLE_*`, `TABLE_SERVICE_URI`, `TABLE_STORAGE_CONNECTION_STRING`, `AzureWebJobsStorage`
- **queue_manager.py**: `AzureWebJobsStorage__queueServiceUri`, `AzureWebJobsStorage`
- **github_api.py**: `GITHUB_TOKEN`, `GITHUB_USERNAME`

### Workers & Blueprint Functions
- **api_gateway.py**: `GITHUB_TOKEN`, `ENABLE_QUEUE_MODE`, `BUILD_BUILDNUMBER`
- **sync_worker.py**: `GITHUB_TOKEN`, `ENABLE_CONFIG_DISCOVERY_GRAPHQL`
- **merge_worker.py**: `TRAINING_EXPERIMENT`
- **reconciliation_worker.py**: `CF_RECONCILE_REQUEUE_COOLDOWN_SECONDS`, `CF_RECONCILE_MIN_AGE_SECONDS`, `CF_CACHE_CLEANUP_ENABLED`, `CF_CACHE_CLEANUP_MAX_AGE_HOURS`

### AI Services
- **ai_assistant.py**: `GROQ_API_KEY`
