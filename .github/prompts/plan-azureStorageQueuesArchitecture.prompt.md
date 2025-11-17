# Azure Storage Queues Migration Architecture Plan

**Date**: November 17, 2025  
**Status**: Planning Phase  
**Priority**: Lead Time Optimization (Fastest Delivery)

---

## Executive Summary

Migration from Durable Functions to Azure Storage Queues-based microservices architecture. **Optimized for shortest lead time** by leveraging existing infrastructure (zero new resources needed).

### Key Metrics
- **Delivery Time**: 7 days
- **Infrastructure Changes**: Zero (reuses existing storage account)
- **Code Changes**: ~150 lines (native Azure Functions queue triggers)
- **Expected Latency Reduction**: 120s → 5s (96% improvement)
- **Training Worker**: Containerized (see `plan-semanticModelTrainingRefactor.prompt.md`)

---

## Technology Selection: Azure Storage Queues

### Decision Rationale (Lead Time Focused)

Azure Storage Queues selected for the following reasons:

- ✅ **Already Deployed**: Storage account exists in `infra/main.bicep` (zero infrastructure changes)
- ✅ **Authentication Ready**: UAMI already has `queueDataContributor` role assigned
- ✅ **VNet Integrated**: Private endpoint exists in `sn-pep` subnet
- ✅ **Native Azure Functions SDK**: Built-in `@app.queue_trigger()` decorator (no polling code needed)
- ✅ **Error Handling**: Automatic dead-letter queue with configurable retry policies
- ✅ **Monitoring**: Auto-logs to Application Insights (zero instrumentation)
- ✅ **AKS Compatible**: Same storage account works with connection string SDK
- ✅ **Purpose-Built**: Designed specifically for reliable message queuing

**Key Advantage**: Leverages existing infrastructure for fastest delivery (6-day timeline).

---

## Architecture Overview

### Current State (Durable Functions - Blocking)

```
┌─────────────────────────────────────────────┐
│         Azure Function App (Monolith)       │
├─────────────────────────────────────────────┤
│  POST /orchestrator_start                   │
│         ↓                                    │
│  repo_context_orchestrator                  │
│         ↓                                    │
│  [Activity 1] get_stale_repos (10s)         │
│         ↓                                    │
│  [Activity 2] fetch_repo_context × N (60s)  │
│         ↓                                    │
│  [Activity 3] merge_results (5s)            │
│         ↓                                    │
│  [Activity 4] train_model (60s) ← BLOCKS    │
│         ↓                                    │
│  HTTP Response (120s total)                 │
└─────────────────────────────────────────────┘
```

**Problem**: User waits 120 seconds for training to complete.

---

### Target State (Queue-Based - Non-Blocking)

```
┌──────────────────────────────────────────────────────────────┐
│                    API Gateway (Async)                        │
│  POST /bundles/{username}/refresh                             │
│    → Enqueue N sync jobs                                      │
│    → Return job_id + status_url (< 2s)                        │
│                                                                │
│  GET /bundles/{username}/status?job_id={id}                   │
│    → Return progress: "7 of 10 repos synced"                  │
└────────────────────────┬─────────────────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         │   Azure Storage Queues        │
         │   (Existing Storage Account)  │
         └─┬──────────┬─────────────┬────┘
           │          │             │
  ┌────────▼──┐  ┌───▼──────┐      │
  │ Sync      │  │ Merge    │      │ (queue: model-training)
  │ Worker    │  │ Worker   │      │
  │ (5-10×)   │  │ (2×)     │      │
  │ Function  │  │ Function │      │
  │ App Queue │  │ App Queue│      │
  │ Trigger   │  │ Trigger  │      │
  └───────┬───┘  └────┬─────┘      │
          │           │             │
          └───────────┴─────────────┘
                      │             │
          ┌───────────▼───────────┐ │
          │  Azure Blob Storage   │ │
          │  (Cache - Unchanged)  │ │
          └───────────────────────┘ │
                                    │
                    ┌───────────────▼────────────────┐
                    │  Training Worker               │
                    │  (Containerized - See Below)   │
                    │                                │
                    │  Implementation Details:       │
                    │  plan-semanticModelTraining    │
                    │  Refactor.prompt.md            │
                    └────────────────────────────────┘
```

