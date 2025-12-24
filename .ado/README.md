# .ado/README.md - Pipeline Templates and Parameters Guide

## Overview

The `.ado` folder contains reusable Azure Pipelines templates for Cloudfolio's CI/CD workflows. These templates are orchestrated by the root `azure-pipelines.yml` file.

## File Structure

```
.ado/
├── parameters.yml                    # Global parameter schema (reference for all templates)
├── infra-core.yml                    # Pipeline: Deploy core infrastructure (standalone)
├── deploy-core-infra.yml             # Template: Reusable core infrastructure deployment
│
├── ci-api-gateway.yml                # Pipeline: Build api-gateway Function App
├── ci-sync-worker.yml                # Pipeline: Build sync-worker Function App
├── ci-merge-worker.yml               # Pipeline: Build merge-worker Function App
├── cd-function.yml                   # Template: Deploy any Function App (ensures core infra first)
│
├── ci-swa.yml                        # Pipeline: Build Angular UI
├── cd-swa.yml                        # Template: Deploy SWA (ensures core infra first)
│
├── ci-container.yml                  # Pipeline: Build & push Docker image
├── cd-container.yml                  # Template: Deploy container (ensures core infra first)
│
├── params/                           # Subdirectory: Component-specific unified parameters
│   ├── params-core.yml               # Core infrastructure parameters
│   ├── params-api-gateway.yml        # Function app: api-gateway
│   ├── params-sync-worker.yml        # Function app: sync-worker
│   ├── params-merge-worker.yml       # Function app: merge-worker
│   ├── params-swa.yml                # Static Web App
│   └── params-container.yml          # Container
│
└── README.md                         # This file
```

**Key Innovation (Phase 13):**
- Created reusable `deploy-core-infra.yml` template for core infrastructure deployment
- Used by `infra-core.yml` for standalone deployments
- Used by ALL CD pipelines as first stage (ensures autonomy)
- Makes CD pipelines fully autonomous: can run independently and bootstrap infra if needed

## Parameters Organization

Parameters are organized into **4 layers** for clarity and maintainability:

### Layer 1: Global Parameter Schema (`parameters.yml`)
The definitive reference for all possible parameters across the pipeline. Defines:
- Parameter names, types, display names
- Default values for common settings
- Documentation for template developers

This is a **SCHEMA REFERENCE**, not a configuration file. Each template declares which parameters it accepts.

### Layer 2: Component-Specific Parameters (`params/params-*.yml`)
Each component defines the parameters it actually uses:
- `params-core.yml` - Core infrastructure (infra-core.yml)
- `params-api-gateway.yml` - api-gateway function app (ci/cd-function.yml)
- `params-sync-worker.yml` - sync-worker function app (ci/cd-function.yml)
- `params-merge-worker.yml` - merge-worker function app (ci/cd-function.yml)
- `params-swa.yml` - Static Web App (ci/cd-swa.yml)
- `params-container.yml` - Container (ci/cd-container.yml)

Each file combines CI and CD parameters for its component (single source of truth).

### Layer 3: Generic Templates (`ci-*.yml`, `cd-*.yml`, `infra-core.yml`)
Reusable template logic that declares parameter requirements. Templates are component-agnostic:
- CI templates: Build, package, publish artifact
- CD templates: Deploy infra via Bicep, download artifact, publish code
- Infrastructure templates: Deploy specific Azure resources

### Layer 4: Root Orchestrator (`azure-pipelines.yml`)
Provides actual values for all parameters via Azure DevOps variables and pipeline inputs.
References specific component parameters when invoking templates.

---

## Global Parameters Reference

All parameters used across the templates are documented in **`.ado/parameters.yml`**. Categories include:

- **Azure subscription/RG credentials** (service connections, subscription ID, resource group)
- **Naming and tagging** (resource name prefix, location, tags)
- **Core infrastructure outputs** (subnet IDs, UAMI, storage account, Log Analytics)
- **Component-specific settings** (container config, Docker Hub, training parameters)
- **Bicep file paths**

### Categories

#### Azure Subscription & Resource Group
- `azureServiceConnection` - Azure DevOps service connection name
- `subscriptionId` - Azure subscription ID
- `resourceGroupName` - Target resource group

