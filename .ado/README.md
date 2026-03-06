# FolioHive DevOps

**Azure DevOps CI/CD Pipelines**

This directory contains Azure DevOps YAML pipelines for automated build, test, and deployment of the FolioHive platform. The pipeline architecture supports independent deployment of infrastructure, Functions, Static Web App, and container workloads.

---

## 📋 Table of Contents

- [Pipeline Overview](#pipeline-overview)
- [Pipeline Inventory](#pipeline-inventory)
- [Pipeline Architecture](#pipeline-architecture)
- [Variable Groups](#variable-groups)
- [Secrets Management](#secrets-management)
- [Deployment Flow](#deployment-flow)
- [Triggering Strategies](#triggering-strategies)
- [Troubleshooting](#troubleshooting)

---

## 🏗️ Pipeline Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Repository Changes                       │
└──────────┬────────────────────┬────────────────────────────┘
           │                    │
    ┌──────┴────────┐     ┌─────┴──────────┐
    │  Path Filter  │     │  Branch Filter │
    │  - infra/**   │     │  - main        │
    │  - api/**     │     │  - feature/*   │
    │  - ui/**      │     └────────────────┘
    └──────┬────────┘
           │
    ┌──────┴────────────────────────────────────────────────┐
    │                Pipeline Router                        │
    │  ┌─────────────┬──────────────┬──────────────────┐   │
    │  │   Infra     │   Functions  │      SWA         │   │
    │  │  Pipeline   │   Pipeline   │   Pipeline       │   │
    │  └─────────────┴──────────────┴──────────────────┘   │
    └───────────────────────────────────────────────────────┘
           │                  │                  │
    ┌──────┴──────┐    ┌──────┴──────┐   ┌──────┴──────┐
    │ Build Stage │    │ Build Stage │   │ Build Stage │
    │ - Validate  │    │ - Install   │   │ - npm build │
    │ - Bicep     │    │ - Test      │   │ - Artifact  │
    └──────┬──────┘    └──────┬──────┘   └──────┬──────┘
           │                  │                  │
    ┌──────┴──────┐    ┌──────┴──────┐   ┌──────┴──────┐
    │Deploy Stage │    │Deploy Stage │   │Deploy Stage │
    │ - az deploy │    │ - func pub  │   │ - swa cli   │
    └─────────────┘    └─────────────┘   └─────────────┘
```

---

## 📦 Pipeline Inventory

### CI/CD Pipelines

| Pipeline File | Purpose | Trigger Paths | Stages |
|--------------|---------|---------------|--------|
| **infra-core.yml** | Infrastructure deployment | `infra/bicep/**` | Validate → Deploy |
| **ci-functions.yml** | Function App CI | `api/v0.3.0/**` | Build → Test → Publish Artifact |
| **cd-functions.yml** | Function App CD | Manual/Artifact | Deploy → Smoke Test |
| **ci-swa.yml** | Static Web App CI | `ui/**` | Build → Test → Publish Artifact |
| **cd-swa.yml** | Static Web App CD | Manual/Artifact | Deploy |
| **ci-container.yml** | Container CI | `api/training-worker/**` | Build → Push to ACR |
| **cd-container.yml** | Container CD | Manual/Artifact | Deploy to ACI |

### Utility Pipelines

| Pipeline File | Purpose | Usage |
|--------------|---------|-------|
| **detect-function-changes.yml** | Detect modified functions | Template imported by ci-functions.yml |
| **ci-build-function.yml** | Build single function | Template for function builds |
| **cd-deploy-function.yml** | Deploy single function | Template for function deployments |
| **deploy-core-infra.yml** | Deploy infrastructure | Template imported by infra-core.yml |

### Parameter Files

| File | Purpose |
|------|---------|
| **params/params-container.yml** | Container-specific parameters (image tags, ACR settings) |

---

## 🏗️ Pipeline Architecture

### 1. Infrastructure Pipeline (`infra-core.yml`)

**Purpose**: Deploy Azure infrastructure using Bicep

**Trigger**:
```yaml
trigger:
  branches:
    include:
      - main
      - develop
  paths:
    include:
      - infra/bicep/**
```

**Stages**:

#### Stage 1: Validate
- Bicep lint check
- `az deployment sub validate` with what-if
- Parameter validation
- Cost estimation (future)

#### Stage 2: Deploy
- `az deployment sub create` with bicepparam
- Output resource IDs to variables
- Tag resources with build ID
- Post-deployment validation

**Variables Required**:
- `azureServiceConnection`: Azure DevOps service connection
- `bicepParamFile`: Path to parameter file (default: `main.bicepparam`)
- `deploymentLocation`: Azure region (default: `eastus`)

**Example Usage**:
```bash
# Trigger manually in Azure DevOps UI
# Or commit changes to infra/bicep/**
```

---

### 2. Function App CI Pipeline (`ci-functions.yml`)

**Purpose**: Build and test Azure Functions (consolidated v0.3.0 app), publish artifact

**Trigger**:
```yaml
trigger:
  branches:
    include:
      - main
      - feature/*
  paths:
    include:
      - api/v0.3.0/**
    exclude:
      - api/v0.3.0/tests/**
```

**Stages**:

#### Stage 1: Detect Changes
- Template: `detect-function-changes.yml`
- Compare current commit with last successful build
- Identify modified blueprints (api_gateway, sync_worker, cache_worker, reconciliation_worker)
- Set variables for downstream stages

#### Stage 2: Build
- Install Python 3.12
- Create virtual environment
- Install dependencies: `pip install -r requirements.txt`
- Install foliohive_shared: `pip install -e ../shared`
- Lint code: `ruff check`
- Run security scan: `bandit -r .`

#### Stage 3: Test
- Run pytest: `pytest tests/ -v --cov=blueprints`
- Generate coverage report (HTML + XML)
- Publish test results to Azure DevOps
- Enforce coverage threshold: 80%

#### Stage 4: Package
- Copy function app code to staging directory
- Include `host.json`, `requirements.txt`
- Exclude tests, __pycache__, .venv
- Zip artifact
- Publish to Azure Pipelines: `$(Build.ArtifactStagingDirectory)/function-app.zip`

**Artifact Name**: `function-app-drop`

**Variables Required**:
- `pythonVersion`: Python version (default: `3.12`)
- `workingDirectory`: Function app directory (default: `api/v0.3.0/function-app`)

---

### 3. Function App CD Pipeline (`cd-functions.yml`)

**Purpose**: Deploy Function App artifact to Azure

**Trigger**:
```yaml
trigger: none  # Manual or artifact-based
resources:
  pipelines:
    - pipeline: ci-functions
      source: ci-functions
      trigger:
        branches:
          include:
            - main
```

**Stages**:

#### Stage 1: Deploy to Dev
- Download artifact: `function-app-drop`
- Extract zip
- Deploy using Azure Functions task:
  ```yaml
  - task: AzureFunctionApp@1
    inputs:
      azureSubscription: $(azureServiceConnection)
      appType: functionAppLinux
      appName: $(functionAppName)
      package: $(Pipeline.Workspace)/function-app-drop/function-app.zip
      deploymentMethod: zipDeploy
  ```
- Wait for deployment health check (60 seconds)

#### Stage 2: Smoke Test
- HTTP request to Function App health endpoint (if exists)
- Validate response: 200 OK
- Check Application Insights for errors
- Rollback on failure (manual approval)

#### Stage 3: Deploy to Prod (Manual Approval)
- Wait for approval gate
- Deploy to production Function App
- Smoke test production
- Send deployment notification

**Variables Required**:
- `azureServiceConnection`: Azure service connection
- `functionAppNameDev`: Dev Function App name
- `functionAppNameProd`: Prod Function App name

---

### 4. Static Web App CI Pipeline (`ci-swa.yml`)

**Purpose**: Build Angular UI, publish artifact

**Trigger**:
```yaml
trigger:
  branches:
    include:
      - main
      - feature/*
  paths:
    include:
      - ui/**
    exclude:
      - ui/**/*.spec.ts
```

**Stages**:

#### Stage 1: Build
- Install Node.js 18
- `npm ci` (clean install)
- `npm run lint` (ESLint + Angular lint)
- `npm run build:prod` (production build)
- Output: `ui/dist/browser/`

#### Stage 2: Test
- `npm run test:ci` (Jasmine/Karma with headless Chrome)
- Generate coverage report
- Publish test results
- Enforce coverage threshold: 80%

#### Stage 3: Package
- Copy `dist/browser/` to staging directory
- Include `staticwebapp.config.json`
- Zip artifact
- Publish to Azure Pipelines: `$(Build.ArtifactStagingDirectory)/ui-app.zip`

**Artifact Name**: `swa-artifact`

**Variables Required**:
- `nodeVersion`: Node.js version (default: `18.x`)
- `workingDirectory`: UI directory (default: `ui`)

---

### 5. Static Web App CD Pipeline (`cd-swa.yml`)

**Purpose**: Deploy Static Web App infra (Bicep) and UI artifact to Azure

**Trigger**:
```yaml
trigger: none  # Manual or artifact-based
resources:
  pipelines:
    - pipeline: ci-swa
      source: ci-swa
      trigger:
        branches:
          include:
            - main
```

**Stages**:

#### Stage 1: Deploy Infra
- Deploy Static Web App using Bicep (`main.staticwebapp.bicep`)
- Output SWA deployment token
- Link backend API (Function App) via `staticSites/linkedBackends`

#### Stage 2: Deploy UI
- Download artifact: `swa-artifact`
- Extract zip
- Deploy using SWA CLI or `AzureStaticWebApp@0` task:
  ```yaml
  - task: AzureStaticWebApp@0
    inputs:
      azure_static_web_apps_api_token: $(swaDeploymentToken)
      app_location: '.'
      output_location: '.'
  ```

**Variables Required**:
- `azureServiceConnection`: Azure service connection
- `swaDeploymentToken`: Static Web App deployment token (from Key Vault or Bicep output)

---

### 6. Container CI Pipeline (`ci-container.yml`)

**Purpose**: Build Docker image for training-worker, push to Azure Container Registry

**Trigger**:
```yaml
trigger:
  branches:
    include:
      - main
  paths:
    include:
      - api/v0.3.0/training-worker/**
```

**Stages**:

#### Stage 1: Build
- Login to Azure Container Registry
- Build Docker image:
  ```bash
  docker build -t $(acrName).azurecr.io/training-worker:$(Build.BuildId) \
    -f api/v0.3.0/training-worker/Dockerfile \
    api/v0.3.0/training-worker/
  ```
- Tag with `latest` and `$(Build.BuildId)`
- Push to ACR

**Artifact**: Container image in ACR

**Variables Required**:
- `acrName`: Azure Container Registry name
- `azureServiceConnection`: Azure service connection

---

### 7. Container CD Pipeline (`cd-container.yml`)

**Purpose**: Deploy container to Azure Container Instances

**Trigger**:
```yaml
trigger: none  # Manual
```

**Stages**:

#### Stage 1: Deploy
- Pull image from ACR
- Create/update Azure Container Instance:
  ```yaml
  - task: AzureContainerInstances@2
    inputs:
      azureSubscription: $(azureServiceConnection)
      resourceGroupName: $(resourceGroupName)
      location: $(location)
      containerName: training-worker
      image: $(acrName).azurecr.io/training-worker:$(imageTag)
      cpu: 1.0
      memory: 1.5
  ```

**Variables Required**:
- `azureServiceConnection`: Azure service connection
- `resourceGroupName`: Resource group name
- `acrName`: Azure Container Registry name
- `imageTag`: Image tag to deploy (default: `latest`)

---

## 🔐 Variable Groups

### foliohive-Vars

**Purpose**: Shared variables across all pipelines

**Variables**:

| Variable Name | Type | Example | Description |
|--------------|------|---------|-------------|
| `azureServiceConnection` | String | `Azure-Prod-Connection` | Azure DevOps service connection |
| `subscriptionId` | String | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | Azure subscription ID |
| `resourceGroupName` | String | `foliohive-prod-rg` | Resource group name |
| `location` | String | `eastus` | Azure region |
| `functionAppName` | String | `foliohive-prod-func` | Function App name |
| `swaName` | String | `foliohive-prod-swa` | Static Web App name |
| `storageAccountName` | String | `foliohiveprodstg` | Storage account name |
| `acrName` | String | `foliohiveprodacr` | Container registry name |
| `keyVaultName` | String | `foliohive-prod-kv` | Key Vault name |

### foliohive-Secrets (Linked from Key Vault)

**Purpose**: Secure secrets from Azure Key Vault

**Secrets**:

| Secret Name | Key Vault Secret | Description |
|------------|------------------|-------------|
| `githubToken` | `github-token` | GitHub PAT for SWA deployment |
| `openaiApiKey` | `openai-api-key` | OpenAI API key |
| `swaDeploymentToken` | `swa-deployment-token` | Static Web App token |

**Setup**:
1. Create Key Vault in Azure
2. Add secrets to Key Vault
3. Grant Azure DevOps service principal access (Get Secret permission)
4. Link variable group to Key Vault in Azure DevOps

---

## 🔑 Secrets Management

### Azure Key Vault Integration

**Pipeline Configuration**:
```yaml
variables:
  - group: foliohive-Vars
  - group: foliohive-Secrets  # Linked to Key Vault

steps:
  - task: AzureKeyVault@2
    inputs:
      azureSubscription: $(azureServiceConnection)
      KeyVaultName: $(keyVaultName)
      SecretsFilter: '*'
      RunAsPreJob: true
```

**Access Secrets in Pipeline**:
```yaml
- script: |
    echo "Deploying with API key: $(openaiApiKey)"
  env:
    OPENAI_API_KEY: $(openaiApiKey)
```

### Secret Rotation

**Rotation Schedule**:
- GitHub Token: Every 90 days
- OpenAI API Key: Every 180 days
- SWA Deployment Token: On demand (if compromised)

**Rotation Process**:
1. Generate new secret in source (GitHub, OpenAI, Azure)
2. Update Key Vault secret value
3. Variable group automatically picks up new value
4. No pipeline changes required

---

## 🚀 Deployment Flow

### End-to-End Deployment

```
1. Developer commits to feature branch
   │
2. CI Pipelines triggered (Functions, SWA)
   │
   ├─→ Build artifacts
   ├─→ Run tests
   └─→ Publish to Azure Pipelines
   │
3. Pull request to main branch
   │
   ├─→ Code review
   ├─→ Automated tests pass
   └─→ Merge to main
   │
4. CD Pipelines triggered (Functions, SWA)
   │
   ├─→ Deploy to Dev environment
   ├─→ Smoke tests
   ├─→ Manual approval gate
   └─→ Deploy to Prod environment
   │
5. Infrastructure changes (if any)
   │
   └─→ Infra pipeline validates and deploys
   │
6. Post-deployment
   │
   ├─→ Monitor Application Insights
   ├─→ Verify health endpoints
   └─→ Send notifications
```

### Deployment Strategy

#### Blue-Green Deployment (Future)
- Deploy to staging slot
- Run smoke tests
- Swap slots (zero downtime)
- Rollback if issues detected

#### Canary Deployment (Future)
- Deploy to 10% of instances
- Monitor metrics for 30 minutes
- Gradually increase to 100%
- Rollback on error rate increase

---

## 🎯 Triggering Strategies

### Path-Based Triggers

**Infra Pipeline** (`infra/bicep/**`):
- Triggers on changes to Bicep files
- Excludes: Documentation changes

**Functions Pipeline** (`api/v0.3.0/**`):
- Triggers on changes to Function code
- Excludes: Tests, documentation

**SWA Pipeline** (`ui/**`):
- Triggers on changes to UI code
- Excludes: Test files, spec files

### Branch-Based Triggers

**Main Branch**:
- Triggers CI + CD pipelines
- Deploys to Dev → Prod (with approval)

**Feature Branches**:
- Triggers CI pipelines only
- No automatic deployment
- Artifacts available for manual deployment

**Develop Branch**:
- Triggers CI pipelines
- Auto-deploys to Dev environment
- No production deployment

### Manual Triggers

**Redeploy without code changes**:
```bash
# Trigger pipeline via Azure CLI
az pipelines run --name cd-functions --branch main
```

**Trigger with specific parameters**:
```bash
az pipelines run --name cd-functions \
  --branch main \
  --variables imageTag=v1.2.3
```

---

## 🐛 Troubleshooting

### Common Issues

#### Issue: "Pipeline failed: Unable to locate executable file: 'func'"
**Solution**: Ensure Azure Functions Core Tools task is included:
```yaml
- task: UseDotNet@2
  inputs:
    packageType: 'sdk'
    version: '6.x'

- bash: |
    npm install -g azure-functions-core-tools@4 --unsafe-perm true
  displayName: 'Install Azure Functions Core Tools'
```

#### Issue: "Deployment failed: Service connection not found"
**Solution**: Verify service connection name matches variable:
```bash
az devops service-endpoint list --organization https://dev.azure.com/yourorg --project yourproject
```

#### Issue: "Key Vault task failed: Access denied"
**Solution**: Grant service principal `Get` permission on secrets:
```bash
az keyvault set-policy \
  --name foliohive-prod-kv \
  --spn $(servicePrincipalId) \
  --secret-permissions get list
```

#### Issue: "Function deployment succeeded but app not responding"
**Solution**: Check Function App logs and Application Insights:
```bash
az functionapp log tail --name foliohive-prod-func --resource-group foliohive-prod-rg
```

### Debugging Pipelines

**Enable verbose logging**:
```yaml
variables:
  system.debug: true
```

**View pipeline logs**:
- Azure DevOps UI → Pipelines → Select run → View logs
- Download logs for offline analysis

**Test pipeline locally**:
```bash
# Install Azure Pipelines CLI
pip install azure-devops

# Validate pipeline YAML
az pipelines validate --yaml-path .ado/ci-functions.yml
```

---

## 📊 Pipeline Metrics

### Success Metrics

- **Build Success Rate**: >95%
- **Deployment Success Rate**: >98%
- **Average Build Time**: <5 minutes
- **Average Deployment Time**: <10 minutes
- **Test Coverage**: >80%

### Monitoring

**Kusto Query (Azure Monitor)**:
```kusto
PipelineRuns
| where ProjectName == "FolioHive"
| summarize 
    SuccessRate = countif(Result == "Succeeded") * 100.0 / count(),
    AvgDuration = avg(Duration)
  by PipelineName
| order by SuccessRate desc
```

---

## 🔄 Pipeline Maintenance

### Regular Tasks

**Weekly**:
- Review failed pipeline runs
- Update dependencies (Python packages, npm packages)
- Check for security vulnerabilities

**Monthly**:
- Review and rotate secrets
- Update pipeline agent versions
- Optimize build times

**Quarterly**:
- Review and update documentation
- Archive old artifacts
- Cost analysis (pipeline minutes)

---

## 📖 Additional Resources

- [Azure Pipelines Documentation](https://learn.microsoft.com/azure/devops/pipelines/)
- [YAML Schema Reference](https://learn.microsoft.com/azure/devops/pipelines/yaml-schema)
- [Azure Functions Deployment Guide](https://learn.microsoft.com/azure/azure-functions/functions-deployment-technologies)
- [Static Web Apps Deployment](https://learn.microsoft.com/azure/static-web-apps/build-configuration)

---

**Questions or Issues?** Check the [root README](../README.md) or submit a GitHub issue.
