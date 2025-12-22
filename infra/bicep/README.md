# Cloudfolio — Bicep Infra Review & Runbook ✅

Summary: This manual explains what each Bicep module does, how the modules are configured, how to safely build/validate/deploy the infra following Azure best practices, and includes test steps (local & deployed) and troubleshooting tips. Save this as `INFRA-README.md` or add into your repo docs.

---

## Table of contents
1. Overview & key files 📁  
2. Module-by-module explanation 🔍  
3. Toggle & parameter controls (private endpoints) ⚙️  
4. Prerequisites & safety checks ⚠️  
5. Build → What‑if → Deploy (commands) 🔧  
6. Post‑deploy validation & tests ✅  
7. Using API code in `api/v0.2.0` for integration tests 🔁  
8. Troubleshooting 🛠️  
9. Security & Azure best practices 🔒  
10. Quick checklist for PR/Deploy ✔️

---

## 1) Overview & key files 📁
- Orchestrator: `infra/bicep/main.bicep` (params, module wiring, outputs)  
- Param defaults: `infra/bicep/main.bicepparam` (location, namePrefix, tags)  
- Modules in `infra/bicep/modules/`:
  - `network.bicep` — VNet + subnets (function & private endpoint subnets)
  - `privateDns.bicep` — Private DNS zones & VNet links (storage + azurewebsites)
  - `identity.bicep` — User-assigned Managed Identity (UAMI)
  - `monitoring.bicep` — Log Analytics + App Insights
  - `storage.bicep` — Storage account (private-only) + PEs (blob/queue/table) + DNS groups + RBAC assignments
  - `functionApps.bicep` — App Service Plan + 3 Function Apps + conditional PEs + DNS groups
  - `staticWebApp.bicep` — Azure Static Web App connected to api-gateway Function App

---

## 2) Module-by-module explanation 🔍
- **main.bicep**
  - Centralizes parameters and calls modules. Generates `uniqueSuffix` for deterministic, unique names. Exports `storageAccountName` and `functionAppNames`.
- **network.bicep**
  - Creates VNet and two subnets:
    - `snet-functions` (delegated to App Service for VNet integration)
    - `snet-private-endpoints` (privateEndpointNetworkPolicies = Disabled)
  - Outputs subnet IDs for wiring.
- **privateDns.bicep**
  - Adds private DNS zones used by private endpoints (storage services and `privatelink.azurewebsites.net`) and creates virtual network links (registration disabled by default). Uses `environment().suffixes.storage` to avoid hardcoding cloud suffixes.
- **identity.bicep**
  - Creates a UAMI for Function Apps and service principal output fields (clientId/principalId).
- **monitoring.bicep**
  - Creates Log Analytics workspace + App Insights; outputs connection string for Function App instrumentation.
- **storage.bicep**
  - Creates a storage account with:
    - `publicNetworkAccess: Disabled`, `networkAcls.defaultAction: Deny`
    - `supportsHttpsTrafficOnly`, `minimumTlsVersion: TLS1_2`
  - Private Endpoints for `blob`, `queue`, `table` and private DNS zone groups
  - RBAC role assignments giving the UAMI Data Contributor roles for the storage account
- **functionApps.bicep**
  - App Service Plan (Elastic Premium) + three Function Apps (api-gateway, merge-worker, sync-worker).
  - Each Function App:
    - `identity: UserAssigned` (UAMI)
    - `publicNetworkAccess: Disabled`
    - `virtualNetworkSubnetId` set to functions subnet (VNet integration -> outbound via VNet)
    - Optionally created private endpoints (PEs) + DNS zone groups if `deployPrivateEndpoints` param is true