#### Deployment Location & Naming
- `location` - Azure region (default: `westus2`)
- `namePrefix` - Prefix for all resources (default: `cloudfolio`)
- `tags` - JSON tags applied to all resources (default: `{}`)

#### Core Infrastructure (infra-core.yml)
*See `params/params-core.yml` for core infrastructure parameters:*
- `bicepFile` - Bicep template for core infra (default: `infra/bicep/main.bicep`)
- `bicepParamFile` - Bicep parameters file (default: `infra/bicep/main.bicepparam`)

#### Core Infrastructure Outputs
*Populated by the `infra-core` stage, used by other stages:*
- `vnetId` - Virtual Network ID
- `functionsSubnetId`, `privateEndpointsSubnetId` - VNet subnets
- `uamiId`, `uamiClientId`, `uamiPrincipalId` - Managed identity
- `storageAccountName`, `storageAccountId` - Storage account
- `appInsightsConnectionString` - Application Insights connection string
- `logAnalyticsWorkspaceId`, `logAnalyticsWorkspaceKey` - Log Analytics workspace
- `privateDnsZoneAzureWebsitesId` - Private DNS zone for azurewebsites.net

#### Function Apps
- `appServicePlanId` - Shared App Service Plan ID (deployed by core infra)
- `deployPrivateEndpoints` - Enable private endpoints (default: `false`)
- `pythonVersion` - Python version for builds (default: `3.13`)
- `bicepFile` - Bicep template for function apps (default: `infra/bicep/main.function.bicep`)

#### Static Web App
- `enableLinkedBackend` - Link SWA to Function backend (default: `false`)
- `apiGatewayId`, `apiGatewayDefaultHostname` - api-gateway function app outputs

#### Container / Docker
- `dockerHubServiceConnection` - Docker Hub service connection name
- `dockerHubRepository` - Docker Hub repo path
- `imageTag` - Container image tag (default: `$(Build.BuildId)`)

#### Container Instance Configuration
- `trainingMode` - `serverless` or `continuous`
- `queueName` - Azure Storage Queue name for training jobs
- `blobContainerName` - Blob container for model artifacts
- `restartPolicy` - `Always`, `OnFailure`, or `Never`
- `cpuCores` - CPU cores (default: `2.0`)
- `memoryGb` - Memory in GB (default: `4.0`)

---

## Stage Templates

### 1. `infra-core.yml` - Core Infrastructure (Run Once)

**Deployment order:** 1st (foundation, run once per environment)

**Parameters:** See `params/params-core.yml` for unified core infrastructure parameters

**What it deploys:**
- Resource Group (created automatically via `action: 'Create Or Update Resource Group'`)
- Virtual Network + subnets (functions, private endpoints)
- User-Assigned Managed Identity (for function apps and container)
- Storage Account (blob, queue, table) + Private Endpoints + Lifecycle policies
- Log Analytics Workspace + Application Insights
- Private DNS zones (azurewebsites.net, privatelink)

**Outputs:** Core infrastructure IDs (provisioned for other stages)
- `vnetId`, `functionsSubnetId`, `privateEndpointsSubnetId`
- `uamiId`, `uamiClientId`, `uamiPrincipalId`
- `storageAccountName`, `storageAccountId`
- `appInsightsConnectionString`
- `logAnalyticsWorkspaceId`, `logAnalyticsWorkspaceKey`
- `privateDnsZoneAzureWebsitesId`

**Job Execution Order:**
1. `Deploy_Core` - Deploys core infrastructure via Bicep (creates RG if needed)

**How to use:**
```yaml
- template: .ado/infra-core.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    subscriptionId: $(subscriptionId)
    resourceGroupName: $(resourceGroupName)
    location: $(location)
    namePrefix: $(namePrefix)
    tags: $(tags)
```

---

### 2. Function App CI/CD Templates (Split Per-Function)

#### `ci-api-gateway.yml`, `ci-sync-worker.yml`, `ci-merge-worker.yml` - Function CI (Per-Function)

**Purpose:** Each function has its own CI pipeline with precise path-based triggers.

**Triggers:**
- Branch: `main`
- Paths: Function-specific directory + `apps/shared/` (rebuilds all functions when shared code changes)

