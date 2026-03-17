# Azure Pipelines (.ado)

This folder contains Cloudfolio’s Azure DevOps (ADO) pipeline definitions and reusable templates.

## What’s in here

### Pipelines (entrypoints)

- `infra-core.yml` — deploys core infrastructure (networking, storage, identity, monitoring).
- `ci-functions.yml` — unified CI for Function Apps; detects what changed and builds only those functions.
- `cd-functions.yml` — unified CD for Function Apps; triggered by Function CI and deploys available artifacts.
- `ci-swa.yml` — builds the Angular UI and publishes the `swa-artifact` pipeline artifact.
- `cd-swa.yml` — deploys Static Web App infra (Bicep) and publishes the UI artifact.
- `ci-container.yml` — builds/pushes the training-worker container image.
- `cd-container.yml` — deploys the container workload (Bicep).

### Templates (reusable building blocks)

- `deploy-core-infra.yml` — reusable “ensure core infra” deployment used by CD pipelines.
- `detect-function-changes.yml` — determines which Function Apps changed in the repo.
- `ci-build-function.yml` — builds a single Function App zip and publishes a pipeline artifact.
- `cd-deploy-function.yml` — deploys Function App infra (Bicep) and publishes the Function zip.

## How the Function CI/CD works

- CI (`ci-functions.yml`) runs on changes under:
  - `api/v0.4.0/api-gateway/**`
  - `api/v0.4.0/sync-worker/**`
  - `api/v0.4.0/merge-worker/**`
  - `api/v0.4.0/shared/**`
- CI publishes artifacts per function using the naming pattern:
  - `api-gateway-$(Build.BuildId)`
  - `sync-worker-$(Build.BuildId)`
  - `merge-worker-$(Build.BuildId)`
- CD (`cd-functions.yml`) is triggered by the Function CI pipeline resource. It attempts to deploy all functions; if a function artifact wasn’t built, its deployment is skipped.

## How the UI (SWA) CI/CD works

- CI (`ci-swa.yml`) builds the Angular UI and publishes `swa-artifact` from `ui/dist/browser/`.
- CD (`cd-swa.yml`) downloads `swa-artifact`, deploys infra via Bicep (`infra/bicep/main.staticwebapp.bicep`), then deploys the artifact using `AzureStaticWebApp@0`.
- CD also queries the deployed `api-gateway` Function App and can link it as a backend (via Bicep `staticSites/linkedBackends`).

## Required variables

Pipelines assume you have an Azure DevOps Variable Group named `folioVars` (see `variables: - group: 'folioVars'`). At minimum, it should contain values for:

- `azureServiceConnection`
- `subscriptionId`
- `resourceGroupName`
- `location`
- `namePrefix`

Some pipelines also expect:

- `pythonVersion` (used by Function CI)

## Where infra lives

- Core infra Bicep entrypoint: `infra/bicep/main.bicep`
- Function Apps infra entrypoint (Flex Consumption): `infra/bicep/main.functions.bicep`
- Legacy Premium Function Apps entrypoint: `infra/bicep/main.functions-premium.bicep`
- Static Web App infra entrypoint: `infra/bicep/main.staticwebapp.bicep`
- Container infra entrypoint: `infra/bicep/main.container.bicep`

## Notes

- These YAML files are designed to be used as separate pipelines in ADO (each YAML is an entrypoint).
- If you rename an Azure DevOps pipeline, update the `resources.pipelines[*].source` values in the CD pipelines accordingly.
# Azure Pipelines (.ado)

This folder contains Cloudfolio’s Azure DevOps (ADO) pipeline definitions and reusable templates.

## What’s in here

### Pipelines (entrypoints)

- `infra-core.yml` — deploys core infrastructure (networking, storage, identity, monitoring).
- `ci-functions.yml` — unified CI for Function Apps; detects what changed and builds only those functions.
- `cd-functions.yml` — unified CD for Function Apps; triggered by Function CI and deploys available artifacts.
- `ci-swa.yml` — builds the Angular UI and publishes the `swa-artifact` pipeline artifact.
- `cd-swa.yml` — deploys Static Web App infra (Bicep) and publishes the UI artifact.
- `ci-container.yml` — builds/pushes the training-worker container image.
- `cd-container.yml` — deploys the container workload (Bicep).

### Templates (reusable building blocks)

- `deploy-core-infra.yml` — reusable “ensure core infra” deployment used by CD pipelines.
- `detect-function-changes.yml` — determines which Function Apps changed in the repo.
- `ci-build-function.yml` — builds a single Function App zip and publishes a pipeline artifact.
- `cd-deploy-function.yml` — deploys Function App infra (Bicep) and publishes the Function zip.

## How the Function CI/CD works

- CI (`ci-functions.yml`) runs on changes under:
  - `api/v0.2.0/api-gateway/**`
  - `api/v0.2.0/sync-worker/**`
  - `api/v0.2.0/merge-worker/**`
  - `api/v0.2.0/shared/**`
- CI publishes artifacts per function using the naming pattern:
  - `api-gateway-$(Build.BuildId)`
  - `sync-worker-$(Build.BuildId)`
  - `merge-worker-$(Build.BuildId)`
- CD (`cd-functions.yml`) is triggered by the Function CI pipeline resource. It attempts to deploy all functions; if a function artifact wasn’t built, its deployment is skipped.

## How the UI (SWA) CI/CD works

- CI (`ci-swa.yml`) builds the Angular UI and publishes `swa-artifact` from `ui/dist/browser/`.
- CD (`cd-swa.yml`) downloads `swa-artifact`, deploys infra via Bicep (`infra/bicep/main.staticwebapp.bicep`), then deploys the artifact using `AzureStaticWebApp@0`.
- CD also queries the deployed `api-gateway` Function App and can link it as a backend (via Bicep `staticSites/linkedBackends`).

## Required variables

Pipelines assume you have an Azure DevOps Variable Group named `folioVars` (see `variables: - group: 'folioVars'`). At minimum, it should contain values for:

- `azureServiceConnection`
- `subscriptionId`
- `resourceGroupName`
- `location`
- `namePrefix`

Some pipelines also expect:

- `pythonVersion` (used by Function CI)

## Where infra lives

- Core infra Bicep entrypoint: `infra/bicep/main.bicep`
- Function Apps infra entrypoint (Flex Consumption): `infra/bicep/main.functions.bicep`
- Legacy Premium Function Apps entrypoint: `infra/bicep/main.functions-premium.bicep`
- Static Web App infra entrypoint: `infra/bicep/main.staticwebapp.bicep`
- Container infra entrypoint: `infra/bicep/main.container.bicep`

## Notes

- These YAML files are designed to be used as separate pipelines in ADO (each YAML is an entrypoint).
- If you rename an Azure DevOps pipeline, update the `resources.pipelines[*].source` values in the CD pipelines accordingly.
# .ado/README.md - Pipeline Templates and Parameters Guide
