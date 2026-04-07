# FolioHive Infrastructure

This folder contains the current Azure Bicep templates used by the Azure DevOps pipelines to deploy FolioHive. The implementation is resource-group scoped and split into three layers: core infrastructure, the Flex Function App, and the Static Web App.

## Current topology

```text
main.bicep
  ├─ identity.bicep      -> user-assigned managed identity
  ├─ network.bicep       -> VNet + functions subnet + private-endpoints subnet
  ├─ privateDns.bicep    -> private DNS zones for blob, queue, table
  ├─ monitoring.bicep    -> Log Analytics + Application Insights
  └─ storage.bicep       -> storage account + private endpoints + RBAC

main.functions.bicep
  └─ functionAppFlex.bicep -> Flex Consumption Linux Function App

main.staticwebapp.bicep
  └─ staticWebApp.bicep     -> Standard Static Web App, optional linked backend
```

## What is actually deployed

### 1. Core infrastructure: `main.bicep`

Deploys the shared platform resources used by the app:

- User-assigned managed identity.
- Virtual network with two subnets:
  - `snet-functions` for Function App VNet integration.
  - `snet-private-endpoints` for Storage private endpoints.
- Private DNS zones for Storage Blob, Queue, and Table endpoints, each linked to the VNet.
- Log Analytics workspace and Application Insights.
- Storage account with:
  - public network access disabled,
  - shared key access disabled,
  - private endpoints for blob, queue, and table,
  - `function-deployments` blob container for Function package deployment.
- RBAC assignments that give the managed identity access to Storage and App Insights metrics publishing.

Core outputs are designed for downstream deployments and include:

- `uamiId`, `uamiClientId`, `uamiPrincipalId`
- `appInsightsName`, `logAnalyticsWorkspaceId`
- `vnetId`, `functionsSubnetId`, `privateEndpointsSubnetId`
- `storageAccountName`, `storageAccountId`
- `namePrefixOut`, `uniqueSuffixOut`

### 2. Function infrastructure: `main.functions.bicep`

Deploys a single Flex Consumption Function App by composing `modules/functionAppFlex.bicep`.

Key characteristics:

- Linux Function App on SKU `FC1`.
- Python runtime `3.13`.
- User-assigned managed identity only.
- Deployment package sourced from the Storage blob container `function-deployments`.
- Managed-identity-based `AzureWebJobsStorage` configuration.
- VNet integration using `functionsSubnetId`.
- Optional diagnostics to Log Analytics.
- Optional CORS origins passed in from deployment orchestration.
- Configurable Flex scaling knobs:
  - `flexMaximumInstanceCount`
  - `flexInstanceMemoryMb`
  - `httpPerInstanceConcurrency`
  - `flexAlwaysReadyInstanceCount`

Outputs:

- `functionAppId`
- `functionAppName`
- `functionAppDefaultHostname`
- `flexPlanId`
- `flexPlanName`

### 3. Static Web App infrastructure: `main.staticwebapp.bicep`

Deploys a Standard Static Web App by composing `modules/staticWebApp.bicep`.

Key characteristics:

- Azure DevOps-backed Static Web App definition.
- Repository URL defaults to the Azure DevOps repo.
- Branch is fixed to `main`.
- Build metadata points to `ui` with output `dist/browser`.
- Can optionally create `staticSites/linkedBackends` for the Function App.
- Can optionally set `API_BASE_URL` when a Function hostname is provided.

Outputs:

- `staticWebAppUrl`
- `staticWebAppId`
- `staticWebAppName`

## Files in this folder

- `main.bicep`: shared core infrastructure entrypoint.
- `main.bicepparam`: default parameter file for `main.bicep`.
- `main.functions.bicep`: Function App entrypoint.
- `main.staticwebapp.bicep`: Static Web App entrypoint.
- `main.functions.json`: compiled ARM JSON artifact checked into the repo.
- `modules/identity.bicep`: user-assigned managed identity.
- `modules/network.bicep`: VNet and subnets.
- `modules/privateDns.bicep`: Storage private DNS zones and VNet links.
- `modules/monitoring.bicep`: Log Analytics and Application Insights.
- `modules/storage.bicep`: locked-down Storage account, private endpoints, RBAC.
- `modules/functionAppFlex.bicep`: Flex Consumption Function App and plan.
- `modules/staticWebApp.bicep`: Static Web App and optional backend link.

## Naming and scope

- All entrypoints use `targetScope = 'resourceGroup'`.
- Resource naming is based on `namePrefix` plus a deterministic `uniqueSuffix` derived from the resource group ID.
- The Storage account name is generated as `cfsa${uniqueSuffix}` and trimmed to Azure's 24-character limit.

## Parameters that matter

### `main.bicep`

- `location`
- `tags`
- `namePrefix`
- `vnetAddressPrefix`
- `functionsSubnetPrefix`
- `privateEndpointsSubnetPrefix`

### `main.functions.bicep`

- `functionAppName`
- `functionsSubnetId`
- `storageAccountName`
- `uamiId`
- `uamiClientId`
- `appInsightsName`
- `logAnalyticsWorkspaceId`
- `corsAllowedOrigins`

### `main.staticwebapp.bicep`

- `namePrefix`
- `uniqueSuffix`
- `repositoryUrl`
- `backendFunctionAppId`
- `backendFunctionAppDefaultHostname`
- `enableLinkedBackend`

## Deployment model

The expected deployment order is:

1. Deploy `main.bicep`.
2. Use its outputs to deploy `main.functions.bicep`.
3. Optionally pass the Function outputs into `main.staticwebapp.bicep`.

This is the same order used by the Azure DevOps pipelines in `.ado/`.

Example commands:

```bash
az deployment group create \
  --resource-group <rg> \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.bicepparam

az deployment group create \
  --resource-group <rg> \
  --template-file infra/bicep/main.functions.bicep \
  --parameters functionAppName=<name> functionsSubnetId=<subnetId> \
               storageAccountName=<storage> uamiId=<uamiId> \
               uamiClientId=<uamiClientId> appInsightsName=<appi>
```

## Important boundaries

This folder does not currently implement:

- Key Vault.
- Premium Function plans.
- Container Instances.
- Network security groups.
- Private DNS zones for Key Vault or App Service.
- Multiple Function App entrypoints in Bicep.

Some older docs and comments in the repo still mention those patterns, but they are not part of the current Bicep surface in this directory.

## Notes on `main.bicepparam`

`main.bicepparam` is a thin default file for `main.bicep`. It sets `location`, `namePrefix`, and tags. In CI/CD, those values are typically overridden by pipeline variables.