- **staticWebApp.bicep**
  - Creates an Azure Static Web App (SWA) resource.
  - Configured to connect to the api-gateway Function App via the `api` property (using the Function App's default hostname).
  - Includes build properties for Angular app deployment (adjust `appLocation`/`outputLocation` as needed).
  - Outputs the SWA URL and ID for access and further configuration.

---

## 3) Toggle & parameter controls (private endpoints) ⚙️
- Control param (in `main.bicep`):
  - `param deployFunctionAppPrivateEndpoints bool = false`
  - Passed to module as `deployPrivateEndpoints`.
- Default behavior:
  - Default is `false` → PEs for Function Apps will NOT be deployed, templates remain in code.
- How to change:
  - Temporarily (CLI): `--parameters deployFunctionAppPrivateEndpoints=true`
  - In params file (`infra/bicep/main.bicepparam`): add `param deployFunctionAppPrivateEndpoints = true`
- Why this approach:
  - Keeps code present and testable without deploying inbound PEs.
  - Clean, safe, and follows Bicep best practice (conditional resources with `if`).

---

## 4) Prerequisites & safety checks ⚠️
- Tools:
  - Azure CLI (latest) + logged in: `az login` & select subscription: `az account set --subscription <id>`
  - Bicep (via `az bicep`): `az bicep version`
- RBAC: You need privileges to create role assignments (e.g., Owner or User Access Admin).
- Resource Group:
  - Create or confirm: `az group create -n rg-cloudfolio -l westus2`
- Safety:
  - Use `what-if` in PRs and CI to audit changes.
  - Test first in a non-production subscription/resource group.

---

## 5) Build → What‑if → Deploy (commands) 🔧
1. Lint/Compile
```bash
az bicep build --file infra/bicep/main.bicep
```
2. Dry-run / What‑If
```bash
az deployment group what-if \
  --resource-group rg-cloudfolio \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.bicepparam \
  --name bicep-whatif
```
3. Validate (optional)
```bash
az deployment group validate \
  --resource-group rg-cloudfolio \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.bicepparam
```
4. Deploy
```bash
az deployment group create \
  --resource-group rg-cloudfolio \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/main.bicepparam \
  --name bicep-deploy
```
- To enable Function App private endpoints:
  - CLI: add `--parameters deployFunctionAppPrivateEndpoints=true` to the `create`/`what-if` command
  - Or update `main.bicepparam` with `param deployFunctionAppPrivateEndpoints = true`

---

## 6) Post‑deploy validation & tests ✅

A. Fetch outputs
```bash
az deployment group show -g rg-cloudfolio -n bicep-deploy --query properties.outputs -o json
```

B. Networking checks
- VNet + subnets:
```bash
az network vnet show -g rg-cloudfolio -n <vnetName> -o table
az network vnet subnet list -g rg-cloudfolio --vnet-name <vnetName> -o table
```
- Ensure `snet-private-endpoints` has `privateEndpointNetworkPolicies: Disabled`.

C. Private DNS checks
```bash
az network private-dns zone list -g rg-cloudfolio -o table
az network private-dns link vnet list -g rg-cloudfolio --zone-name "<zoneName>" -o table
```

D. Storage checks
```bash
az storage account show -g rg-cloudfolio -n <storageAccountName> -o json
az storage account network-rule list -n <storageAccountName> -g rg-cloudfolio
az network private-endpoint list -g rg-cloudfolio -o table
```
- Confirm `publicNetworkAccess` = Disabled and `networkAcls.defaultAction` = Deny.

E. UAMI + RBAC checks
```bash
az identity show -g rg-cloudfolio -n <uamiName> -o json
az role assignment list --scope $(az storage account show -n <storageAccountName> -g rg-cloudfolio --query id -o tsv) -o table
```
- Confirm role assignments for UAMI.

F. Function App checks
```bash
az webapp show -n <functionAppName> -g rg-cloudfolio -o json
```
- Confirm:
  - `identity.userAssignedIdentities` has UAMI
  - `properties.publicNetworkAccess` == "Disabled"
  - `properties.virtualNetworkSubnetId` = functions subnet ID
  - AppSettings include `APPINSIGHTS_CONNECTIONSTRING` and storage/MSI entries

G. DNS & connectivity (from a test VM in the same VNet)
1. Launch a test VM into the VNet/subnet (or use a jump box/bastion):
```bash
az vm create -n testvm -g rg-cloudfolio --image UbuntuLTS --vnet-name <vnetName> --subnet <subnetName> --admin-username azureuser --generate-ssh-keys
```
2. From VM:
```bash
nslookup <storageAccountName>.blob.<storageSuffix>
nslookup <functionAppName>.privatelink.azurewebsites.net
curl -v https://<functionAppName>.privatelink.azurewebsites.net/health
```
- Expected: Private IPs returned and HTTPS calls reach function (if PEs deployed). If PEs not deployed, function will be unreachable by private hostnames (that's expected if you set `deployFunctionAppPrivateEndpoints=false`).

H. Function App integration tests (repo)
- Local dev / unit tests (use included project `api/v0.2.0`):
```bash
cd api/v0.2.0/api-gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/
```
- E2E tests (if infrastructure deployed): set environment variables for the tests (storage account, function hostnames) and run integration tests under `tests/integration`.

---

## 7) Using `api/v0.2.0` for integration tests 🔁
- Local development: `ensure-azurite.sh` & `setup-dev.sh` are included to run against Azurite/storage emulator and to run functions locally. Use these to test Function behavior before deploying infra.
- End-to-end (deployed infra):
  - Update test config with real hostnames: Function private hostnames will be `<appName>.privatelink.azurewebsites.net` (if PEs deployed).
  - Use a test VM in VNet to run curl/pytest against private hostnames.

---

## 8) Troubleshooting 🛠️
- DNS resolves to public IP / not resolving to private IP:
  - Confirm private DNS zone exists and VNet link is `Completed`.
  - Ensure the VM uses Azure DNS (no custom DNS servers).
- PE NIC not assigned IP:
  - Check `az network nic list -g rg-cloudfolio --query ...` for PE NICs.
  - Validate the PE subnet has `privateEndpointNetworkPolicies` = Disabled.
- UAMI still denied access:
  - Role assignments may take a short time to propagate (wait a few minutes).
  - Confirm `principalId` matches UAMI `properties.principalId`.

---

## 9) Security & Azure best practices 🔒
- Use `az deployment group what-if` in CI to gate infra changes.
- Least privilege RBAC: assign minimal roles (scope at storage account).
- Use Key Vault + Managed Identity for any secrets. Avoid connection strings in appSettings.
- Diagnostic settings: forward logs to Log Analytics (enable Diagnostic Settings for storage & function apps).
- Use Azure Policy to enforce:
  - Storage public access disabled
  - Private endpoints required for storage & apps (optional)
- Ensure service principals and CI have only required permissions.

---

## 10) Quick checklist for PR / Deploy ✔️
- [ ] Add/Update `main.bicepparam` for environment-specific values
- [ ] Run `az bicep build` locally
- [ ] Run `az deployment group what-if` and review changes
- [ ] Confirm role assignments and network choices
- [ ] Deploy to non-prod RG first and run integration checks from a VM in VNet
- [ ] When satisfied, enable `deployFunctionAppPrivateEndpoints=true` for prod (if needed), run `what-if` again, then deploy

---

If you'd like, I can:
- Add a Bicep test VM module to the repository that can be deployed (and destroyed) for DNS/PE checks, or  
- Add a GitHub Action workflow to run `bicep build` + `az deployment group what-if` on PRs.  

Reply with "test VM" or "CI workflow" (or "both") and I’ll scaffold it.