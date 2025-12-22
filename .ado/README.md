# .ado/README.md - Pipeline Templates and Parameters Guide

## Overview

The `.ado` folder contains reusable Azure Pipelines templates for Cloudfolio's CI/CD workflows. These templates are orchestrated by the root `azure-pipelines.yml` file.

## File Structure

```
.ado/
├── parameters.yml                    # Global parameter schema (reference for all templates)
├── infra-core.yml                    # Stage: Deploy core infrastructure (one-time setup)
│
├── ci-function.yml                   # Template: Generic CI build + upload for function apps
├── cd-function.yml                   # Template: Generic CD deploy infra + code for function apps
│
├── ci-swa.yml                        # Template: Generic CI build Angular UI
├── cd-swa.yml                        # Template: Generic CD deploy SWA infra + content
│
├── ci-container.yml                  # Template: Generic CI build & push Docker image
├── cd-container.yml                  # Template: Generic CD deploy container infra
│
├── swa.yml                           # (DEPRECATED) Old monolithic SWA deployment
├── container.yml                     # (DEPRECATED) Old monolithic container deployment
│
├── params/                           # Subdirectory: Component-specific unified parameters
│   ├── params-core.yml               # Core infrastructure parameters (for infra-core.yml)
│   ├── params-api-gateway.yml        # Function app: api-gateway (CI + CD combined)
│   ├── params-sync-worker.yml        # Function app: sync-worker (CI + CD combined)
│   ├── params-merge-worker.yml       # Function app: merge-worker (CI + CD combined)
│   ├── params-swa.yml                # Static Web App (CI + CD combined)
│   └── params-container.yml          # Container (CI + CD combined)
│
└── README.md                         # This file
```

**Phase 10-11 Changes:** 
- SWA and container split into CI/CD templates (Phase 10)
- Unified parameter files organized in `params/` subdirectory (Phase 10)
- Core infrastructure parameters consolidated into `params-core.yml` (Phase 11)
- Global `parameters.yml` refactored as schema reference only (Phase 11)
- All components now follow consistent 4-layer parameter architecture

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
- CI templates: Build, package, upload artifact
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

````

### Categories

#### Azure Subscription & Resource Group
- `azureServiceConnection` - Azure DevOps service connection name
- `subscriptionId` - Azure subscription ID
- `resourceGroupName` - Target resource group

#### Deployment Location & Naming
- `location` - Azure region (default: `westus2`)
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
- `deployFunctionAppPrivateEndpoints` - Enable private endpoints (default: `false`)
- `artifactContainerName` - Storage container for function artifacts (default: `function-artifacts`)
- `pythonVersion` - Python version for builds (default: `3.13`)
- `bicepFunctionAppFile` - Bicep template for function apps (default: `infra/bicep/main.function.bicep`)

#### Static Web App
- `enableLinkedBackend` - Link SWA to Function backend (default: `true`)
- `apiGatewayId`, `apiGatewayDefaultHostname` - api-gateway function app outputs (populated by function deployment)

#### Container / Docker
- `dockerHubServiceConnection` - Docker Hub service connection name
- `dockerHubRepository` - Docker Hub repo path (e.g., `username/training-worker`)
- `imageTag` - Container image tag (default: `latest`, or use `$(Build.BuildId)`)

#### Container Instance Configuration
- `trainingMode` - `serverless` (exit after one batch) or `continuous` (poll forever)
- `queueName` - Azure Storage Queue name for training jobs
- `blobContainerName` - Blob container for model artifacts
- `containerRestartPolicy` - Restart behavior: `Always`, `OnFailure`, `Never`
- `containerCpuCores` - CPU cores (0.5–4.0, default: `2.0`)
- `containerMemoryGb` - Memory in GB (1–16, default: `4.0`)

## Stage Templates

### 1. `infra-core.yml` - Core Infrastructure (Run Once)

**Deployment order:** 1st (foundation, run once per environment)

**Parameters:** See `params/params-core.yml` for unified core infrastructure parameters

**What it deploys:**
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

### 2. CI & CD Templates (Generic, Unified Per-Function Parameters)

#### Generic CI Template: `ci-function.yml`

Single reusable CI template for building any Function App. Configure per-function via unified parameter files that combine CI and CD config.

**Parameters:**
- `functionName` - Name of the function (api-gateway, sync-worker, merge-worker)
- `functionDir` - Path to function app code (e.g., `api/v0.2.0/api-gateway`)
- `sharedDir` - Path to shared library (e.g., `api/v0.2.0/shared`)
- `pythonVersion` - Python version for build (inherited from `parameters.yml` default)
- `storageAccountName` - Where to upload artifact zip
- `artifactContainerName` - Storage container name (inherited from `parameters.yml`)
- `artifactBuildId` - Build ID for versioning
- Other Azure subscription/authentication params

