targetScope = 'resourceGroup'

type Tags = {
  *: string
}

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags Tags = {}

@description('Prefix used for resource naming')
param namePrefix string = 'cloudfolio'

@description('Unique suffix to keep resource names stable across deployments')
param uniqueSuffix string = uniqueString(resourceGroup().id, namePrefix)

@description('Whether to deploy private endpoints for Function Apps')
param deployFunctionAppPrivateEndpoints bool = false

@description('Subnet ID for Function Apps VNet integration')
param functionsSubnetId string

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
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix

    functionsSubnetId: functionsSubnetId
    privateEndpointsSubnetId: privateEndpointsSubnetId

    storageAccountName: storageAccountName
    uamiId: uamiId
    uamiClientId: uamiClientId

    appInsightsConnectionString: appInsightsConnectionString
    deployPrivateEndpoints: deployFunctionAppPrivateEndpoints
    privateDnsZoneAzureWebsitesId: privateDnsZoneAzureWebsitesId
  }
}

output functionAppNames array = functionApps.outputs.functionAppNames
output apiGatewayName string = functionApps.outputs.apiGatewayName
output mergeWorkerName string = functionApps.outputs.mergeWorkerName
output syncWorkerName string = functionApps.outputs.syncWorkerName
output apiGatewayId string = functionApps.outputs.apiGatewayId
output apiGatewayDefaultHostname string = functionApps.outputs.apiGatewayDefaultHostname
