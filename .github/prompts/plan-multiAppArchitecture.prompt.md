# Multi-App Architecture Migration Plan

**Date**: November 17, 2025  
**Status**: Planning Phase  
**Objective**: Migrate from monolithic Azure Durable Functions to distributed queue-based microservices with multi-user support

---

## Executive Summary

Refactor the portfolio application from a single Azure Function App (with hardcoded username 'yungryce') to a cloud-agnostic multi-app architecture where each service scales independently and supports any GitHub username.

### Key Transformations

| Aspect | Current State | Target State | Impact |
|--------|---------------|--------------|--------|
| **Architecture** | Monolithic Durable Functions | 4 independent Function Apps | Independent scaling, faster delivery |
| **Latency** | 60-1200s blocking orchestration | <5s API response + async workers | 96% latency reduction |
| **Username** | Hardcoded 'yungryce' in 6 locations | Dynamic username from request | Multi-tenant ready |
| **Deployment** | Single Function App | API Gateway + 3 workers + shared package | Fault isolation |
| **Infrastructure (PRIMARY)** | Azure-locked (Durable Functions) | **Function Apps + Azure Storage Queues** | Azure-optimized, serverless |
| **Infrastructure (FUTURE)** | N/A | **Containers on AKS** (see plan-aksDeployment.prompt.md) | Portable to AWS/GCP/GKE |
| **Cost** | $85/month (always-on) | $47/month (serverless, scale-to-zero) | 45% reduction |

**IMPORTANT CLARIFICATION**: 
- **PRIMARY Deployment**: Azure Function Apps for API Gateway, Sync Worker, Merge Worker
- **EXCEPTION**: Training Worker containerized (ACI/AKS) due to high CPU/GPU requirements (4 vCPU, 16GB RAM)
- **FUTURE Alternative**: Full AKS deployment (all 4 workers) - See `plan-aksDeployment.prompt.md` for details

---

## Problem Statement

### Current Architecture Issues

**1. Performance Bottleneck**
- Durable Functions orchestrator blocks 60-120s before responding
- Training activity (`yield context.call_activity('train_semantic_model_activity')`) is synchronous despite "background" comment
- No parallelization opportunity (all in orchestrator control flow)

**2. Username Hardcoding (Multi-User Blocker)**
```
Location                                      | Severity  | Line
----------------------------------------------|-----------|------
api/config/cache_manager.py                  | CRITICAL  | 64-88 (fallback to 'yungryce')
api/config/github_api.py                     | CRITICAL  | 11-14 (fallback to 'yungryce')
src/app/projects/projects.component.ts       | CRITICAL  | 37
src/app/projects/project/project.component.ts| CRITICAL  | 37
src/app/assistant/assistant.component.ts     | CRITICAL  | 21
api/starter.sh                               | MEDIUM    | Line 3 (script default)
```

**3. Monolithic Design**
- `function_app.py`: 705 lines containing HTTP endpoints + orchestrator + 4 activities
- Tight coupling: All managers imported at module level
- Cannot scale workers independently (all share same Function App instances)
- No fault isolation (training failure impacts API availability)

**4. Azure Lock-In**
- Durable Functions orchestration state tied to Azure Storage
- Cannot migrate to AWS/GCP without complete rewrite
- Vendor-specific monitoring (Application Insights tightly integrated)

---

## Target Architecture

### Directory Structure

```
portfolio/
├── apps/                                    # NEW: Microservices root
│   ├── shared/                              # Common code package
│   │   ├── __init__.py
│   │   ├── setup.py                         # Installable Python package
│   │   ├── cache/
│   │   │   ├── cache_manager.py         # From api/config/
│   │   │   └── fingerprint_manager.py   # From api/config/
│   │   ├── github/
│   │   │   ├── github_api.py            # From api/config/
│   │   │   └── github_repo_manager.py   # From api/config/
│   │   ├── ai/
│   │   │   ├── fine_tuning.py           # From api/config/
│   │   │   ├── repo_scoring_service.py  # From api/ai/
│   │   │   └── type_analyzer.py         # From api/ai/
│   │   ├── models/
│   │   │   └── schemas.py               # Pydantic models for queues
│   │   └── config/
│   │       └── settings.py              # Environment variable management
│   │   └── tests/
│   │
│   ├── api-gateway/                         # PRIMARY: Azure Function App HTTP triggers
│   │   ├── function_app.py                  # HTTP triggers: @app.route('/bundles/{username}')
│   │   ├── requirements.txt
│   │   ├── host.json                        # Function App configuration
│   │   ├── routes/
│   │   │   ├── bundles.py                   # GET /bundles/{username}, POST /bundles/{username}/refresh
│   │   │   ├── repos.py                     # GET /bundles/{username}/{repo}
│   │   │   ├── ai.py                        # POST /ai
│   │   │   ├── status.py                    # GET /status/{job_id}
│   │   │   └── health.py                    # GET /health (readiness/liveness)
│   │   ├── dependencies.py                  # Shared injections
│   │   └── models/
│   │       ├── requests.py                  # Pydantic request schemas
│   │       └── responses.py                 # Pydantic response schemas
│   │
│   ├── sync-worker/                         # PRIMARY: Azure Function App queue trigger
│   │   ├── function_app.py                  # @app.queue_trigger('github-sync')
│   │   ├── requirements.txt
│   │   ├── host.json
│   │   ├── github_sync.py                   # Core sync logic
│   │   └── tests/
│   │
│   ├── merge-worker/                        # PRIMARY: Azure Function App queue trigger
│   │   ├── function_app.py                  # @app.queue_trigger('merge-results')
│   │   ├── requirements.txt
│   │   ├── host.json
│   │   ├── merge_logic.py                   # Fresh + cached merge
│   │   └── tests/
│   │
│   └── training-worker/                     # EXCEPTION: Containerized (ACI/AKS only)
│       ├── Dockerfile                       # Multi-stage: CPU + GPU variants
│       ├── requirements.txt
│       ├── worker.py                        # Queue consumer main loop
│       ├── model_training.py                # SemanticModel training orchestrator
│       ├── models/
│       │   ├── semantic_model.py            # Standalone (no Function App deps)
│       │   └── model_registry.py            # Experiment configurations
│       └── tests/
│
├── src/                                     # Angular Frontend (location unchanged)
│   ├── app/
│   │   ├── services/
│   │   │   ├── config.service.ts            # UPDATED: Remove hardcoded username
│   │   │   ├── repo-bundle.service.ts       # UPDATED: Add polling methods
│   │   │   └── assistant.service.ts         # UPDATED: Dynamic username
│   │   └── projects/
│   │       ├── projects.component.ts        # UPDATED: Get username from route
│   │       └── project/
│   │           └── project.component.ts     # UPDATED: Get username from route
│   └── environments/
│       ├── environment.ts                   # UPDATED: apiUrl points to Gateway
│       └── environment.development.ts
│
├── infra/                                   # Infrastructure as Code
│   ├── main.bicep                           # UPDATED: Deploy Function Apps + Storage Queues
│   ├── function-apps.bicep                  # NEW: 3 Function Apps (API, Sync, Merge)
│   ├── container-instance.bicep             # NEW: Training Worker (ACI) - high compute
│   ├── acr.bicep                            # NEW: Container Registry (for training worker)
│   ├── storage-queues.bicep                 # NEW: Azure Storage Queues configuration
│   └── monitoring.bicep                     # NEW: Application Insights for multi-app
│
└── api/                                     # DEPRECATED (migrated to apps/)
    └── function_app.py                      # Remove after migration complete
```

