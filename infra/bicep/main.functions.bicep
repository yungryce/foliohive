targetScope = 'resourceGroup'

type Tags = {
  *: string
}

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags Tags = {}

@description('Function App name to deploy')
param functionAppName string

@description('App Service Plan resource ID')
param appServicePlanId string

@description('Subnet ID for Function Apps VNet integration')
param functionsSubnetId string

@description('Whether to deploy private endpoints for Function Apps')
@allowed([
  'true'
  'false'
  'True'
  'False'
])
param deployPrivateEndpoint string = 'false'

var deployPrivateEndpointBool = toLower(deployPrivateEndpoint) == 'true'

@description('Subnet ID for Private Endpoints')
param privateEndpointsSubnetId string

@description('Storage account name used by the Function Apps')
param storageAccountName string

@description('User-assigned managed identity resource ID')
param uamiId string

@description('User-assigned managed identity client ID')
param uamiClientId string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Private DNS Zone ID for AzureWebSites (required when using private endpoints)')
param privateDnsZoneAzureWebsitesId string

module functionApps './modules/functionApps.bicep' = {
  name: 'functionApps'
  params: {
    location: location
    tags: tags
    functionAppName: functionAppName
    appServicePlanId: appServicePlanId
    functionsSubnetId: functionsSubnetId
    privateEndpointsSubnetId: privateEndpointsSubnetId
    storageAccountName: storageAccountName
    uamiId: uamiId
    uamiClientId: uamiClientId
    appInsightsConnectionString: appInsightsConnectionString
    deployPrivateEndpoint: deployPrivateEndpointBool
    privateDnsZoneAzureWebsitesId: privateDnsZoneAzureWebsitesId
  }
}

output functionAppId string = functionApps.outputs.functionAppId
output functionAppName string = functionApps.outputs.functionAppName
output functionAppDefaultHostname string = functionApps.outputs.functionAppDefaultHostname