**Unified Parameter Files (CI + CD in one file):**
- `params-api-gateway.yml` - Configuration for api-gateway (used by both CI and CD)
- `params-sync-worker.yml` - Configuration for sync-worker (used by both CI and CD)
- `params-merge-worker.yml` - Configuration for merge-worker (used by both CI and CD)

**What it does:**
- Installs function app requirements + shared library
- Creates function app zip package
- Uploads artifact to Storage: `${functionName}/${Build.BuildId}.zip`

**Example usage in root pipeline:**
```yaml
- template: .ado/ci-function.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    subscriptionId: $(subscriptionId)
    resourceGroupName: $(resourceGroupName)
    storageAccountName: $(storageAccountName)
    artifactContainerName: $(artifactContainerName)
    artifactBuildId: $(Build.BuildId)
    pythonVersion: '3.13'
    functionName: api-gateway
    functionDir: api/v0.2.0/api-gateway
    sharedDir: api/v0.2.0/shared
```

---

#### Generic CD Template: `cd-function.yml`

Single reusable CD template for deploying any Function App. Configure per-function via unified parameter files.

**Parameters:**
- `functionName` - Name of the function
- `appServicePlanId`, `functionsSubnetId`, `storageAccountName`, `uamiId`, etc. - Core infra outputs
- `artifactContainerName`, `artifactBuildId`, `pythonVersion` - Artifact storage and config
- Other Bicep + Azure deployment params

**Unified Parameter Files (CI + CD in one file):**
- `params-api-gateway.yml` - Configuration for api-gateway (used by both CI and CD)
- `params-sync-worker.yml` - Configuration for sync-worker (used by both CI and CD)
- `params-merge-worker.yml` - Configuration for merge-worker (used by both CI and CD)

**What it does:**
- Builds Bicep template to ARM JSON
- Deploys Function App infra via `main.function.bicep` (incremental)
- Resolves deployed function app name
- Downloads artifact from Storage: `${functionName}/${Build.BuildId}.zip`
- Publishes artifact code to Function App via `AzureFunctionApp@2`

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
    storageAccountName: $(storageAccountName)
    uamiId: $(uamiId)
    uamiClientId: $(uamiClientId)
    appInsightsConnectionString: $(appInsightsConnectionString)
    privateDnsZoneAzureWebsitesId: $(privateDnsZoneAzureWebsitesId)
    pythonVersion: '3.13'
    functionName: api-gateway
    functionAppName: cloudfolio-api-gateway
    artifactContainerName: function-artifacts
    artifactBuildId: $(Build.BuildId)
```

---

#### Separation of Concerns & Parameter Organization

**Parameters are organized into three layers:**

1. **Global Parameters** (`parameters.yml`) - High-level, shared by all CI/CD invocations
   - Shared CI/CD config: `artifactContainerName`, `pythonVersion`, `bicepFunctionAppFile`, `deployFunctionAppPrivateEndpoints`
   - Inherited defaults for Bicep paths, container settings, Docker config

2. **Unified Per-Function Parameters** (`params-api-gateway.yml`, `params-sync-worker.yml`, `params-merge-worker.yml`) - Combined CI + CD configuration
   - CI-specific: `functionName`, `functionDir`, `sharedDir`, `pythonVersion` (can override global)
   - CD-specific: `appServicePlanId`, `functionsSubnetId`, `storageAccountName`, `uamiId`, `bicepFile`, etc.
   - Both CI and CD templates reference the same parameter file (single source of truth per function)

3. **Generic Templates** (`ci-function.yml`, `cd-function.yml`) - Reusable CI/CD logic
   - CI logic: Build, package, upload to storage
   - CD logic: Deploy infra via Bicep, download artifact, publish code

**Benefits:**
- **Single source of truth per function** - `params-*.yml` combines CI + CD, no duplication
- **Centralized shared params** - `parameters.yml` reduces duplication of common settings
- **Reusable templates** - Update CI/CD logic once, applies to all functions

- Per-function customization without duplicating code
- Easy to add new functions (create new parameter file, reference generic template)
- Clear ownership: template owner vs. function owner

---

### 3. SWA CI/CD Templates

#### `ci-swa.yml` - Build Static Web App (CI)

**Deployment order:** 3 (requires core infra)

**What it does:**
- Builds Angular UI using Node.js
- Creates zip artifact: `swa.zip`
- Uploads artifact to Storage: `swa/${Build.BuildId}.zip`

**Parameters:**
- `uiDir` - Path to Angular UI source (default: `ui`)
- `storageAccountName`, `artifactContainerName`, `artifactBuildId` - Artifact storage config
- Azure subscription/RG credentials

**Example usage in root pipeline:**
```yaml
- template: .ado/ci-swa.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    subscriptionId: $(subscriptionId)
    resourceGroupName: $(resourceGroupName)
    storageAccountName: $(storageAccountName)
    artifactContainerName: 'swa-artifacts'
    artifactBuildId: $(Build.BuildId)
    uiDir: 'ui'