**What each does:**
- Installs function requirements + shared library
- Creates function app zip package
- Publishes artifact via `PublishPipelineArtifact@1` (pipeline artifacts, not storage)

**Example: ci-api-gateway.yml**
- Triggers on: `apps/api-gateway/**` + `apps/shared/**` changes on `main` branch
- Publishes artifact: `api-gateway-$(Build.BuildId)`

**Key benefit:** Each function's CI only runs when that function (or shared code) changes. No unnecessary builds.

---

#### `cd-function.yml` - Function CD (Generic Template)

**Deployment order:** After each function's CI completes

**How it works:**
- Uses `resources.pipelines` to reference the corresponding CI pipeline (e.g., `ci-function`)
- Downloads CI artifact automatically
- Deploys Function App infrastructure via Bicep directly (no build step)
- Resolves deployed function app name
- Publishes artifact code to Function App

**Parameters:**
- `functionName` - Name of function (api-gateway, sync-worker, merge-worker)
- `bicepFile` - Path to Bicep template for function infra
- `appServicePlanId`, `functionsSubnetId`, `uamiId`, etc. - Core infra outputs
- `pythonVersion` - Python version for runtime

**Unified Parameter Files:**
- `params-api-gateway.yml` - Configuration for api-gateway (used by both CI and CD)
- `params-sync-worker.yml` - Configuration for sync-worker
- `params-merge-worker.yml` - Configuration for merge-worker

**Example usage in root pipeline:**
```yaml
- template: .ado/cd-function.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    subscriptionId: $(subscriptionId)
    resourceGroupName: $(resourceGroupName)
    location: $(location)
    namePrefix: $(namePrefix)
    bicepFile: infra/bicep/main.function.bicep
    appServicePlanId: $(appServicePlanId)
    functionsSubnetId: $(functionsSubnetId)
    privateEndpointsSubnetId: $(privateEndpointsSubnetId)
    uamiId: $(uamiId)
    uamiClientId: $(uamiClientId)
    uamiPrincipalId: $(uamiPrincipalId)
    appInsightsConnectionString: $(appInsightsConnectionString)
    privateDnsZoneAzureWebsitesId: $(privateDnsZoneAzureWebsitesId)
    deployPrivateEndpoints: false
    functionName: api-gateway
    pythonVersion: '3.13'
```

**Key improvements:**
- Per-function CI pipelines eliminate unnecessary builds
- CD remains generic and reusable
- Direct Bicep deployment (no JSON compilation step)
- Pipeline artifacts handle CI/CD artifact passing

---

### 3. SWA CI/CD Templates

#### `ci-swa.yml` - Build Static Web App (CI)

**Triggers:**
- Branch: `main`
- Paths: `ui/**` changes

**What it does:**
- Builds Angular UI using Node.js
- Creates dist directory: `ui/dist/cloudfolio-ui`
- Publishes artifact via `PublishPipelineArtifact@1` (no zipping, direct files)

**Parameters:**
- `uiDir` - Path to Angular UI source (default: `ui`)

**Example usage:**
```yaml
- template: .ado/ci-swa.yml
  parameters:
    uiDir: 'ui'
```

---

#### `cd-swa.yml` - Deploy Static Web App (CD)

**Triggers:** Automatically when ci-swa completes (via `resources.pipelines`)

**What it does:**
- Downloads SWA artifact from CI pipeline directly (no Storage account needed)
- Deploys SWA infrastructure via Bicep
- Retrieves SWA deployment details and API token
- Publishes UI content to SWA via `AzureStaticWebApp@0`

**Two-job structure:**
- `Deploy_Infra` - Deploys SWA infrastructure, reads outputs, downloads artifact
- `Deploy_Content` - Publishes UI content to SWA (depends on Deploy_Infra)

**Parameters:**
- `bicepFile` - Bicep template path (default: `infra/bicep/main.staticwebapp.bicep`)
- `apiGatewayId`, `apiGatewayDefaultHostname` - Backend function app config (optional)
- `enableLinkedBackend` - Link SWA to function backend (default: `false`)