---

## Data Flow Diagrams

### Current Flow (Synchronous, Blocking)

```
┌─────────────┐
│  Frontend   │
│ (Angular)   │
└──────┬──────┘
       │ POST /api/orchestrator_start?username=yungryce
       │ (hardcoded username)
       ↓
┌──────────────────────────────────────────────────┐
│         Azure Durable Functions                   │
│                                                   │
│  ┌─────────────────────────────────────────┐    │
│  │ repo_context_orchestrator                │    │
│  │                                          │    │
│  │ 1. get_stale_repos_activity (5s)        │    │
│  │    ↓                                     │    │
│  │ 2. fetch_repo × N in parallel (60s)     │◄── BLOCKS HERE
│  │    ↓                                     │    │
│  │ 3. merge_repo_results_activity (2s)     │    │
│  │    ↓                                     │    │
│  │ 4. train_semantic_model_activity (60s)  │◄── BLOCKS HERE
│  │    [yield = synchronous wait]           │    │
│  └─────────────────────────────────────────┘    │
│                                                   │
└──────────────┬────────────────────────────────────┘
               │
               │ After 120s total
               ↓
┌─────────────┐
│  Frontend   │ ← Response: { data: [...] }
└─────────────┘
```

**Total Latency**: 5s + 60s + 2s + 60s = **127 seconds**

---

### Target Flow (Asynchronous, Non-Blocking)

```
┌─────────────┐
│  Frontend   │
│ (Angular)   │
└──────┬──────┘
       │ 1. POST /api/bundles/{username}/refresh
       │    (username from route param)
       ↓
┌──────────────────────────────────────────────────┐
│     API Gateway (Function App HTTP Trigger)      │
│                                                   │
│  - Enqueue job to 'github-sync' queue            │
│  - Generate job_id (UUID)                        │
│  - Save job status to cache: {job_id: "queued"} │
│                                                   │
└──────┬───────────────────────────────────────────┘
       │
       │ 2. Immediate response (<5s)
       ↓
┌─────────────┐
│  Frontend   │ ← { job_id: "uuid-123", status_url: "..." }
└──────┬──────┘
       │
       │ 3. Poll GET /api/status/uuid-123 every 2s
       │
       ↓ (async background processing continues)

┌─────────────────────────┐    ┌─────────────────────────┐
│   Sync Worker           │    │   Merge Worker          │
│   (Scale: 0-10)         │    │   (Scale: 0-5)          │
│                         │    │                         │
│ - Receive from queue    │    │ - Receive from queue    │
│ - Fetch GitHub repos    │    │ - Merge fresh + cached  │
│ - Update cache status   │    │ - Save bundle to cache  │
│ - Enqueue to            │───→│ - Enqueue to            │
│   'merge-results'       │    │   'model-training'      │
└─────────────────────────┘    └────────┬────────────────┘
                                        │
                                        ↓
                           ┌─────────────────────────┐
                           │  Training Worker        │
                           │  (Scale: 0-2)           │
                           │                         │
                           │ - Receive from queue    │
                           │ - Train SemanticModel   │
                           │ - Upload to Blob        │
                           │ - Update cache status   │
                           └─────────────────────────┘

┌─────────────┐
│  Frontend   │ ← Poll returns: { status: "completed", data: {...} }
└─────────────┘
```

**Total Latency (User-Perceived)**: <5s API response + background processing (user continues browsing)

---

## Migration Phases

### Phase 1: Username Flexibility (Week 1) ✅ CRITICAL PATH

**Objective**: Remove all hardcoded 'yungryce' references, enable dynamic username from requests

#### Tasks

**1.1 Backend: Remove Fallback Logic**
```python
# api/config/cache_manager.py
# BEFORE:
def generate_cache_key(self, kind, **kwargs):
    username = kwargs.get('username') or 'yungryce'  # ❌ Remove this
    
# AFTER:
def generate_cache_key(self, kind, **kwargs):
    username = kwargs.get('username')
    if not username:
        raise ValueError("Username is required for cache key generation")
```

Files to update:
- `api/config/cache_manager.py:64-88` - Remove fallback, add validation
- `api/config/github_api.py:11-14` - Remove fallback, require username in `__init__`
- `api/function_app.py:34-38` - Update `_get_github_managers()` to validate username

**1.2 Frontend: Dynamic Username from Routes**
```typescript
// BEFORE (src/app/projects/projects.component.ts:37):
username = 'yungryce';  // ❌ Hardcoded

// AFTER:
constructor(private route: ActivatedRoute) {}

ngOnInit() {
  this.username = this.route.snapshot.paramMap.get('username') || 'yungryce';
  // Or get from ConfigService in Phase 2
}
```

Files to update:
- `src/app/projects/projects.component.ts:37` - Add route parameter
- `src/app/projects/project/project.component.ts:37` - Add route parameter
- `src/app/assistant/assistant.component.ts:21` - Add route parameter
- `src/app/app.routes.ts` - Add `/:username` path segment to projects/assistant routes

**1.3 Testing**
- Create test data for username 'testuser' (separate cache namespace)
- Verify cache keys use correct username: `repos_bundle_context_testuser`
- Test frontend with URL: `/projects/testuser`

**Deliverables:**
- ✅ No hardcoded 'yungryce' in application code (only in docs/examples)
- ✅ Username required in all API calls (fail fast if missing)
- ✅ Frontend supports `/:username` route parameter
- ✅ Cache keys scoped by username