```

---

#### `cd-swa.yml` - Deploy Static Web App (CD)

**Deployment order:** 6 (requires SWA CI, core infra outputs)

**What it does:**
- Builds Bicep template for Static Web App
- Deploys SWA infra via ARM (incremental)
- Retrieves SWA deployment details
- Downloads UI artifact from Storage
- Publishes UI content to SWA via `AzureStaticWebApp@0`

**Parameters:**
- `bicepFile` - Bicep template path (default: `infra/bicep/main.staticwebapp.bicep`)
- `apiGatewayId`, `apiGatewayDefaultHostname` - Backend function app config (optional)
- `enableLinkedBackend` - Link SWA to function backend (default: `false`)
- `storageAccountName`, `artifactContainerName`, `artifactBuildId` - Artifact download config
- Azure subscription/RG credentials, location, namePrefix

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
    storageAccountName: $(storageAccountName)
    artifactContainerName: 'swa-artifacts'
    artifactBuildId: $(Build.BuildId)
```

---

### 4. Container CI/CD Templates

#### `ci-container.yml` - Build Container Image (CI)

**Deployment order:** 4 (independent)

**What it does:**
- Builds training-worker Docker image from Dockerfile
- Pushes image to Docker Hub with tag: `${imageTag}` and `latest`

**Parameters:**
- `containerDir`, `dockerFile` - Source paths for Docker build
- `imageTag` - Image tag (default: `$(Build.BuildId)`)
- `dockerHubServiceConnection`, `dockerHubRepository` - Docker Hub credentials and repo
- Azure subscription credentials (for logging)

**Example usage:**
```yaml
- template: .ado/ci-container.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    dockerHubServiceConnection: $(dockerHubServiceConnection)
    dockerHubRepository: $(dockerHubRepository)
    imageTag: $(Build.BuildId)
    containerDir: 'api/v0.2.0/training-worker'
    dockerFile: 'api/v0.2.0/training-worker/Dockerfile'
```

---

#### `cd-container.yml` - Deploy Container Instance (CD)

**Deployment order:** 7 (requires container CI, core infra outputs)

**What it does:**
- Builds Bicep template for Azure Container Instance
- Deploys container infra via ARM (incremental)
- Configures container with:
  - Managed identity (UAMI) for Azure access
  - Storage account mount for model artifacts
  - Log Analytics integration for monitoring
  - Training job queue and blob container names
  - Container resource limits (CPU, memory, restart policy)

**Parameters:**
- `bicepFile` - Bicep template path (default: `infra/bicep/main.container.bicep`)
- `dockerHubRepository`, `imageTag` - Container image location
- `uamiPrincipalId`, `uamiId`, `uamiClientId` - Managed identity for container
- `storageAccountName`, `logAnalyticsWorkspaceId`, `logAnalyticsWorkspaceKey` - Storage and logging
- `trainingMode`, `queueName`, `blobContainerName` - Training job config
- `restartPolicy`, `cpuCores`, `memoryGb` - Container behavior and resources
- Azure subscription/RG credentials, location, namePrefix

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

**Key Architecture (SWA and Container in Phase 10):**
- **Before Phase 10:** Monolithic `swa.yml` and `container.yml` combined build + deploy in one template
- **After Phase 10:** Split into `ci-swa.yml` + `cd-swa.yml` and `ci-container.yml` + `cd-container.yml`
- **Benefit:** Consistent with function app pattern, independent CI/CD stages, artifact storage decoupling

---

### 5. Parameter Organization

**All components now follow 3-layer parameter architecture:**

1. **Global Parameters** (`parameters.yml`)
   - Azure credentials, location, naming, core infra outputs
   - Shared CI/CD defaults: `artifactContainerName`, `pythonVersion`, `bicepFunctionAppFile`, `deployFunctionAppPrivateEndpoints`

2. **Unified Per-Component Parameters** (`params/params-*.yml`)
   - Function apps: `params-api-gateway.yml`, `params-sync-worker.yml`, `params-merge-worker.yml`
   - SWA: `params-swa.yml`
   - Container: `params-container.yml`
   - Each file combines CI + CD config (single source of truth per component)

3. **Generic Templates** (`ci-*.yml`, `cd-*.yml`)
   - Reusable CI logic (build, package, upload artifact)
   - Reusable CD logic (deploy infra, download artifact, publish)
   - No component-specific knowledge

