# FolioHive Infrastructure

**Bicep Infrastructure as Code**

This directory contains Azure infrastructure definitions using Bicep. The infrastructure supports a cloud-native SaaS architecture with Function Apps, Static Web Apps, Storage, and monitoring.

---

## 📋 Table of Contents

- [Architecture Overview](#architecture-overview)
- [Resource Topology](#resource-topology)
- [Module Structure](#module-structure)
- [Deployment](#deployment)
- [Configuration](#configuration)
- [Networking](#networking)
- [Security](#security)
- [Cost Optimization](#cost-optimization)
- [Troubleshooting](#troubleshooting)

---

## 🏗️ Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│                      Azure Subscription                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  Resource Group                          │  │
│  │                                                          │  │
│  │  ┌─────────────────┐      ┌──────────────────────────┐  │  │
│  │  │ Virtual Network │      │  Static Web App          │  │  │
│  │  │   (VNet)        │      │  (Angular UI)            │  │  │
│  │  │  10.0.0.0/16    │      │  - CDN                   │  │  │
│  │  └────────┬────────┘      │  - Auto SSL              │  │  │
│  │           │               └──────────────────────────┘  │  │
│  │  ┌────────┴────────┐                                    │  │
│  │  │ Function App    │◄────┐                             │  │
│  │  │ (Flex Consumption)    │                             │  │
│  │  │ - Blueprints          │                             │  │
│  │  │ - VNet Inject         │                             │  │
│  │  └────────┬──────────────┘                             │  │
│  │           │                                             │  │
│  │  ┌────────┴────────┐      ┌──────────────────────────┐  │  │
│  │  │ Storage Account │      │  Application Insights    │  │  │
│  │  │ - Table         │      │  - Telemetry             │  │  │
│  │  │ - Blob          │      │  - Custom Metrics        │  │  │
│  │  │ - Queue         │      │  - Dashboards            │  │  │
│  │  │ - Private EP    │      └──────────────────────────┘  │  │
│  │  └─────────────────┘                                    │  │
│  │                                                          │  │
│  │  ┌─────────────────┐      ┌──────────────────────────┐  │  │
│  │  │ Key Vault       │      │  Managed Identity        │  │  │
│  │  │ - Secrets       │◄─────│  - System Assigned       │  │  │
│  │  │ - Private EP    │      │  - RBAC Assignments      │  │  │
│  │  └─────────────────┘      └──────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### Deployment Targets

The infrastructure supports multiple hosting patterns:

1. **Flex Consumption** (Recommended): `main.bicep` with `main.bicepparam`
2. **Premium Functions**: `main.functions-premium.bicep`
3. **Container Instances**: `main.container.bicep`

---

## 🧱 Resource Topology

### Core Resources

| Resource Type | SKU/Tier | Purpose |
|--------------|----------|---------|
| **Function App** | Flex Consumption | API + Workers (HTTP + Queue) |
| **Storage Account** | Standard LRS | Table, Blob, Queue storage |
| **Static Web App** | Standard | Angular UI with CDN |
| **Application Insights** | Standard | Telemetry and monitoring |
| **Key Vault** | Standard | Secrets management |
| **Virtual Network** | Standard | Private networking |
| **Managed Identity** | System Assigned | Passwordless authentication |
| **Log Analytics** | PerGB2018 | Log aggregation |

### Resource Naming Convention

Pattern: `{prefix}-{environment}-{region}-{resource-type}`

**Examples**:
- Function App: `foliohive-prod-eastus-func`
- Storage: `foliohiveprodstg` (no hyphens, 24 char limit)
- Static Web App: `foliohive-prod-swa`
- Key Vault: `foliohive-prod-kv`

---

## 📦 Module Structure

```
infra/bicep/
├── main.bicep                      # Primary entry point (Flex Consumption)
├── main.bicepparam                 # Default parameters
├── main.functions-premium.bicep    # Premium plan variant
├── main.container.bicep            # Container instance variant
├── main.functions.bicep            # Legacy functions (deprecated)
│
└── modules/                        # Reusable modules
    ├── functionAppFlex.bicep       # Flex Consumption Function App
    ├── functionApps.bicep          # Standard Function App
    ├── storage.bicep               # Storage Account (Table, Blob, Queue)
    ├── staticWebApp.bicep          # Static Web App
    ├── monitoring.bicep            # App Insights + Log Analytics
    ├── identity.bicep              # Managed Identity + RBAC
    ├── network.bicep               # VNet + Subnets
    ├── privateDns.bicep            # Private DNS zones
    └── containerInstance.bicep     # Container instances
```

### Module Descriptions

#### 1. `functionAppFlex.bicep`

**Purpose**: Deploy Azure Functions with Flex Consumption plan

**Parameters**:
- `location`: Azure region
- `functionAppName`: Unique app name
- `storageAccountName`: Storage connection
- `appInsightsKey`: Monitoring key
- `vnetSubnetId`: Subnet for VNet integration (optional)
- `managedIdentityId`: System-assigned identity

**Outputs**:
- `functionAppId`: Resource ID
- `functionAppName`: App name
- `defaultHostName`: FQDN

**Features**:
- Always-ready instances (0-1000)
- Per-second billing
- VNet integration support
- Managed Identity enabled
- Application settings injection

#### 2. `storage.bicep`

**Purpose**: Deploy Storage Account with Table, Blob, Queue services

**Parameters**:
- `storageAccountName`: Globally unique name (3-24 chars)
- `location`: Azure region
- `sku`: Storage SKU (Standard_LRS, Standard_GRS)
- `enablePrivateEndpoint`: Private networking

**Outputs**:
- `storageAccountId`: Resource ID
- `storageAccountName`: Account name
- `connectionString`: Connection string (secure)
- `blobEndpoint`: Blob service endpoint
- `tableEndpoint`: Table service endpoint
- `queueEndpoint`: Queue service endpoint

**Features**:
- Table Storage: 7 tables for normalized schema
- Blob Storage: `file-cache` container for cached files
- Queue Storage: `sync-jobs`, `cache-jobs` queues
- Private endpoint support
- Soft delete enabled

#### 3. `staticWebApp.bicep`

**Purpose**: Deploy Angular UI to Azure Static Web Apps

**Parameters**:
- `swaName`: Static Web App name
- `location`: Azure region (limited regions)
- `sku`: Free or Standard
- `repositoryUrl`: GitHub repo URL
- `branch`: Deployment branch

**Outputs**:
- `swaId`: Resource ID
- `swaDefaultHostname`: Default URL
- `swaApiKey`: Deployment token (secure)

**Features**:
- Global CDN
- Auto SSL certificates
- GitHub Actions integration
- SPA routing support
- Custom domain support

#### 4. `monitoring.bicep`

**Purpose**: Deploy Application Insights and Log Analytics workspace

**Parameters**:
- `appInsightsName`: App Insights resource name
- `logAnalyticsName`: Workspace name
- `location`: Azure region

**Outputs**:
- `appInsightsId`: Resource ID
- `instrumentationKey`: App Insights key
- `connectionString`: Connection string
- `logAnalyticsId`: Workspace ID

**Features**:
- Custom metrics (AI token usage, cache hits)
- Live metrics streaming
- Log queries (Kusto)
- Alerting and dashboards

#### 5. `identity.bicep`

**Purpose**: Create Managed Identity and assign RBAC roles

**Parameters**:
- `identityName`: Managed Identity name
- `storageAccountId`: Storage account resource ID
- `keyVaultId`: Key Vault resource ID (optional)

**Outputs**:
- `identityId`: Managed Identity resource ID
- `principalId`: Identity principal ID

**RBAC Assignments**:
- Storage Account: `Storage Blob Data Contributor`, `Storage Queue Data Contributor`, `Storage Table Data Contributor`
- Key Vault: `Key Vault Secrets User`

#### 6. `network.bicep`

**Purpose**: Deploy Virtual Network with subnets

**Parameters**:
- `vnetName`: VNet name
- `vnetAddressPrefix`: CIDR (e.g., 10.0.0.0/16)
- `functionSubnetPrefix`: Subnet CIDR (e.g., 10.0.1.0/24)

**Outputs**:
- `vnetId`: VNet resource ID
- `functionSubnetId`: Function subnet ID

**Features**:
- Service endpoints for Storage
- Delegation for Function App integration
- Network security groups

#### 7. `privateDns.bicep`

**Purpose**: Create Private DNS zones for private endpoints

**Parameters**:
- `vnetId`: Virtual Network ID
- `storageAccountName`: Storage account name

**Outputs**:
- `privateDnsZoneIds`: Array of DNS zone IDs

**DNS Zones**:
- `privatelink.blob.core.windows.net`
- `privatelink.table.core.windows.net`
- `privatelink.queue.core.windows.net`
- `privatelink.vaultcore.azure.net`

---

## 🚀 Deployment

### Prerequisites

- Azure CLI 2.50+
- Azure subscription with Contributor access
- Bicep CLI (bundled with Azure CLI)
- GitHub Personal Access Token (for Static Web App)

### Deployment Steps

#### 1. Login to Azure

```bash
az login
az account set --subscription "Your-Subscription-Name"
```

#### 2. Review Parameters

Edit `main.bicepparam`:

```bicep
using 'main.bicep'

param projectName = 'foliohive'
param environment = 'prod'
param location = 'eastus'
param githubToken = '<your-github-token>'
param openaiApiKey = '<your-openai-key>'
```

#### 3. Validate Deployment

```bash
az deployment sub create \
  --name foliohive-validation \
  --location eastus \
  --template-file main.bicep \
  --parameters main.bicepparam \
  --what-if
```

#### 4. Deploy Infrastructure

```bash
az deployment sub create \
  --name foliohive-deployment \
  --location eastus \
  --template-file main.bicep \
  --parameters main.bicepparam
```

**Deployment Time**: 5-10 minutes

#### 5. Verify Deployment

```bash
# List deployed resources
az resource list --resource-group foliohive-prod-rg --output table

# Get Function App URL
az functionapp show \
  --name foliohive-prod-func \
  --resource-group foliohive-prod-rg \
  --query defaultHostName \
  --output tsv

# Get Static Web App URL
az staticwebapp show \
  --name foliohive-prod-swa \
  --resource-group foliohive-prod-rg \
  --query defaultHostname \
  --output tsv
```

### Alternative Deployment Methods

#### Using Azure DevOps Pipeline

Automated via `.ado/infra-core-cd.yml`:

```yaml
trigger:
  branches:
    include:
      - main
  paths:
    include:
      - infra/bicep/**

steps:
  - task: AzureCLI@2
    inputs:
      azureSubscription: 'Azure-Service-Connection'
      scriptType: 'bash'
      scriptLocation: 'inlineScript'
      inlineScript: |
        az deployment sub create \
          --name $(Build.BuildId) \
          --location eastus \
          --template-file infra/bicep/main.bicep \
          --parameters infra/bicep/main.bicepparam
```

#### Using GitHub Actions

```yaml
name: Deploy Infrastructure

on:
  push:
    branches: [main]
    paths: ['infra/bicep/**']

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: azure/login@v1
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}
      
      - name: Deploy Bicep
        run: |
          az deployment sub create \
            --name ${{ github.run_id }} \
            --location eastus \
            --template-file infra/bicep/main.bicep \
            --parameters infra/bicep/main.bicepparam
```

---

## ⚙️ Configuration

### Parameter Reference

#### Required Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `projectName` | string | Project prefix for resource names | `foliohive` |
| `environment` | string | Environment name (dev, staging, prod) | `prod` |
| `location` | string | Azure region | `eastus` |

#### Optional Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `functionAppSku` | string | `FlexConsumption` | Function App plan SKU |
| `storageSku` | string | `Standard_LRS` | Storage redundancy |
| `swaSku` | string | `Standard` | Static Web App tier |
| `enablePrivateEndpoints` | bool | `true` | Enable private networking |
| `enableVNetIntegration` | bool | `true` | Function VNet integration |

#### Secret Parameters (Secure)

| Parameter | Type | Description | How to Obtain |
|-----------|------|-------------|---------------|
| `githubToken` | securestring | GitHub PAT for SWA | GitHub Settings → Developer Settings → PAT |
| `openaiApiKey` | securestring | OpenAI API key | OpenAI Platform → API Keys |

### Environment-Specific Configurations

#### Development
```bicep
param environment = 'dev'
param storageSku = 'Standard_LRS'
param swaSku = 'Free'
param enablePrivateEndpoints = false
```

#### Staging
```bicep
param environment = 'staging'
param storageSku = 'Standard_GRS'
param swaSku = 'Standard'
param enablePrivateEndpoints = true
```

#### Production
```bicep
param environment = 'prod'
param storageSku = 'Standard_GRS'
param swaSku = 'Standard'
param enablePrivateEndpoints = true
param enableVNetIntegration = true
```

---

## 🌐 Networking

### Private Networking Architecture

```
┌────────────────────────────────────────────┐
│         Virtual Network (10.0.0.0/16)      │
│                                            │
│  ┌──────────────────────────────────────┐ │
│  │  Function Subnet (10.0.1.0/24)       │ │
│  │  - VNet Integration                  │ │
│  │  - Delegation: Microsoft.Web/        │ │
│  │    serverFarms                       │ │
│  └──────────────────────────────────────┘ │
│                                            │
│  ┌──────────────────────────────────────┐ │
│  │  Private Endpoint Subnet (10.0.2.0/24│ │
│  │  - Storage Private Endpoints         │ │
│  │  - Key Vault Private Endpoint        │ │
│  └──────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

### Service Endpoints

Enabled on Function subnet:
- `Microsoft.Storage` - Access to Storage services
- `Microsoft.KeyVault` - Access to Key Vault
- `Microsoft.Web` - Function App management

### Private Endpoints

**Storage Account**:
- Blob service: `foliohiveprodstg.privatelink.blob.core.windows.net`
- Table service: `foliohiveprodstg.privatelink.table.core.windows.net`
- Queue service: `foliohiveprodstg.privatelink.queue.core.windows.net`

**Key Vault**:
- Vault service: `foliohive-prod-kv.privatelink.vaultcore.azure.net`

### Network Security

- **Firewall Rules**: Storage Account restricted to VNet
- **NSG**: Deny inbound by default, allow Function subnet
- **Private DNS**: Automatic DNS resolution for private endpoints

---

## 🔐 Security

### Managed Identity

**System-Assigned Identity** enabled on Function App:
- No stored credentials in code
- Automatic credential rotation
- RBAC-based access control

**RBAC Role Assignments**:
```bicep
// Storage roles
'Storage Blob Data Contributor'
'Storage Queue Data Contributor'
'Storage Table Data Contributor'

// Key Vault role
'Key Vault Secrets User'
```

### Secrets Management

**Key Vault Secrets**:
- `github-token`: GitHub API token
- `openai-api-key`: OpenAI API key
- `storage-connection-string`: Storage connection string (backup)

**Access Pattern**:
```python
# Function App code
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

credential = DefaultAzureCredential()
client = SecretClient(vault_url="https://foliohive-prod-kv.vault.azure.net/", credential=credential)

github_token = client.get_secret("github-token").value
```

### Security Best Practices

- ✅ Managed Identity for all Azure service communication
- ✅ Private endpoints for Storage and Key Vault
- ✅ VNet integration for Function App
- ✅ No credentials in source code or environment variables
- ✅ HTTPS-only traffic enforced
- ✅ Storage soft delete enabled (30-day retention)
- ✅ Application Insights telemetry for security monitoring

---

## 💰 Cost Optimization

### Cost Breakdown (Estimated Monthly)

| Resource | SKU | Estimated Cost | Notes |
|----------|-----|----------------|-------|
| **Function App** | Flex Consumption | $15-50 | Based on execution time |
| **Storage Account** | Standard LRS | $5-15 | 10GB blob, 1M transactions |
| **Static Web App** | Standard | $9 | Fixed cost |
| **Application Insights** | Standard | $5-10 | 5GB ingestion |
| **Key Vault** | Standard | $1 | Per secret + operations |
| **VNet** | Standard | $0 | No gateway required |
| **Bandwidth** | Outbound | $5-10 | First 5GB free |
| **Total** | - | **$40-95/month** | Production workload |

### Cost Optimization Strategies

#### 1. Storage
- Use LRS instead of GRS in non-critical environments
- Enable blob lifecycle management (archive old files)
- Set TTL on cached blobs (future)
- Use Table Storage (cheap) instead of Cosmos DB

#### 2. Function App
- Flex Consumption: Pay only for execution time (no idle costs)
- Optimize function execution time (reduce AI token usage)
- Use always-ready instances sparingly (0-1)

#### 3. Static Web App
- Use Free tier for dev/staging
- Enable CDN caching (reduce origin requests)

#### 4. Monitoring
- Set Application Insights sampling rate (reduce ingestion)
- Use daily cap on telemetry
- Archive logs to cheap storage after 30 days

#### 5. Networking
- Private endpoints: $7.50/month per endpoint (only if needed)
- Avoid VPN Gateway ($45-140/month) - use private endpoints instead

### Cost Monitoring

**Azure Cost Management Query**:
```kusto
ResourceCosts
| where ResourceGroup == "foliohive-prod-rg"
| summarize TotalCost = sum(Cost) by ResourceType
| order by TotalCost desc
```

**Budget Alerts**:
- Set monthly budget: $100
- Alert at 80% threshold
- Alert at 100% threshold

---

## 🔍 Troubleshooting

### Common Issues

#### Issue: "Deployment failed: Resource name already exists"
**Solution**: Resource names must be globally unique (Storage, Static Web App, Key Vault). Update `projectName` or `environment` parameter.

#### Issue: "Function App cannot access Storage"
**Solution**: Verify Managed Identity has correct RBAC roles:
```bash
az role assignment list \
  --assignee $(az functionapp identity show --name foliohive-prod-func --resource-group foliohive-prod-rg --query principalId -o tsv) \
  --output table
```

#### Issue: "Private endpoint DNS not resolving"
**Solution**: Ensure Private DNS zone is linked to VNet:
```bash
az network private-dns link vnet list \
  --resource-group foliohive-prod-rg \
  --zone-name privatelink.blob.core.windows.net \
  --output table
```

#### Issue: "Static Web App deployment fails"
**Solution**: Verify GitHub token has `repo` and `workflow` scopes. Check SWA deployment logs in GitHub Actions.

### Diagnostic Commands

```bash
# Check deployment status
az deployment sub show \
  --name foliohive-deployment \
  --query properties.provisioningState

# List all resources
az resource list --resource-group foliohive-prod-rg --output table

# Get Function App logs
az functionapp log tail \
  --name foliohive-prod-func \
  --resource-group foliohive-prod-rg

# Test connectivity from Function App
az functionapp show \
  --name foliohive-prod-func \
  --resource-group foliohive-prod-rg \
  --query outboundIpAddresses

# Check Key Vault access
az keyvault secret show \
  --vault-name foliohive-prod-kv \
  --name github-token \
  --query value -o tsv
```

### Bicep Validation

```bash
# Lint Bicep file
az bicep lint --file main.bicep

# Build to ARM template
az bicep build --file main.bicep

# Decompile ARM to Bicep
az bicep decompile --file template.json
```

---

## 📖 Additional Resources

- [Azure Functions Flex Consumption Documentation](https://learn.microsoft.com/azure/azure-functions/flex-consumption-plan)
- [Bicep Language Reference](https://learn.microsoft.com/azure/azure-resource-manager/bicep/)
- [Azure Static Web Apps Documentation](https://learn.microsoft.com/azure/static-web-apps/)
- [Private Endpoint Best Practices](https://learn.microsoft.com/azure/private-link/private-endpoint-overview)
- [Managed Identity Best Practices](https://learn.microsoft.com/azure/active-directory/managed-identities-azure-resources/overview)

---

## 🔄 Update Strategy

### Infrastructure Updates

1. **Test in Development**: Deploy changes to dev environment first
2. **Validate with What-If**: Use `--what-if` flag to preview changes
3. **Stage Deployment**: Deploy to staging for integration testing
4. **Production Deployment**: Deploy to production during maintenance window
5. **Rollback Plan**: Keep previous deployment name for easy rollback

### Rollback Procedure

```bash
# List recent deployments
az deployment sub list --query "[].{name:name, state:properties.provisioningState, timestamp:properties.timestamp}" --output table

# Rollback to previous deployment
az deployment sub create \
  --name foliohive-rollback \
  --location eastus \
  --template-file main.bicep \
  --parameters @previous-deployment-params.json
```

---

**Questions or Issues?** Check the [root README](../../README.md) or submit a GitHub issue.