**Time Estimate**: 6 hours

---

### Phase 2: Extract Shared Code (Week 2) 🔄 PREP FOR MULTI-APP

**Objective**: Create `apps/shared/` package with reusable components for all workers

#### Tasks

**2.1 Create Shared Package Structure**
```bash
mkdir -p apps/shared/{cache,github,ai,models,config}
touch apps/shared/setup.py
touch apps/shared/__init__.py
```

**2.2 Move Core Modules**
```
api/config/cache_manager.py          → apps/shared/cache/cache_manager.py
api/config/fingerprint_manager.py    → apps/shared/cache/fingerprint_manager.py
api/config/github_api.py              → apps/shared/github/github_api.py
api/config/github_repo_manager.py     → apps/shared/github/github_repo_manager.py
api/config/fine_tuning.py             → apps/shared/ai/fine_tuning.py
api/ai/repo_scoring_service.py        → apps/shared/ai/repo_scoring_service.py
api/ai/type_analyzer.py               → apps/shared/ai/type_analyzer.py
api/ai/ai_assistant.py                → apps/shared/ai/ai_assistant.py
```

**2.3 Create setup.py**
```python
# apps/shared/setup.py
from setuptools import setup, find_packages

setup(
    name="portfolio-shared",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "azure-storage-blob>=12.19.0",
        "azure-identity>=1.15.0",
        "pydantic>=2.5.0",
        "torch>=2.2.2",
        "sentence-transformers>=2.6.1",
        "groq>=0.4.0",
    ],
    python_requires=">=3.11",
)
```

**2.4 Update Imports in function_app.py**
```python
# BEFORE:
from config.cache_manager import cache_manager
from config.github_api import GitHubAPI

# AFTER:
from cache import cache_manager
from github import GitHubAPI
```

**2.5 Create Queue Message Schemas**
```python
# apps/shared/models/schemas.py
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class SyncJobMessage(BaseModel):
    """Message for github-sync queue"""
    job_id: str = Field(..., description="Unique job identifier")
    username: str = Field(..., min_length=1, description="GitHub username")
    force_refresh: bool = Field(default=False)
    requested_at: str  # ISO timestamp

class MergeJobMessage(BaseModel):
    """Message for merge-results queue"""
    job_id: str
    username: str
    fresh_repos: List[Dict]
    cached_bundle: List[Dict]

class TrainingJobMessage(BaseModel):
    """Message for model-training queue"""
    job_id: str
    username: str
    repos_bundle: List[Dict]
    training_params: Dict = Field(default_factory=dict)
    experiment_name: str = Field(default='default')
```

**Deliverables:**
- ✅ `apps/shared/` package installable with `pip install -e .`
- ✅ All core logic extracted from `api/` to shared package
- ✅ Pydantic schemas for queue messages (type-safe integration)
- ✅ `function_app.py` still works with updated imports
- ✅ Unit tests pass for shared modules

**Time Estimate**: 8 hours

---

### Phase 3: Configure Queue Infrastructure (Week 3) ☁️ INFRASTRUCTURE

**Objective**: Configure Azure Storage Queues using existing storage account (zero new infrastructure cost)

#### Tasks

**3.1 Configure Azure Storage Queues**
```bicep
// infra/storage-queues.bicep
// See plan-azureStorageQueuesArchitecture.prompt.md for detailed implementation

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: 'stportfolio${uniqueString(resourceGroup().id)}'
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource githubSyncQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  parent: queueService
  name: 'github-sync'
}

resource mergeResultsQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  parent: queueService
  name: 'merge-results'
}

resource modelTrainingQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  parent: queueService
  name: 'model-training'
}

output storageAccountName string = storageAccount.name
output queueEndpoint string = storageAccount.properties.primaryEndpoints.queue
```

**Note**: Uses existing Azure Storage account (zero additional cost). Detailed implementation in `plan-azureStorageQueuesArchitecture.prompt.md`.

**3.2 Deploy Container Instance Infrastructure (Training Worker Only)**
```bicep
// infra/container-instance.bicep
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acrportfolio${uniqueString(resourceGroup().id)}'
  location: location
  sku: {
    name: 'Basic'  // $5/month
  }
  properties: {
    adminUserEnabled: false  // Use Managed Identity
    publicNetworkAccess: 'Enabled'
  }
}

resource containerGroup 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: 'aci-training-worker'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    containers: [
      {
        name: 'training-worker'
        properties: {
          image: '${acr.properties.loginServer}/training-worker:latest'
          resources: {
            requests: {
              cpu: 4
              memoryInGB: 16
            }
          }
          environmentVariables: [
            {
              name: 'AZURE_STORAGE_QUEUE_URL'
              value: storageAccount.properties.primaryEndpoints.queue
            }
          ]
        }
      }
    ]
    osType: 'Linux'
    restartPolicy: 'Never'  // Start only when triggered by queue message
  }
}

output acrLoginServer string = acr.properties.loginServer
```

**3.3 Create Queue Monitoring Alerts**
```bicep
// infra/monitoring.bicep
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'portfolio-alerts'
  location: 'global'
  properties: {
    groupShortName: 'portfolio'
    enabled: true
    emailReceivers: [
      {
        name: 'AdminEmail'
        emailAddress: 'admin@example.com'
      }
    ]
  }
}

resource poisonQueueAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'poison-queue-alert'
  location: 'global'
  properties: {
    description: 'Alert when poison message queue has messages (failed after 3 retries)'
    severity: 2
    enabled: true
    scopes: [
      storageAccount.id
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          name: 'PoisonQueueDepth'
          metricName: 'QueueMessageCount'
          metricNamespace: 'Microsoft.Storage/storageAccounts/queueServices'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Average'
          dimensions: [
            {
              name: 'QueueName'
              operator: 'Include'
              values: [
                'github-sync-poison'
                'merge-results-poison'
                'model-training-poison'
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}
```
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'DLQ Depth'
          metricName: 'QueueMessageCount'
          metricNamespace: 'Microsoft.Storage/storageAccounts/queueServices'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Average'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}
```

**3.4 Deploy Resources**
```bash
cd infra
az deployment group create \
  --resource-group rg-portfolio-prod \
  --template-file main.bicep \
  --parameters environment=prod