**Parameter Subdirectory:**
- `.ado/params/` organizes all parameter files in one place
- Easy to see all components at a glance
- Cleaner root `.ado/` directory

---

### 4. `swa.yml` - Static Web App (Build + Deploy)

**Deployment order:** 2a (requires core infra)

**What it does:**
- Builds and packages the api-gateway Python Function App
- Installs shared library dependencies
- Deploys via Bicep `main.function.bicep` (reusable single-function template)
- Publishes function zip to the Function App

**Inputs (from core infra):**
- App Service Plan ID, subnet IDs, UAMI, storage account, App Insights connection string, private DNS zone ID

**Outputs:** Function App name and hostname

**How to use:**
```yaml
- template: .ado/api-gateway.yml
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
    storageAccountName: $(storageAccountName)
    uamiId: $(uamiId)
    uamiClientId: $(uamiClientId)
    appInsightsConnectionString: $(appInsightsConnectionString)
    privateDnsZoneAzureWebsitesId: $(privateDnsZoneAzureWebsitesId)
    deployPrivateEndpoints: false
```

---

### 2b. `sync-worker.yml` - Sync Worker Function App (Deploy)

**Deployment order:** 2b (requires core infra, parallel with api-gateway)

**What it does:**
- Builds and packages the sync-worker Python Function App
- Installs shared library dependencies
- Deploys via Bicep `main.function.bicep` (same reusable template)
- Publishes function zip to the Function App

**Inputs (from core infra):** Same as api-gateway

**Outputs:** Function App name and hostname

**How to use:** Same pattern as api-gateway, with `functionName: sync-worker` and `functionDir: apps/sync-worker`

---

### 2c. `merge-worker.yml` - Merge Worker Function App (Deploy)

**Deployment order:** 2c (requires core infra, parallel with api-gateway and sync-worker)

**What it does:**
- Builds and packages the merge-worker Python Function App
- Installs shared library dependencies
- Deploys via Bicep `main.function.bicep` (same reusable template)
- Publishes function zip to the Function App

**Inputs (from core infra):** Same as api-gateway

**Outputs:** Function App name and hostname

**How to use:** Same pattern as api-gateway, with `functionName: merge-worker` and `functionDir: apps/merge-worker`

---

**Key Difference (Modular Approach):**
- **Before:** Single `functions.yml` deployed all 3 function apps in one Bicep file
- **Now:** Separate pipeline files (api-gateway, sync-worker, merge-worker) each deploy a single function app using a reusable `main.function.bicep` template
- **Benefit:** Independent updates, faster CI/CD, cleaner separation of concerns

---

### 5. `container.yml` - Container Instance (Build + Deploy)

**Deployment order:** 4th (optional, independent if needed)

**What it does:**
- Builds training-worker Docker image
- Pushes image to Docker Hub
- Deploys Bicep `main.container.bicep` (Azure Container Instance)
- Configures container with storage, logging, and training parameters

**Inputs (from core infra):**
- UAMI, storage account, Log Analytics workspace

**Outputs:** Container instance ID and name

**How to use:**
```yaml
- template: .ado/container.yml
  parameters:
    azureServiceConnection: $(azureServiceConnection)
    subscriptionId: $(subscriptionId)
    resourceGroupName: $(resourceGroupName)
    location: $(location)
    namePrefix: $(namePrefix)
    dockerHubServiceConnection: $(dockerHubServiceConnection)
    dockerHubRepository: $(dockerHubRepository)
    imageTag: $(Build.BuildId)
    uamiPrincipalId: $(uamiPrincipalId)
    uamiId: $(uamiId)
    uamiClientId: $(uamiClientId)
    storageAccountName: $(storageAccountName)
    logAnalyticsWorkspaceId: $(logAnalyticsWorkspaceId)
    logAnalyticsWorkspaceKey: $(logAnalyticsWorkspaceKey)
    trainingMode: serverless
```

---

## Root Orchestrator: `azure-pipelines.yml`

The root `azure-pipelines.yml` file orchestrates the stage templates in sequence:

```yaml
variables:
  azureServiceConnection: ''
  subscriptionId: ''
  resourceGroupName: ''
  location: 'westus2'
  # ... more variables
```

**Typical execution flow:**
1. **infra-core** → Creates core infrastructure (VNet, identity, storage, App Service Plan), outputs IDs/names
2. **CI (ci-function.yml × 3)** → Builds each Function App individually, zips, uploads to Storage `${functionName}/${Build.BuildId}.zip`
3. **CD (cd-function.yml × 3)** → Deploys Function App infra + downloads/publishes matching artifact from Storage
4. **swa** → Deploys Static Web App (optional)
5. **container** → Builds/deploys training worker container (independent)

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