**Example usage:**
```yaml
- template: .ado/cd-swa.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    subscriptionId: $(subscriptionId)
    resourceGroupName: $(resourceGroupName)
    location: $(location)
    namePrefix: $(namePrefix)
    apiGatewayId: $(apiGatewayId)
    apiGatewayDefaultHostname: $(apiGatewayDefaultHostname)
    enableLinkedBackend: false
```

**Key improvements:**
- Uses pipeline artifacts instead of Azure Storage
- No zip/unzip steps (direct file deployment)
- Simpler artifact management
- Faster deployment

---

### 4. Container CI/CD Templates

#### `ci-container.yml` - Build Container Image (CI)

**Triggers:**
- Branch: `main`
- Paths: `apps/training-worker/**` changes

**What it does:**
- Builds training-worker Docker image from Dockerfile
- Pushes image to Docker Hub with tag: `$(Build.BuildId)` and `latest`

**Parameters:**
- `dockerHubServiceConnection` - Docker Hub service connection
- `dockerHubRepository` - Docker Hub repo path
- `imageTag` - Image tag (default: `$(Build.BuildId)`)
- `containerDir` - Path to training-worker code

**Example usage:**
```yaml
- template: .ado/ci-container.yml
  parameters:
    dockerHubServiceConnection: $(dockerHubServiceConnection)
    dockerHubRepository: $(dockerHubRepository)
    imageTag: $(Build.BuildId)
```

---

#### `cd-container.yml` - Deploy Container Instance (CD)

**Deployment order:** After container CI completes

**What it does:**
- Deploys Azure Container Instance infrastructure via Bicep
- Configures container with:
  - Managed identity (UAMI) for Azure access
  - Storage account mount for model artifacts
  - Log Analytics integration for monitoring
  - Training job queue and blob container names
  - Container resource limits (CPU, memory, restart policy)

**Parameters:**
- `bicepFile` - Bicep template path (default: `infra/bicep/main.container.bicep`)
- `dockerHubRepository`, `imageTag` - Container image location
- `uamiPrincipalId`, `uamiId`, `uamiClientId` - Managed identity
- `storageAccountName`, `logAnalyticsWorkspaceId`, `logAnalyticsWorkspaceKey` - Storage and logging
- `trainingMode`, `queueName`, `blobContainerName` - Training job config
- `restartPolicy`, `cpuCores`, `memoryGb` - Container behavior

**Example usage:**
```yaml
- template: .ado/cd-container.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    subscriptionId: $(subscriptionId)
    resourceGroupName: $(resourceGroupName)
    location: $(location)
    namePrefix: $(namePrefix)
    uamiPrincipalId: $(uamiPrincipalId)
    uamiId: $(uamiId)
    uamiClientId: $(uamiClientId)
    storageAccountName: $(storageAccountName)
    logAnalyticsWorkspaceId: $(logAnalyticsWorkspaceId)
    logAnalyticsWorkspaceKey: $(logAnalyticsWorkspaceKey)
    dockerHubRepository: $(dockerHubRepository)
    imageTag: $(Build.BuildId)
    trainingMode: serverless
```

---

## Key Design Principles

### 1. Autonomous CD Pipelines (Phase 13 - Critical)
- **Before:** CD pipelines validated RG existed (failed if infra missing)
- **After:** CD pipelines deploy core infra themselves as first stage
- **Benefit:** CD pipelines are fully autonomous - can run independently without manual setup
- **Implementation:** All CD pipelines use `deploy-core-infra.yml` template as `Ensure_Core_Infrastructure` stage before app deployment

### 2. Reusable Core Infrastructure Deployment
- **Template:** `deploy-core-infra.yml` - Single source of truth for core infra deployment
- **Used by:** `infra-core.yml` (standalone deployment) + ALL CD pipelines (bootstrap infra if needed)
- **Benefit:** Consistent deployment logic, no duplication, ensures idempotency