```

**Deliverables:**
- ✅ Azure Storage Queues configured (github-sync, merge-results, model-training)
- ✅ Poison message queues configured (automatic retry handling after 3 attempts)
- ✅ Azure Container Registry deployed (for training worker only)
- ✅ Container Instance infrastructure configured (training worker deployment target)
- ✅ Poison queue monitoring alerts configured
- ✅ Managed Identity has roles: queueDataContributor, blobDataContributor, acrPull

**Time Estimate**: 4 hours

**Reference**: See `plan-azureStorageQueuesArchitecture.prompt.md` for detailed queue implementation

---

### Phase 4: Build API Gateway (Week 4) 🚪 HTTP TRIGGERS

**Objective**: Create Function App with HTTP triggers to replace Durable Functions orchestrator

#### Tasks

**4.1 Create Function App with HTTP Triggers**
```python
# apps/api-gateway/function_app.py
import azure.functions as func
from azure.storage.queue import QueueClient
from azure.identity import DefaultAzureCredential
import uuid
import json
import os
from datetime import datetime
from cache import cache_manager
from models.schemas import SyncJobMessage

app = func.FunctionApp()

# Azure Storage Queue connection
queue_service_url = os.getenv('AZURE_STORAGE_QUEUE_URL')
credential = DefaultAzureCredential()