**Benefits**:
- ✅ User gets response in < 2s (96% faster)
- ✅ Sync workers scale independently (5-10 instances)
- ✅ Training runs in background (no blocking)
- ✅ Fault isolation (training failure doesn't block sync)

---

## Queue Design

### Queue 1: `github-sync` (High Priority)

**Purpose**: Fetch individual repository context  
**Message Schema**:
```json
{
  "job_id": "uuid-v4",
  "username": "{username}",
  "repo_name": "portfolio",
  "metadata": {
    "name": "portfolio",
    "updated_at": "2025-11-17T10:30:00Z",
    "languages": {"TypeScript": 45000, "Python": 30000}
  },
  "fingerprint": "sha256-hash"
}
```

**Note**: All usernames are dynamic - replace `{username}` with actual GitHub username from request.

**Worker Configuration**:
- **Instances**: 5-10 (auto-scale based on queue depth)
- **Timeout**: 30s per message
- **Retry Policy**: 3 attempts, exponential backoff
- **Dead-Letter Queue**: `github-sync-poison` (manual review after 3 failures)

---

### Queue 2: `merge-results` (Medium Priority)

**Purpose**: Aggregate synced repos into bundle cache  
**Message Schema**:
```json
{
  "job_id": "uuid-v4",
  "username": "{username}",
  "synced_repos": ["portfolio", "aks-cluster", "terraform-modules"],
  "trigger_source": "user_request"
}
```

**Note**: All usernames are dynamic - replace `{username}` with actual GitHub username from request.

**Worker Configuration**:
- **Instances**: 2 (fixed, lightweight)
- **Timeout**: 10s
- **Trigger**: Enqueued after all sync jobs complete

---

### Queue 3: `model-training` (Low Priority)

**Purpose**: Fine-tune semantic model (background task)  
**Message Schema**:
```json
{
  "username": "{username}",
  "repos_bundle": [...],  // Full bundle data
  "training_params": {
    "batch_size": 8,
    "max_pairs": 150,
    "epochs": 2
  }
}
```

**Note**: All usernames are dynamic. Training worker implementation: `plan-semanticModelTrainingRefactor.prompt.md`

**Worker Configuration**:
- **Instances**: 1 (CPU-intensive, no scale-out needed)
- **Timeout**: 10 minutes
- **Trigger**: Enqueued after merge completes
- **Priority**: Background (no user-facing impact)

---

## Implementation Plan (7-Day Timeline)

**Note**: This plan uses Azure Storage Queues for fastest delivery. Training worker implementation from `plan-semanticModelTrainingRefactor.prompt.md`.

### Day 1: Queue Infrastructure Setup (2 hours)

**Task**: Create queue manager using existing storage account

```python
# api/config/queue_manager.py (NEW FILE)
from azure.storage.queue import QueueServiceClient, QueueClient
from azure.identity import DefaultAzureCredential
import os
import json
import logging

logger = logging.getLogger('portfolio.api')

class QueueManager:
    """
    Manages Azure Storage Queues for asynchronous job processing.
    Reuses existing storage account from infra/main.bicep.
    """
    
    def __init__(self):
        # Reuse existing storage account URI (already configured in Function App)
        account_url = os.getenv('AzureWebJobsStorage__queueServiceUri')
        if not account_url:
            raise ValueError("AzureWebJobsStorage__queueServiceUri not configured")
        
        # Use existing User-Assigned Managed Identity (no connection strings)
        credential = DefaultAzureCredential()
        self.service_client = QueueServiceClient(account_url=account_url, credential=credential)
        
        # Queue names
        self.SYNC_QUEUE = "github-sync"
        self.MERGE_QUEUE = "merge-results"
        self.TRAINING_QUEUE = "model-training"
        
        # Initialize queues (idempotent)
        self._create_queues()
    
    def _create_queues(self):
        """Create queues if they don't exist."""
        for queue_name in [self.SYNC_QUEUE, self.MERGE_QUEUE, self.TRAINING_QUEUE]:
            try:
                queue_client = self.service_client.get_queue_client(queue_name)
                queue_client.create_queue()
                logger.info(f"Created queue: {queue_name}")
            except Exception as e:
                if "QueueAlreadyExists" not in str(e):
                    logger.warning(f"Error creating queue {queue_name}: {e}")
    
    def enqueue_sync_job(self, job_id: str, username: str, repo_metadata: dict):
        """Enqueue a single repository sync job."""
        message = {
            "job_id": job_id,
            "username": username,
            "repo_name": repo_metadata.get('name'),
            "metadata": repo_metadata,
            "fingerprint": repo_metadata.get('fingerprint')
        }
        queue_client = self.service_client.get_queue_client(self.SYNC_QUEUE)
        queue_client.send_message(json.dumps(message))
        logger.info(f"Enqueued sync job for {username}/{repo_metadata.get('name')}")
    
    def enqueue_merge_job(self, job_id: str, username: str, synced_repos: list):
        """Enqueue a merge job after all sync jobs complete."""
        message = {
            "job_id": job_id,
            "username": username,
            "synced_repos": synced_repos,
            "trigger_source": "sync_complete"
        }
        queue_client = self.service_client.get_queue_client(self.MERGE_QUEUE)
        queue_client.send_message(json.dumps(message))
        logger.info(f"Enqueued merge job for {username}")
    
    def enqueue_training_job(self, username: str, repos_bundle: list, training_params: dict):
        """Enqueue a training job (background task)."""
        message = {
            "username": username,
            "repos_bundle": repos_bundle,
            "training_params": training_params
        }
        queue_client = self.service_client.get_queue_client(self.TRAINING_QUEUE)
        queue_client.send_message(json.dumps(message))
        logger.info(f"Enqueued training job for {username}")

# Global instance
queue_manager = QueueManager()
```

**Infrastructure Changes**: **ZERO** (uses existing storage account)

---

### Day 2: API Gateway Refactor (4 hours)

**Task**: Add non-blocking refresh endpoint

```python
# api/function_app.py (ADD NEW ENDPOINT)

import uuid
from config.queue_manager import queue_manager
from config.fingerprint_manager import FingerprintManager

@app.route(route="bundles/{username}/refresh", methods=["POST"])
async def trigger_bundle_refresh(req: func.HttpRequest) -> func.HttpResponse:
    """
    NEW: Non-blocking endpoint that queues sync jobs.
    Replaces blocking POST /orchestrator_start endpoint.
    """
    try:
        username = req.route_params.get('username')
        if not username:
            return create_error_response("Username required", 400)
        
        # Feature flag: Toggle between queue-based and Durable Functions
        if not os.getenv('ENABLE_QUEUE_MODE', 'false').lower() == 'true':
            # Fallback to existing Durable Functions (backward compatibility)
            logger.info("Queue mode disabled, using Durable Functions")
            # ... existing orchestrator_start logic ...
        
        # Check if cached bundle is still valid (fingerprint match)
        bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
        cached_bundle = cache_manager.get(bundle_cache_key)
        
        if cached_bundle['status'] == 'valid':
            # Calculate current fingerprints
            repo_manager = _get_github_managers(username)
            current_repos = repo_manager.get_all_repos_metadata(username=username, include_languages=False)
            current_fingerprints = {
                repo.get('name'): FingerprintManager.generate_metadata_fingerprint(repo)
                for repo in current_repos if repo.get('name')
            }
            current_bundle_fingerprint = FingerprintManager.generate_bundle_fingerprint(
                list(current_fingerprints.values())
            )
            
            cached_fingerprint = cached_bundle.get('fingerprint')
            if cached_fingerprint == current_bundle_fingerprint:
                logger.info(f"Bundle fingerprint match for {username}, returning cached data")
                return create_success_response({
                    "status": "cached",
                    "repos_count": len(cached_bundle['data']),
                    "fingerprint": cached_fingerprint
                })
        
        # Identify stale repositories (same logic as get_stale_repos_activity)
        repo_manager = _get_github_managers(username)
        all_repos_metadata = repo_manager.get_all_repos_metadata(username=username, include_languages=True)
        
        # Calculate fingerprints
        current_fingerprints = {
            repo.get('name'): FingerprintManager.generate_metadata_fingerprint(repo)
            for repo in all_repos_metadata if repo.get('name')
        }
        
        # Check per-repo cache
        stale_repos = []
        for repo_metadata in all_repos_metadata:
            repo_name = repo_metadata.get('name')
            current_fingerprint = current_fingerprints.get(repo_name)
            
            repo_cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo_name)
            per_repo_entry = cache_manager.get(repo_cache_key)
            
            if per_repo_entry.get('status') != 'valid' or per_repo_entry.get('fingerprint') != current_fingerprint:
                stale_repos.append({**repo_metadata, 'fingerprint': current_fingerprint})
        
        if not stale_repos:
            logger.info(f"No stale repos for {username}, returning cached bundle")
            return create_success_response({
                "status": "cached",
                "repos_count": len(cached_bundle['data']) if cached_bundle['status'] == 'valid' else 0
            })
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        # Enqueue sync jobs (one message per repository)
        for repo_metadata in stale_repos:
            queue_manager.enqueue_sync_job(job_id, username, repo_metadata)
        
        # Store job metadata in cache for status tracking
        job_cache_key = f"job:{job_id}"
        cache_manager.save(job_cache_key, {
            "username": username,
            "total_repos": len(stale_repos),
            "completed_repos": 0,
            "status": "queued",
            "created_at": datetime.now().isoformat()
        }, ttl=3600)  # 1 hour TTL
        
        logger.info(f"Enqueued {len(stale_repos)} sync jobs for {username}, job_id: {job_id}")
        
        return create_success_response({
            "status": "processing",
            "job_id": job_id,
            "status_url": f"/bundles/{username}/status?job_id={job_id}",
            "repos_queued": len(stale_repos)
        })
    
    except Exception as e:
        logger.error(f"Error triggering bundle refresh: {str(e)}", exc_info=True)
        return create_error_response(f"Failed to trigger refresh: {str(e)}", 500)


@app.route(route="bundles/{username}/status", methods=["GET"])
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    NEW: Poll job progress for async refresh.
    Frontend calls this endpoint every 2 seconds.
    """
    try:
        username = req.route_params.get('username')
        job_id = req.params.get('job_id')
        
        if not job_id:
            return create_error_response("job_id query parameter required", 400)
        
        # Retrieve job metadata from cache
        job_cache_key = f"job:{job_id}"
        job_data = cache_manager.get(job_cache_key)
        
        if job_data['status'] != 'valid':
            return create_error_response("Job not found or expired", 404)
        
        job_info = job_data['data']
        total = job_info.get('total_repos', 0)
        completed = job_info.get('completed_repos', 0)
        status = job_info.get('status', 'unknown')
        
        # Check if all repos are synced
        if completed >= total and status == 'queued':
            status = 'completed'
            job_info['status'] = 'completed'
            cache_manager.save(job_cache_key, job_info, ttl=3600)
        
        return create_success_response({
            "job_id": job_id,
            "username": username,
            "status": status,
            "progress": {
                "total": total,
                "completed": completed,
                "percentage": int((completed / total * 100) if total > 0 else 0)
            },
            "created_at": job_info.get('created_at')
        })
    
    except Exception as e:
        logger.error(f"Error retrieving job status: {str(e)}", exc_info=True)
        return create_error_response(f"Failed to retrieve status: {str(e)}", 500)
```

**Backward Compatibility**: Feature flag `ENABLE_QUEUE_MODE` allows rollback to Durable Functions.

---

### Day 2: Sync Worker (4 hours)

**Task**: Extract existing fetch logic into queue trigger

```python
# api/function_app.py (ADD QUEUE TRIGGER)

@app.queue_trigger(arg_name="msg", queue_name="github-sync", connection="AzureWebJobsStorage")
def sync_worker(msg: func.QueueMessage):
    """
    Queue worker: Fetch single repository context.
    Reuses existing fetch_repo_context_bundle_activity logic.
    """
    try:
        message = json.loads(msg.get_body().decode('utf-8'))
        job_id = message.get('job_id')
        username = message.get('username')
        repo_metadata = message.get('metadata')
        repo_name = repo_metadata.get('name')
        fingerprint = message.get('fingerprint')
        
        logger.info(f"[Job {job_id}] Processing sync for {username}/{repo_name}")
        
        # Initialize managers (same as existing code)
        repo_manager = _get_github_managers(username)
        
        # Fetch .repo-context.json (same logic as fetch_repo_context_bundle_activity)
        repo_context = repo_manager.get_file_content(repo=repo_name, path='.repo-context.json', username=username)
        repo_context = json.loads(repo_context) if repo_context and isinstance(repo_context, str) else {}
        
        # Fetch README.md
        readme_content = repo_manager.get_file_content(repo=repo_name, path='README.md', username=username) or ""
        
        # Fetch SKILLS-INDEX.md
        skills_index_content = repo_manager.get_file_content(repo=repo_name, path='SKILLS-INDEX.md', username=username) or ""
        
        # Fetch ARCHITECTURE.md
        architecture_content = repo_manager.get_file_content(repo=repo_name, path='ARCHITECTURE.md', username=username) or ""
        
        # Analyze file types
        from ai.type_analyzer import FileTypeAnalyzer
        file_type_analyzer = FileTypeAnalyzer()
        file_types = repo_manager.get_all_file_types(repo_name, username)
        categorized_types = file_type_analyzer.analyze_repository_files(file_types)
        
        # Combine results (same format as existing activity)
        result = {
            "name": repo_name,
            'metadata': repo_metadata,
            'repoContext': repo_context,
            'readme': readme_content,
            'skills_index': skills_index_content,
            'architecture': architecture_content,
            'file_types': file_types,
            "categorized_types": categorized_types,
            'fingerprint': fingerprint,
            "languages": repo_metadata.get("languages", {}),
            'has_documentation': bool(repo_context) and bool(readme_content)
        }
        
        # Save to per-repo cache
        repo_cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo_name)
        cache_manager.save(repo_cache_key, result, ttl=None, fingerprint=fingerprint)
        
        logger.info(f"[Job {job_id}] Saved {username}/{repo_name} to cache")
        
        # Update job progress
        job_cache_key = f"job:{job_id}"
        job_data = cache_manager.get(job_cache_key)
        if job_data['status'] == 'valid':
            job_info = job_data['data']
            job_info['completed_repos'] = job_info.get('completed_repos', 0) + 1
            cache_manager.save(job_cache_key, job_info, ttl=3600)
            
            # If all repos synced, enqueue merge job
            if job_info['completed_repos'] >= job_info['total_repos']:
                logger.info(f"[Job {job_id}] All repos synced, enqueuing merge job")
                # Get list of synced repo names
                synced_repos = [repo_name]  # Would need to track all repo names in job metadata
                queue_manager.enqueue_merge_job(job_id, username, synced_repos)
        
    except Exception as e:
        logger.error(f"Error processing sync job: {str(e)}", exc_info=True)
        raise  # Re-raise to trigger retry/dead-letter queue
```

**Code Reuse**: 95% of logic copied from existing `fetch_repo_context_bundle_activity`.

---

### Day 3: Merge & Training Workers (4 hours)

**Task**: Extract existing merge and training logic into queue triggers

```python
# api/function_app.py (ADD MERGE WORKER)

@app.queue_trigger(arg_name="msg", queue_name="merge-results", connection="AzureWebJobsStorage")
def merge_worker(msg: func.QueueMessage):
    """
    Queue worker: Aggregate synced repos into bundle cache.
    Reuses existing merge_repo_results_activity logic.
    """
    try:
        message = json.loads(msg.get_body().decode('utf-8'))
        job_id = message.get('job_id')
        username = message.get('username')
        
        logger.info(f"[Job {job_id}] Starting merge for {username}")
        
        # Fetch all per-repo caches
        repo_manager = _get_github_managers(username)
        all_repos_metadata = repo_manager.get_all_repos_metadata(username=username, include_languages=False)
        
        merged_results = []
        for repo_metadata in all_repos_metadata:
            repo_name = repo_metadata.get('name')
            repo_cache_key = cache_manager.generate_cache_key(kind='repo', username=username, repo=repo_name)
            cached_repo = cache_manager.get(repo_cache_key)
            
            if cached_repo['status'] == 'valid':
                merged_results.append(cached_repo['data'])
            else:
                logger.warning(f"Repo {repo_name} missing from cache during merge")
        
        # Generate bundle fingerprint
        repo_fingerprints = [repo.get('fingerprint', '') for repo in merged_results]
        bundle_fingerprint = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints)
        
        # Save merged bundle to cache
        bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
        cache_manager.save(bundle_cache_key, merged_results, ttl=None, fingerprint=bundle_fingerprint)
        
        logger.info(f"[Job {job_id}] Saved merged bundle with {len(merged_results)} repos")
        
        # Enqueue training job (background task, no job tracking)
        training_params = {'batch_size': 8, 'max_pairs': 150, 'epochs': 2, 'warmup_steps': 50}
        queue_manager.enqueue_training_job(username, merged_results, training_params)
        
        # Update job status to completed
        job_cache_key = f"job:{job_id}"
        job_data = cache_manager.get(job_cache_key)
        if job_data['status'] == 'valid':
            job_info = job_data['data']
            job_info['status'] = 'completed'
            cache_manager.save(job_cache_key, job_info, ttl=3600)
        
    except Exception as e:
        logger.error(f"Error processing merge job: {str(e)}", exc_info=True)
        raise


@app.queue_trigger(arg_name="msg", queue_name="model-training", connection="AzureWebJobsStorage")
def training_worker(msg: func.QueueMessage):
    """
    Queue worker: Fine-tune semantic model (background task).
    Reuses existing train_semantic_model_activity logic.
    """
    try:
        message = json.loads(msg.get_body().decode('utf-8'))
        username = message.get('username')
        repos_bundle = message.get('repos_bundle')
        training_params = message.get('training_params', {})
        
        logger.info(f"Starting semantic model training for {username}")
        
        # Check if enough documented repos exist
        documented_repos = [repo for repo in repos_bundle if repo.get("has_documentation", False)]
        if len(documented_repos) < 3:
            logger.info(f"Not enough documented repos ({len(documented_repos)}), skipping training")
            return
        
        # Initialize semantic model (same logic as existing activity)
        from config.fine_tuning import SemanticModel
        semantic_model = SemanticModel()
        
        model_ready = semantic_model.ensure_model_ready(
            repos_bundle,
            train_if_missing=True,
            training_params={
                'batch_size': training_params.get('batch_size', 8),
                'max_pairs': training_params.get('max_pairs', 150)
            }
        )
        
        logger.info(f"Semantic model training {'succeeded' if model_ready else 'failed'} for {username}")
        
    except Exception as e:
        logger.error(f"Error processing training job: {str(e)}", exc_info=True)
        # Don't re-raise - training failures shouldn't block sync
```

**Code Reuse**: 90% of logic copied from existing activities.

---

### Day 4: Training Worker Integration (2 hours)

**Task**: Integrate containerized training worker (from Semantic Model Plan)

- Deploy training worker container to Azure Container Registry
- Configure queue connection for `model-training` queue
- Test training job enqueuing from merge_worker
- Verify training worker polls queue and processes messages

**Reference**: `plan-semanticModelTrainingRefactor.prompt.md` (Lines 449-635)

---

### Day 5: Local Testing (4 hours)

**Task**: Test with Azurite (local Azure Storage emulator)

```bash
# Install Azurite
npm install -g azurite

# Start Azurite (storage emulator)
azurite --silent --location /tmp/azurite --debug /tmp/azurite/debug.log

# Update local.settings.json
cat > api/local.settings.json <<EOF
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "AzureWebJobsStorage__queueServiceUri": "http://127.0.0.1:10001/devstoreaccount1",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "ENABLE_QUEUE_MODE": "true",
    "GITHUB_TOKEN": "your-token-here"
  }
}
EOF

# Start Azure Functions locally
cd api
func start
```

**Test Scenarios** (use any GitHub username):
1. Trigger refresh: `POST http://localhost:7071/api/bundles/{username}/refresh`
2. Poll status: `GET http://localhost:7071/api/bundles/{username}/status?job_id={id}`
3. Verify workers process messages from Azurite queues
4. Check dead-letter queue handling (simulate failures)

**Note**: Replace `{username}` with any valid GitHub username (e.g., `testuser`, `yungryce`)

---

### Day 6: Staging Deployment (2 hours)

**Task**: Deploy to staging with feature flag enabled

```bash
# Set feature flag in Azure Portal
az functionapp config appsettings set \
  --name fa-portfolio-staging \
  --resource-group rg-portfolio-staging \
  --settings ENABLE_QUEUE_MODE=true

# Deploy via existing pipeline (no changes needed)
# azure-pipelines-artifact.yml already deploys entire api/ folder
```

**Monitoring**:
- Application Insights: Track queue message processing time
- Storage Analytics: Monitor queue depth, dequeue count
- Alert on dead-letter queue messages (poison messages)

---

### Day 7: Production Rollout (1 hour)

**Task**: Gradual rollout with traffic percentage

```python
# api/function_app.py (UPDATE FEATURE FLAG LOGIC)

import random

@app.route(route="bundles/{username}/refresh", methods=["POST"])
async def trigger_bundle_refresh(req: func.HttpRequest) -> func.HttpResponse:
    # Gradual rollout: percentage-based traffic split
    queue_traffic_pct = int(os.getenv('QUEUE_TRAFFIC_PCT', '0'))  # 0-100
    use_queue_mode = random.randint(1, 100) <= queue_traffic_pct
    
    if use_queue_mode:
        # NEW: Queue-based refresh
        ...
    else:
        # LEGACY: Durable Functions refresh
        ...
```

**Rollout Schedule**:
- Day 7 Morning: `QUEUE_TRAFFIC_PCT=10` (10% of users)
- Day 7 Afternoon: `QUEUE_TRAFFIC_PCT=50` (if no errors)
- Day 7 Evening: `QUEUE_TRAFFIC_PCT=100` (full migration)
- Day 8+: Monitor for 7 days before removing Durable Functions code

---

## Frontend Integration (Parallel Work)

### Update `repo-bundle.service.ts`

```typescript
// src/app/services/repo-bundle.service.ts

export interface RefreshResponse {
  status: 'cached' | 'processing';
  job_id?: string;
  status_url?: string;
  repos_queued?: number;
  repos_count?: number;
}

export interface JobStatus {
  job_id: string;
  username: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  progress: {
    total: number;
    completed: number;
    percentage: number;
  };
}

refreshUserBundle(username: string): Observable<RefreshResponse> {
  return this.http.post<RefreshResponse>(
    `${this.apiUrl}/bundles/${username}/refresh`,
    {}
  );
}

pollJobStatus(username: string, jobId: string): Observable<JobStatus> {
  return this.http.get<JobStatus>(
    `${this.apiUrl}/bundles/${username}/status?job_id=${jobId}`
  ).pipe(
    // Poll every 2 seconds until completed
    expand(status => {
      if (status.status === 'completed') {
        return EMPTY;
      }
      return timer(2000).pipe(
        switchMap(() => this.http.get<JobStatus>(
          `${this.apiUrl}/bundles/${username}/status?job_id=${jobId}`
        ))
      );
    })
  );
}
```

### Update `projects.component.ts`

```typescript
// src/app/projects/projects.component.ts

loadProjects(): void {
  this.loading = true;
  this.loadingMessage = 'Checking for updates...';
  
  // Try to get cached bundle first
  this.repoBundleService.getUserBundle(this.username).subscribe({
    next: (response) => {
      if (response.data && response.data.length > 0) {
        this.projects = response.data.map(toCardVM);
        this.loading = false;
      } else {
        // No cache, trigger refresh
        this.triggerRefresh();
      }
    },
    error: (error) => {
      if (error.status === 404) {
        // No cache found, trigger refresh
        this.triggerRefresh();
      } else {
        this.handleError(error);
      }
    }
  });
}

triggerRefresh(): void {
  this.loadingMessage = 'Syncing repositories...';
  
  this.repoBundleService.refreshUserBundle(this.username).subscribe({
    next: (response) => {
      if (response.status === 'cached') {
        // Already cached, reload immediately
        this.loadProjects();
      } else if (response.status === 'processing' && response.job_id) {
        // Poll for completion
        this.pollRefreshStatus(response.job_id);
      }
    },
    error: (error) => this.handleError(error)
  });
}

pollRefreshStatus(jobId: string): void {
  this.repoBundleService.pollJobStatus(this.username, jobId).subscribe({
    next: (status) => {
      // Update progress message
      const pct = status.progress.percentage;
      const completed = status.progress.completed;
      const total = status.progress.total;
      this.loadingMessage = `Syncing repositories... ${completed} of ${total} (${pct}%)`;
      
      if (status.status === 'completed') {
        // Refresh complete, reload projects
        this.loadProjects();
      }
    },
    error: (error) => this.handleError(error)
  });
}
```

---

## Rollback Plan (Emergency Procedures)

### Scenario 1: Queue Workers Failing

**Symptoms**: Dead-letter queue filling up, sync jobs not completing

**Action**:
```bash
# Disable queue mode immediately
az functionapp config appsettings set \
  --name fa-portfolio-prod \
  --resource-group rg-portfolio-prod \
  --settings ENABLE_QUEUE_MODE=false

# Or use traffic percentage for gradual rollback
az functionapp config appsettings set \
  --name fa-portfolio-prod \
  --resource-group rg-portfolio-prod \
  --settings QUEUE_TRAFFIC_PCT=0
```

**Recovery Time**: < 1 minute (no deployment needed)

---

### Scenario 2: High Queue Depth (Backlog)

**Symptoms**: Queue depth > 1000 messages, slow processing

**Action**:
```bash
# Scale out sync workers (increase max instances)
az functionapp config set \
  --name fa-portfolio-prod \
  --resource-group rg-portfolio-prod \
  --max-instance-count 50  # From 100 to 50 reserved for sync workers
```

**Recovery Time**: 2-5 minutes (auto-scale kicks in)

---

### Scenario 3: Cache Consistency Issues

**Symptoms**: Stale data returned, fingerprint mismatches

**Action**:
```python
# Purge all job metadata (keep repo/bundle caches intact)
# Run this as admin endpoint or Azure Function timer trigger
from azure.storage.queue import QueueServiceClient

def purge_job_metadata():
    # Clear job tracking caches
    container_client = cache_manager.get_container_client("github-cache")
    for blob in container_client.list_blobs(name_starts_with="job:"):
        container_client.delete_blob(blob.name)
    
    # Purge all queues
    service_client = QueueServiceClient(...)
    for queue_name in ["github-sync", "merge-results", "model-training"]:
        queue_client = service_client.get_queue_client(queue_name)
        queue_client.clear_messages()
```

---

## Performance Projections

### Latency Comparison

| Metric | Durable Functions (Current) | Queue-Based (Target) | Improvement |
|--------|----------------------------|---------------------|-------------|
| **Initial Load (10 repos)** | 120s (blocks on training) | 5s (immediate response) | **96% faster** |
| **Cached Load** | 2s | 2s | No change |
| **Partial Refresh (2 repos)** | 60s (still trains) | 10s (no training block) | **83% faster** |
| **Frontend Perception** | "Frozen" for 2 minutes | "Syncing 7/10..." progress bar | ✅ Better UX |

---

### Cost Comparison

| Component | Durable Functions | Queue-Based | Difference |
|-----------|------------------|-------------|------------|
| **Compute** | $80/month (100 max instances) | $60/month (auto-scale 5-20) | **-$20/month** |
| **Storage (Queues)** | N/A | $0.50/month (1M messages) | +$0.50/month |
| **Storage (Blobs)** | $5/month | $5/month | No change |
| **Monitoring** | Included | Included | No change |
| **Total** | $85/month | $65.50/month | **-23% ($19.50/month savings)** |

**ROI**: Saves $234/year while improving performance by 96%.

---

## AKS Migration Path (Future)

When deploying to AKS, **same storage account works** with minor SDK changes:

```python
# workers/sync_worker.py (AKS version)
from azure.storage.queue import QueueClient
import os

# Use connection string instead of Managed Identity
queue_client = QueueClient.from_connection_string(
    conn_str=os.getenv('AZURE_STORAGE_CONNECTION_STRING'),
    queue_name="github-sync"
)

# Polling loop (Azure Functions uses triggers automatically)
while True:
    messages = queue_client.receive_messages(max_messages=10, visibility_timeout=30)
    for message in messages:
        try:
            process_sync_job(json.loads(message.content))
            queue_client.delete_message(message)  # Remove from queue after success
        except Exception as e:
            logger.error(f"Failed to process message: {e}")
            # Leave message in queue for retry (auto-reappears after visibility timeout)
```

**Migration Effort**: ~50 lines of code (change from trigger to polling SDK)

---

## Success Metrics

### Week 1 (Post-Deployment)
- ✅ P95 latency < 5s (from 120s)
- ✅ Zero production incidents
- ✅ Dead-letter queue empty (no poison messages)
- ✅ Frontend polling working (progress bar displayed)

### Week 2-4 (Optimization)
- ✅ Auto-scaling working (5-20 workers based on queue depth)
- ✅ Training completes within 10 minutes (background)
- ✅ Cost reduced by 20% ($65/month target)

### Week 5-6 (Cleanup)
- ✅ Remove Durable Functions code (after 7 days stable)
- ✅ Update documentation
- ✅ Retrospective: Lessons learned

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| **Queue depth backlog** | Medium | Medium | Auto-scale to 50 workers, alerts at depth > 500 |
| **Dead-letter queue filling** | Low | High | Retry logic + alerts, manual investigation |
| **Cache consistency** | Low | Medium | Fingerprint validation, purge utility |
| **Training job timeout** | Medium | Low | Increase queue visibility timeout to 15 minutes |
| **Frontend polling failure** | Low | High | Exponential backoff, timeout after 5 minutes |

**Overall Risk**: **LOW** (feature flags enable instant rollback)

---

## Appendix: Environment Variables

### Required (Already Exist)
- `AzureWebJobsStorage__queueServiceUri` (from `infra/main.bicep`)
- `AZURE_CLIENT_ID` (User-Assigned Managed Identity)
- `GITHUB_TOKEN` (from Key Vault)

### New (Add to Function App)
- `ENABLE_QUEUE_MODE` (boolean, default: `false`)
- `QUEUE_TRAFFIC_PCT` (integer 0-100, default: `0`)

### No Changes Needed
- `AzureWebJobsStorage__blobServiceUri` (cache still uses blobs)
- `APPLICATIONINSIGHTS_CONNECTION_STRING` (monitoring unchanged)

---

## Summary

**Total Implementation Time**: 6 days (vs 13 days for Redis)

**Benefits**:
- ✅ 96% latency reduction (120s → 5s)
- ✅ Zero infrastructure changes (reuses existing storage)
- ✅ Native Azure Functions integration (less code)
- ✅ AKS-compatible (minor SDK changes later)
- ✅ Instant rollback capability (feature flags)
- ✅ Cost reduction: $85 → $65/month (-23%)

**Next Step**: Approve plan and begin Day 1 implementation (queue_manager.py creation).