### 3. CI Pipeline Separation (Phase 12)
- **Before:** Single `ci-function.yml` for all 3 functions (couldn't detect which function changed)
- **After:** Three separate CI pipelines (`ci-api-gateway.yml`, `ci-sync-worker.yml`, `ci-merge-worker.yml`)
- **Benefit:** Each function builds only when its code (or shared code) changes → faster pipelines

### 4. Direct Bicep Deployment (Phase 12)
- **Before:** CD templates compiled Bicep → JSON, then deployed
- **After:** CD templates deploy Bicep directly via `templateFile` property
- **Benefit:** Eliminates redundant build step, faster deployments, cleaner artifacts

### 5. Artifact Handling
- **Function Apps & Container:** Pipeline artifacts (PublishPipelineArtifact@1 → DownloadPipelineArtifact@2)
- **SWA:** Pipeline artifacts (no longer uses Azure Storage)
- **Benefit:** Simpler, built-in Azure DevOps artifact handling, no storage account dependencies

### 6. Parameter Organization (4 Layers)
1. **Global Schema** (`parameters.yml`) - Authoritative reference
2. **Component Parameters** (`params/params-*.yml`) - Per-component configuration
3. **Templates** (`ci-*.yml`, `cd-*.yml`, `deploy-core-infra.yml`) - Reusable logic
4. **Pipelines** (`infra-core.yml`, triggered CI/CD pipelines) - Runtime execution

---

### 7. CI Pipeline Separation (Phase 12)
- **Before:** Single `ci-function.yml` for all 3 functions (couldn't detect which function changed)
- **After:** Three separate CI pipelines (`ci-api-gateway.yml`, `ci-sync-worker.yml`, `ci-merge-worker.yml`)
- **Benefit:** Each function builds only when its code (or shared code) changes → faster pipelines

### 8. Direct Bicep Deployment (Phase 12)
- **Before:** CD templates compiled Bicep → JSON, then deployed
- **After:** CD templates deploy Bicep directly via `templateFile` property
- **Benefit:** Eliminates redundant build step, faster deployments, cleaner artifacts

### 9. Resource Group Creation (Phase 12)
- **Before:** Custom jobs in each template to create RG
- **After:** Only `infra-core.yml` creates RG via `action: 'Create Or Update Resource Group'`; CD templates use `action: 'Select Resource Group'`
- **Benefit:** Single RG creation point, reduced redundancy, clearer intent

### 10. Artifact Handling
- **Function Apps & Container:** Pipeline artifacts (PublishPipelineArtifact@1 → DownloadPipelineArtifact@2)
- **SWA:** Pipeline artifacts (no longer uses Azure Storage)
- **Benefit:** Simpler, built-in Azure DevOps artifact handling, no storage account dependencies

### 11. Parameter Organization (4 Layers)
1. **Global Schema** (`parameters.yml`) - Authoritative reference
2. **Component Parameters** (`params/params-*.yml`) - Per-component configuration
3. **Generic Templates** (`ci-*.yml`, `cd-*.yml`) - Reusable logic
4. **Root Orchestrator** (`azure-pipelines.yml`) - Variable injection

---



## Setup Checklist

Before running pipelines, ensure:

1. **Azure Service Connection** exists in your Azure DevOps project
   - Name it and set `azureServiceConnection` variable

2. **Resource Group** exists in your Azure subscription
   - Set `resourceGroupName` variable

3. **Variable Group** (optional but recommended)
   - Create a variable group in ADO with all parameters from `parameters.yml`
   - Link it to the pipeline for easy management

4. **Docker Hub Credentials** (if deploying container)
   - Create Docker Hub service connection in ADO
   - Set `dockerHubServiceConnection` and `dockerHubRepository`

5. **Run core infra first**
   - Allow `infra-core` stage to complete and capture outputs
   - Copy outputs into variables for subsequent stages

---

## Variable Precedence

Variables are resolved in this order (highest to lowest):

1. **Runtime parameters** (set when triggering pipeline)
2. **Variable group values** (linked to pipeline)
3. **Root `azure-pipelines.yml` defaults** (`variables:` section)
4. **Template parameter defaults** (in `.ado/*.yml` files)

---

## Troubleshooting

- **Missing core infrastructure outputs:** Ensure `infra-core` stage completes successfully before running other stages.
- **Function App deployment fails:** Check subnet IDs, UAMI permissions, and App Insights connection string.
- **SWA content not deploying:** Verify Angular build output path matches `app_location` parameter.
- **Container image push fails:** Ensure Docker Hub service connection has valid credentials.