@app.route(route="bundles/{username}/refresh", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def refresh_bundle(req: func.HttpRequest) -> func.HttpResponse:
    """Non-blocking endpoint to trigger bundle refresh"""
    username = req.route_params.get('username')
    
    if not username:
        return func.HttpResponse(
            json.dumps({"error": "Username required"}),
            status_code=400,
            mimetype='application/json'
        )
    
    # Check cache first (unless force_refresh=true)
    try:
        req_body = req.get_json()
        force_refresh = req_body.get('force_refresh', False)
    except ValueError:
        force_refresh = False
    
    if not force_refresh:
        bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
        cached = cache_manager.get(bundle_cache_key)
        if cached.get('status') == 'valid':
            return func.HttpResponse(
                json.dumps({"status": "cached", "data": cached['data']}),
                mimetype='application/json'
            )
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Enqueue sync job to Azure Storage Queue
    message = SyncJobMessage(
        job_id=job_id,
        username=username,
        force_refresh=force_refresh,
        requested_at=datetime.utcnow().isoformat()
    )
    
    queue_client = QueueClient(
        account_url=queue_service_url,
        queue_name='github-sync',
        credential=credential
    )
    queue_client.send_message(message.model_dump_json())
    
    # Save job status to cache (Blob Storage)
    job_status_key = f'job:{job_id}'
    cache_manager.save(job_status_key, {
        'status': 'queued',
        'username': username,
        'created_at': datetime.utcnow().isoformat()
    }, ttl=3600)  # 1 hour TTL
    
    return func.HttpResponse(
        json.dumps({
            "job_id": job_id,
            "status": "queued",
            "status_url": f"/api/status/{job_id}"
        }),
        mimetype='application/json'
    )

@app.route(route="bundles/{username}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_bundle(req: func.HttpRequest) -> func.HttpResponse:
    """Get cached bundle (synchronous endpoint)"""
    username = req.route_params.get('username')
    
    if not username:
        return func.HttpResponse(
            json.dumps({"error": "Username required"}),
            status_code=400,
            mimetype='application/json'
        )
    
    bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
    result = cache_manager.get(bundle_cache_key)
    
    if result.get('status') != 'valid':
        return func.HttpResponse(
            json.dumps({"error": "Bundle not found. Use POST /bundles/{username}/refresh to generate."}),
            status_code=404,
            mimetype='application/json'
        )
    
    return func.HttpResponse(
        json.dumps({
            "username": username,
            "fingerprint": result.get('fingerprint'),
            "data": result.get('data')
        }),
        mimetype='application/json'
    )

@app.route(route="status/{job_id}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    """Poll job status"""
    job_id = req.route_params.get('job_id')
    
    if not job_id:
        return func.HttpResponse(
            json.dumps({"error": "Job ID required"}),
            status_code=400,
            mimetype='application/json'
        )
    
    job_status_key = f'job:{job_id}'
    job_data = cache_manager.get(job_status_key)
    
    if not job_data:
        return func.HttpResponse(
            json.dumps({"error": "Job not found or expired"}),
            status_code=404,
            mimetype='application/json'
        )
    
    status = job_data.get('status')
    
    if status == 'completed':
        # Fetch result from cache
        username = job_data.get('username')
        bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
        result = cache_manager.get(bundle_cache_key)
        return func.HttpResponse(
            json.dumps({
                "status": "completed",
                "data": result.get('data')
            }),
            mimetype='application/json'
        )
    
    return func.HttpResponse(
        json.dumps({
            "status": status,
            "message": job_data.get('message', ''),
            "progress": float(job_data.get('progress', 0))
        }),
        mimetype='application/json'
    )

@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """Health check endpoint"""
    return func.HttpResponse(
        json.dumps({"status": "healthy"}),
        mimetype='application/json'
    )
```

**4.2 Create host.json Configuration**
```json
// apps/api-gateway/host.json
{
  "version": "2.0",
  "logging": {
    "applicationInsights": {
      "samplingSettings": {
        "isEnabled": true,
        "maxTelemetryItemsPerSecond": 20
      }
    }
  },
  "extensionBundle": {
    "id": "Microsoft.Azure.Functions.ExtensionBundle",
    "version": "[4.*, 5.0.0)"
  },
  "functionTimeout": "00:10:00",
  "http": {
    "routePrefix": "api",
    "maxOutstandingRequests": 200,
    "maxConcurrentRequests": 100
  }
}
```

**4.3 Create requirements.txt**
```txt
# apps/api-gateway/requirements.txt
azure-functions>=1.18.0
azure-storage-queue>=12.9.0
azure-storage-blob>=12.19.0
azure-identity>=1.15.0
pydantic>=2.5.0

# Install shared package (in deployment)
# pip install -e ../shared
```

**4.4 Deploy Function App**
```bicep
// infra/function-apps.bicep
resource apiGatewayPlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: 'fp-api-gateway'
  location: location
  sku: {
    name: 'FC1'  // Flex Consumption
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true  // Linux
  }
}

resource apiGatewayApp 'Microsoft.Web/sites@2023-01-01' = {
  name: 'func-api-gateway'
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: apiGatewayPlan.id
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccount.name
        }
        {
          name: 'AZURE_STORAGE_QUEUE_URL'
          value: storageAccount.properties.primaryEndpoints.queue
        }
        {
          name: 'AZURE_STORAGE_BLOB_URL'
          value: storageAccount.properties.primaryEndpoints.blob
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: managedIdentity.properties.clientId
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
      ]
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      cors: {
        allowedOrigins: [
          'https://portfolio.yungryce.dev'
        ]
      }
    }
    httpsOnly: true
  }
}

output apiGatewayUrl string = 'https://${apiGatewayApp.properties.defaultHostName}'
```

**Deliverables:**
- ✅ Function App with HTTP triggers for `/bundles/*`, `/status/*`, `/health` endpoints
- ✅ Job status tracking in cache (job_id → status mapping)
- ✅ Deployed as Function App (Flex Consumption) with auto-scaling
- ✅ Native Application Insights integration
- ✅ CORS configured for Static Web App
- ✅ Azure Storage Queue integration with Managed Identity
- ✅ host.json configured for optimal performance

**Time Estimate**: 8 hours 

**Note**: For future AKS deployment, a FastAPI variant will be created. See `plan-aksDeployment.prompt.md` Phase 2 for containerized API Gateway implementation.

---

### Phase 5: Build Worker Services (Week 5-6) ⚙️ MICROSERVICES

**Objective**: Create sync, merge, and training workers to replace Durable Functions activities

#### Tasks

**5.1 Sync Worker (GitHub Data Fetcher)**
```python
# apps/sync-worker/worker.py
from azure.storage.queue import QueueClient
from azure.identity import DefaultAzureCredential
import json
import logging
from cache import cache_manager
from github import GitHubRepoManager, GitHubAPI
from models.schemas import SyncJobMessage, MergeJobMessage
from github_sync import process_sync_job
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    # Azure Storage Queue connection
    queue_service_url = os.getenv('AZURE_STORAGE_QUEUE_URL')
    credential = DefaultAzureCredential()
    queue_client = QueueClient(
        account_url=queue_service_url,
        queue_name='github-sync',
        credential=credential
    )
    
    logger.info("Sync worker started, polling 'github-sync' queue...")
    
    while True:
        # Poll queue for messages (receive batch of 1)
        messages = queue_client.receive_messages(messages_per_page=1, visibility_timeout=300)
        
        for msg in messages:
            try:
                message_data = json.loads(msg.content)
                message = SyncJobMessage.model_validate(message_data)
                
                logger.info(f"Processing sync job {message.job_id} for {message.username}")
                
                # Update status in cache (Blob Storage)
                job_status_key = f'job:{message.job_id}'
                cache_manager.save(job_status_key, {
                    'status': 'processing',
                    'message': 'Fetching GitHub repositories'
                }, ttl=3600)
                
                # Process job (fetch stale repos)
                fresh_repos, cached_bundle = process_sync_job(message.username)
                
                # Enqueue merge job to Azure Storage Queue
                merge_queue_client = QueueClient(
                    account_url=queue_service_url,
                    queue_name='merge-results',
                    credential=credential
                )
                merge_message = MergeJobMessage(
                    job_id=message.job_id,
                    username=message.username,
                    fresh_repos=fresh_repos,
                    cached_bundle=cached_bundle
                )
                merge_queue_client.send_message(merge_message.model_dump_json())
                
                # Update status in cache
                cache_manager.save(job_status_key, {
                    'status': 'syncing',
                    'message': 'Merging results'
                }, ttl=3600)
                
                # Delete message from queue after successful processing
                queue_client.delete_message(msg)
                
                logger.info(f"Sync job {message.job_id} completed, enqueued to merge")
                
            except Exception as e:
                logger.error(f"Sync job failed: {e}", exc_info=True)
                # Message will be retried automatically (visibility timeout expires)
                # After max retries, moves to dead-letter queue

if __name__ == '__main__':
    main()
```

```python
# apps/sync-worker/github_sync.py
from github import GitHubRepoManager, GitHubAPI
from cache import cache_manager, FingerprintManager
import os

def process_sync_job(username: str):
    """Fetch stale repositories for username (port of get_stale_repos_activity)"""
    
    github_token = os.getenv('GITHUB_TOKEN')
    api = GitHubAPI(token=github_token, username=username)
    repo_manager = GitHubRepoManager(api, username=username)
    
    # Fetch cached bundle
    bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=username)
    cached_bundle = cache_manager.get(bundle_cache_key)
    
    # Fetch current repo metadata
    all_repos_metadata = repo_manager.get_all_repos_metadata(username=username, include_languages=True)
    
    # Calculate fingerprints
    current_fingerprints = {
        repo.get('name'): FingerprintManager.generate_metadata_fingerprint(repo)
        for repo in all_repos_metadata if repo.get('name')
    }
    
    # ... (rest of fingerprint comparison logic from get_stale_repos_activity)
    
    return fresh_repos, cached_bundle
```

**5.2 Merge Worker (Data Consolidation)**
```python
# apps/merge-worker/worker.py
from azure.storage.queue import QueueClient
from azure.identity import DefaultAzureCredential
import json
import logging
import os
from cache import cache_manager, FingerprintManager
from models.schemas import MergeJobMessage, TrainingJobMessage
from merge_logic import merge_repos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    # Azure Storage Queue connection
    queue_service_url = os.getenv('AZURE_STORAGE_QUEUE_URL')
    credential = DefaultAzureCredential()
    queue_client = QueueClient(
        account_url=queue_service_url,
        queue_name='merge-results',
        credential=credential
    )
    
    logger.info("Merge worker started, polling 'merge-results' queue...")
    
    while True:
        # Poll queue for messages
        messages = queue_client.receive_messages(messages_per_page=1, visibility_timeout=600)
        
        for msg in messages:
            try:
                message_data = json.loads(msg.content)
                message = MergeJobMessage.model_validate(message_data)
                
                logger.info(f"Processing merge job {message.job_id} for {message.username}")
                
                # Merge fresh and cached repos
                merged_results = merge_repos(
                    username=message.username,
                    fresh_repos=message.fresh_repos,
                    cached_bundle=message.cached_bundle
                )
                
                # Cache merged bundle
                bundle_cache_key = cache_manager.generate_cache_key(kind='bundle', username=message.username)
                repo_fingerprints = [r.get('fingerprint', '') for r in merged_results]
                bundle_fingerprint = FingerprintManager.generate_bundle_fingerprint(repo_fingerprints)
                cache_manager.save(bundle_cache_key, merged_results, ttl=None, fingerprint=bundle_fingerprint)
                
                # Enqueue training job to Azure Storage Queue (low priority)
                training_queue_client = QueueClient(
                    account_url=queue_service_url,
                    queue_name='model-training',
                    credential=credential
                )
                training_message = TrainingJobMessage(
                    job_id=message.job_id,
                    username=message.username,
                    repos_bundle=merged_results,
                    training_params={'batch_size': 8, 'epochs': 2}
                )
                training_queue_client.send_message(training_message.model_dump_json())
                
                # Update job status in cache: completed
                job_status_key = f'job:{message.job_id}'
                cache_manager.save(job_status_key, {
                    'status': 'completed',
                    'message': 'Bundle ready'
                }, ttl=3600)
                
                # Delete message from queue after successful processing
                queue_client.delete_message(msg)
                
                logger.info(f"Merge job {message.job_id} completed")
                
            except Exception as e:
                logger.error(f"Merge job failed: {e}", exc_info=True)
                # Message will be retried automatically

if __name__ == '__main__':
    main()
```

**5.3 Training Worker (from existing plan)**
- Use code from `plan-semanticModelTrainingRefactor.prompt.md`
- Already uses Azure Storage Queue (model-training queue)
- Same containerized approach (CPU + GPU variants)
- Polls Azure Storage Queue, not Redis

**5.4 Deploy Workers to Function Apps**
```bicep
// Deploy 3 workers with queue trigger scaling
resource syncWorker 'Microsoft.Web/sites@2023-01-01' = {
  name: 'func-sync-worker'
  kind: 'functionapp,linux'
  properties: {
    serverFarmId: functionAppPlan.id
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsStorage'
          value: storageConnectionString
        }
      ]
      functionAppScaleLimit: 10  // Max concurrent instances
    }
  }
}
```

**Scaling Configuration (host.json)**:
```json
{
  "version": "2.0",
  "extensions": {
    "queues": {
      "batchSize": 16,
      "maxDequeueCount": 3,
      "newBatchThreshold": 8,
      "visibilityTimeout": "00:05:00"
    }
  },
  "concurrency": {
    "dynamicConcurrencyEnabled": true,
    "maximumFunctionConcurrency": 100
  }
}
```

**Deliverables:**
- ✅ Sync worker deployed (queue trigger on `github-sync`, scales 0-10 based on queue depth)
- ✅ Merge worker deployed (queue trigger on `merge-results`, scales 0-5 based on queue depth)
- ✅ Training worker deployed to ACI (uses `model-training` queue, scales 0-2)
- ✅ All workers use shared package (consistent cache logic)
- ✅ Poison message handling (failed messages move to `{queue}-poison` after 3 retries)
- ✅ Application Insights logging configured for all Function Apps

**Time Estimate**: 12 hours (simpler than Container Apps, no custom scaling metrics)

**Note**: Training worker is the ONLY containerized component (deployed to ACI), as it requires >2GB memory and GPU support. All other workers use Function Apps with queue triggers for automatic scaling.

---

### Phase 6: Frontend Integration (Week 7) 🖥️ ANGULAR

**Objective**: Update Angular app to use queue-based API with polling

#### Tasks

**6.1 Update RepoBundleService**
```typescript
// src/app/services/repo-bundle.service.ts
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, throwError, of, timer } from 'rxjs';
import { switchMap, map, catchError, filter, take, retry, tap } from 'rxjs/operators';
import { ConfigService } from './config.service';

interface JobStatus {
  status: 'queued' | 'processing' | 'syncing' | 'completed' | 'failed';
  message?: string;
  progress?: number;
  data?: any;
}

@Injectable({ providedIn: 'root' })
export class RepoBundleService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  
  private apiUrl = this.config.apiUrl;

  /**
   * Get user bundle (tries cache first, triggers refresh if needed)
   */
  getUserBundle(username: string, forceRefresh = false): Observable<any> {
    if (!forceRefresh) {
      // Try cache first
      return this.http.get(`${this.apiUrl}/bundles/${username}`).pipe(
        catchError(err => {
          if (err.status === 404) {
            // Cache miss, trigger refresh
            return this.startRefreshAndPoll(username);
          }
          return throwError(() => err);
        })
      );
    }
    
    return this.startRefreshAndPoll(username);
  }

  /**
   * Start refresh job and poll until complete
   */
  private startRefreshAndPoll(username: string): Observable<any> {
    return this.http.post<{ job_id: string }>(`${this.apiUrl}/bundles/${username}/refresh`, {}).pipe(
      switchMap(({ job_id }) => this.pollJobStatus(job_id)),
      map(status => status.data)
    );
  }

  /**
   * Poll job status until completed or failed
   */
  private pollJobStatus(jobId: string): Observable<JobStatus> {
    return timer(0, 2000).pipe(  // Poll every 2 seconds
      switchMap(() => this.http.get<JobStatus>(`${this.apiUrl}/status/${jobId}`)),
      filter(status => status.status === 'completed' || status.status === 'failed'),
      take(1),  // Stop after first completed/failed
      retry({ count: 3, delay: 1000 })  // Retry on network errors
    );
  }

  /**
   * Get single repository bundle
   */
  getUserSingleRepoBundle(username: string, repo: string): Observable<any> {
    return this.http.get(`${this.apiUrl}/bundles/${username}/${repo}`);
  }
}
```

**6.2 Update Components with Route Parameters**
```typescript
// src/app/projects/projects.component.ts
import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { RepoBundleService } from '../services/repo-bundle.service';
import { tap, catchError } from 'rxjs/operators';
import { of } from 'rxjs';

@Component({
  selector: 'app-projects',
  templateUrl: './projects.component.html'
})
export class ProjectsComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private repoService = inject(RepoBundleService);
  
  username = 'yungryce';  // Default fallback
  loading = false;
  loadingMessage = '';
  repoBundle$ = of(null);

  ngOnInit() {
    // Get username from route parameter (Phase 1)
    this.username = this.route.snapshot.paramMap.get('username') || 'yungryce';
    
    this.loading = true;
    this.loadingMessage = 'Loading repositories...';
    
    this.repoBundle$ = this.repoService.getUserBundle(this.username).pipe(
      tap(() => {
        this.loading = false;
        this.loadingMessage = '';
      }),
      catchError(err => {
        this.loading = false;
        this.loadingMessage = 'Failed to load repositories';
        console.error('Error loading bundle:', err);
        return of(null);
      })
    );
  }
}
```

**6.3 Update Routes**
```typescript
// src/app/app.routes.ts
export const routes: Routes = [
  { path: '', component: HomeComponent },
  
  // Add username parameter (optional with default)
  { path: 'projects', component: ProjectsComponent },  // Uses default 'yungryce'
  { path: 'projects/:username', component: ProjectsComponent },
  
  { path: 'projects/:username/:repo', component: ProjectComponent },
  
  { path: 'assistant', component: AssistantComponent },
  { path: 'assistant/:username', component: AssistantComponent },
];
```

**6.4 Update Static Web App Proxy (Cutover)**
```json
// staticwebapp.config.json
{
  "routes": [
    {
      "route": "/api/*",
      "rewrite": "/api/*",
      "allowedRoles": ["anonymous"]
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html"
  },
  "mimeTypes": {
    ".json": "application/json"
  },
  "globalHeaders": {
    "Cache-Control": "no-cache, no-store, must-revalidate"
  },
  "responseOverrides": {
    "404": {
      "rewrite": "/index.html"
    }
  }
}
```

**Note**: During cutover, update Azure Static Web App backend link from old Durable Functions app to new Function App Gateway URL

**Deliverables:**
- ✅ RepoBundleService uses polling pattern
- ✅ Components support `/:username` route parameter
- ✅ Loading states show poll progress
- ✅ Static Web App proxy configured for Function App Gateway
- ✅ Backward compatible (works with both sync and async APIs during migration)

**Time Estimate**: 8 hours

---

### Phase 7: Cutover & Decommission (Week 8) 🚀 GO-LIVE

**Objective**: Switch traffic to queue-based architecture, decommission Durable Functions

#### Tasks

**7.1 Parallel Deployment (Week 1)**
- Deploy new Function App (api-gateway) with HTTP triggers
- Keep old Durable Functions app running (fallback)
- Route 10% of traffic to new Function App Gateway via Static Web App configuration

**7.2 Gradual Rollout (Week 2-3)**
```
Week 2: 10% → 25% → 50% traffic to Gateway
Week 3: 50% → 75% → 100% traffic to Gateway
```

Monitor metrics:
- Latency (p50, p95, p99)
- Error rate (<0.1% target)
- Queue depth (should stay <100)
- Worker scaling (should scale to 0 when idle)

**7.3 Validation Checklist**
- ✅ API response time <5s (96% improvement from 120s)
- ✅ Workers scale to 0 when queue empty (cost savings)
- ✅ Cache hit rate >80% (consistent with current)
- ✅ No failed jobs in dead letter queue
- ✅ Frontend polling works correctly
- ✅ Username parameter works for multiple users

**7.4 Decommission Old Durable Functions App**
```bash
# Stop old Durable Functions app (keep for 7 days as rollback window)
az functionapp stop --name func-portfolio-durable --resource-group rg-portfolio-prod

# After 7 days with no issues, delete
az functionapp delete --name func-portfolio-durable --resource-group rg-portfolio-prod
```

**7.5 Cleanup Old Code**
- Delete `api/function_app.py` (migrate content to workers)
- Delete `api/config/` (moved to `apps/shared/`)
- Delete `api/ai/` (moved to `apps/shared/`)
- Update documentation to reflect new architecture

**Deliverables:**
- ✅ 100% traffic on new Function App Gateway (HTTP triggers)
- ✅ Old Durable Functions app decommissioned
- ✅ Old code removed from repository
- ✅ Documentation updated
- ✅ Rollback playbook tested (can revert in <15 min)

**Time Estimate**: 12 hours (spread over 3 weeks)

---

## Integration Points Summary

### Queue Message Flow

```
API Gateway
    ↓ enqueue(SyncJobMessage)
┌───────────────────┐
│  github-sync      │ ← Sync Worker polls
│  (Storage Queue)  │
└───────────────────┘
    ↓ enqueue(MergeJobMessage)
┌───────────────────┐
│  merge-results    │ ← Merge Worker polls
│  (Storage Queue)  │
└───────────────────┘
    ↓ enqueue(TrainingJobMessage)
┌───────────────────┐
│  model-training   │ ← Training Worker polls
│  (Storage Queue)  │
└───────────────────┘
```

### Cache Contract (Unchanged)

All apps use shared cache_manager with consistent keys:
```
User Bundle:  repos_bundle_context_{username}
Repo Bundle:  repo_level_bundle_{username}_{repo}
Model:        fine_tuned_model_metadata
              model_{fingerprint}
Job Status:   job:{job_id} (Blob Storage, 1 hour TTL)
```

**Note**: Job status stored in Blob Storage via cache_manager (existing pattern), not a separate cache service. All cache operations use Azure Blob Storage.

### Environment Variables (Per App)

**API Gateway:**
```
AZURE_STORAGE_QUEUE_URL=https://stgportfolio.queue.core.windows.net
AZURE_STORAGE_BLOB_URL=https://stgportfolio.blob.core.windows.net
GROQ_API_KEY=<from-keyvault>
AZURE_CLIENT_ID=<managed-identity>
```

**Sync Worker:**
```
AZURE_STORAGE_QUEUE_URL=https://stgportfolio.queue.core.windows.net
AZURE_STORAGE_BLOB_URL=https://stgportfolio.blob.core.windows.net
GITHUB_TOKEN=<from-keyvault>
AZURE_CLIENT_ID=<managed-identity>
```

**Merge Worker:**
```
AZURE_STORAGE_QUEUE_URL=https://stgportfolio.queue.core.windows.net
AZURE_STORAGE_BLOB_URL=https://stgportfolio.blob.core.windows.net
AZURE_CLIENT_ID=<managed-identity>
```

**Training Worker:**
```
AZURE_STORAGE_QUEUE_URL=https://stgportfolio.queue.core.windows.net
AZURE_STORAGE_BLOB_URL=https://stgportfolio.blob.core.windows.net
AZURE_CLIENT_ID=<managed-identity>
```

---

## Cost Analysis

### Current (Monolithic Function App)

| Resource | Configuration | Monthly Cost |
|----------|--------------|--------------|
| Function App | 2GB RAM, 100 max instances | $45 |
| Azure Storage (Blob) | 10GB data, 1M operations | $25 |
| Application Insights | 5GB ingestion | $15 |
| **Total** | | **$85/month** |

### Target (Multi-App Function Apps + ACI)

| Resource | Configuration | Monthly Cost |
|----------|--------------|--------------|
| Function App (API Gateway) | Flex Consumption, 1GB RAM | $12 (estimated avg) |
| Function App (Sync Worker) | Flex Consumption, 2GB RAM | $15 (burst traffic) |
| Function App (Merge Worker) | Flex Consumption, 1GB RAM | $8 (burst traffic) |
| Training Worker (ACI) | 4 vCPU, 16GB, on-demand | $0.40/run × 10 runs = $4 |
| Azure Storage (Queues) | 1M queue operations | $0.50 |
| Azure Storage (Blob) | 10GB data, 1M operations | $25 |
| Application Insights | 5GB ingestion | $15 |
| **Total** | | **$79.50/month** |

**Cost Reduction**: -$5.50/month (-6%)  
**Justification**:
- 96% latency reduction (120s → 5s)
- Independent scaling (Function Apps scale to 0 when idle)
- Fault isolation (training failures don't impact API)
- No Container Apps Environment overhead
- No Azure Container Registry needed (only training worker uses containers)
- Function Apps Flex Consumption cheaper than Container Apps for low/medium traffic
- Training worker on ACI (on-demand) eliminates idle costs
- Azure Storage Queues provide reliable messaging at minimal cost

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| **Queue depth overflow** | Medium | High | Implement queue depth alerts (>100 messages), auto-scale workers to max replicas |
| **Cache key inconsistency** | Low | Critical | Use shared package for cache logic, comprehensive tests for key generation |
| **Worker crash loops** | Medium | Medium | Implement exponential backoff retry, dead letter queue for poison messages |
| **Azure Storage throttling** | Low | Medium | Implement exponential backoff, monitor 503 responses |
| **Container image bloat** | High | Low | Use multi-stage Docker builds, separate CPU/GPU training images |
| **Username injection attacks** | Medium | High | Validate username regex `^[a-zA-Z0-9-]+$`, parameterized queries, escape special chars |
| **CORS misconfiguration** | Low | Medium | Test cross-origin requests during deployment, monitor CORS errors |
| **Rollback complexity** | Medium | High | Maintain Function App for 7 days, feature flag for instant rollback |

**Overall Risk**: **MEDIUM** (mitigations in place for all critical risks)

---

## Success Metrics

### Performance

| Metric | Baseline (Current) | Target | Measurement |
|--------|-------------------|--------|-------------|
| **API Response Time** | 120s (blocking) | <5s | Application Insights, p95 |
| **Bundle Refresh Time** | 120s | 60s background | Azure Storage Queue monitoring |
| **Cache Hit Rate** | 85% | >80% | Cache manager logs |
| **Worker Scale-to-Zero** | N/A | <60s when idle | Function App metrics |

### Cost

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| **Monthly Infrastructure Cost** | $85 | $79.50 (6% reduction) | Azure Cost Management |
| **Cost per User Request** | $0.008 | $0.005 | Cost / Request Count |

### Reliability

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| **API Error Rate** | <0.5% | <0.1% | Application Insights |
| **Worker Retry Rate** | N/A | <5% | Queue metrics |
| **Poison Message Queue Depth** | N/A | 0 messages | Azure Storage Queue monitoring |

### Multi-User Support

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| **Hardcoded Usernames** | 6 locations | 0 | Code search |
| **Cache Isolation** | Single user | Per-username keys | Cache key audit |
| **Concurrent Users** | 1 (yungryce) | Unlimited | Load test |

---

## Rollback Plan

### Trigger Conditions
- API error rate >1% for 5 consecutive minutes
- Queue depth >500 messages for 10 minutes
- Worker crash rate >10% per hour
- Cache inconsistency detected (fingerprint mismatch >5%)

### Rollback Steps (Target: <15 minutes)

**1. Switch Traffic Back to Function App**
```bash
# Update Static Web App backend link
az staticwebapp backends update \
  --name portfolio-frontend \
  --resource-group rg-portfolio-prod \
  --backend-resource-id /subscriptions/.../providers/Microsoft.Web/sites/func-portfolio-prod
```

**2. Drain Queue Gracefully**
```bash
# Pause API Gateway (stop accepting new jobs)
az functionapp stop --name func-api-gateway --resource-group rg-portfolio-prod

# Let workers drain existing queue (monitor depth → 0)
# Estimated time: 5-10 minutes
```

**3. Validate Old Function App Health**
```bash
# Check endpoint health
curl https://func-portfolio-durable.azurewebsites.net/api/health

# Test orchestrator
curl -X POST https://func-portfolio-durable.azurewebsites.net/api/orchestrator_start \
  -H "Content-Type: application/json" \
  -d '{"username": "yungryce"}'
```

**4. Post-Rollback Analysis**
- Export Function App logs to Blob Storage
- Analyze queue depth patterns (identify bottleneck)
- Review poison message queues
- Conduct blameless postmortem

---

## Timeline Summary

| Phase | Duration | Key Deliverable | Blocking Dependencies |
|-------|----------|-----------------|----------------------|
| **Phase 1: Username Flexibility** | 1 week | No hardcoded usernames | None |
| **Phase 2: Extract Shared Code** | 1 week | `apps/shared/` package | Phase 1 complete |
| **Phase 3: Deploy Infrastructure** | 1 week | Azure Storage Queues deployed | None (parallel with Phase 2) |
| **Phase 4: Build API Gateway** | 1 week | Function App with HTTP triggers deployed | Phase 2, 3 complete |
| **Phase 5: Build Workers** | 1.5 weeks | 3 workers deployed | Phase 2, 3 complete |
| **Phase 6: Frontend Integration** | 1 week | Polling UI implemented | Phase 4 complete |
| **Phase 7: Cutover** | 3 weeks | 100% traffic on new Function App | Phase 5, 6 complete |
| **Total** | **7.5-8 weeks** | Multi-app architecture live | |

**Critical Path**: Phase 1 → Phase 2 → Phase 4 → Phase 6 → Phase 7

---

## Next Steps

1. **Review this plan** with stakeholders (confirm priorities, timeline, cost)
2. **Spike Phase 1** (username flexibility) - validate assumptions with quick prototype
3. **Set up project tracking** (GitHub Project board with 8 epics, 40+ issues)
4. **Create Phase 1 branch** (`feature/username-flexibility`)
5. **Begin implementation** (start with backend username validation)

**Estimated Start Date**: Week of November 18, 2025  
**Target Completion**: Week of January 13, 2026
