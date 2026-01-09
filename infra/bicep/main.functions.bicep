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

@description('Subnet ID for Function Apps VNet integration')
param functionsSubnetId string

@description('Storage account name used by the Function App')
param storageAccountName string

@description('User-assigned managed identity resource ID')
param uamiId string

@description('User-assigned managed identity client ID')
param uamiClientId string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Whether to deploy a private endpoint for this function app')
param deployPrivateEndpoint bool = false

@description('Subnet ID for Private Endpoints (required if deployPrivateEndpoint is true)')
param privateEndpointsSubnetId string = ''

@description('Private DNS Zone ID for AzureWebSites (required if deployPrivateEndpoint is true)')
param privateDnsZoneAzureWebsitesId string = ''

@description('Name of the Flex Consumption plan created for this app')
param flexPlanName string = '${functionAppName}-flex-plan'

@description('Maximum Flex Consumption instances for this app')
@minValue(1)
@maxValue(1000)
param flexMaximumInstanceCount int = 100

@description('Flex Consumption memory allocation per instance in MB')
@minValue(512)
@maxValue(4096)
param flexInstanceMemoryMb int = 2048

@description('HTTP concurrency per Flex instance')
@minValue(1)
@maxValue(100)
param httpPerInstanceConcurrency int = 20

@description('Always Ready instance count; set to >0 to keep warm instances online')
@minValue(0)
@maxValue(10)
param flexAlwaysReadyInstanceCount int = 0

@description('Public network access mode for the function app')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Enabled'

module functionApp './modules/functionAppFlex.bicep' = {
  name: 'functionAppFlex'
  params: {
    location: location
    tags: tags
    functionAppName: functionAppName
    functionsSubnetId: functionsSubnetId
    storageAccountName: storageAccountName
    uamiId: uamiId
    uamiClientId: uamiClientId
    appInsightsConnectionString: appInsightsConnectionString
    deployPrivateEndpoint: deployPrivateEndpoint
    privateEndpointsSubnetId: privateEndpointsSubnetId
    privateDnsZoneAzureWebsitesId: privateDnsZoneAzureWebsitesId
    flexPlanName: flexPlanName
    flexMaximumInstanceCount: flexMaximumInstanceCount
    flexInstanceMemoryMb: flexInstanceMemoryMb
    httpPerInstanceConcurrency: httpPerInstanceConcurrency
    flexAlwaysReadyInstanceCount: flexAlwaysReadyInstanceCount
    publicNetworkAccess: publicNetworkAccess
  }
}

output functionAppId string = functionApp.outputs.functionAppId
output functionAppName string = functionApp.outputs.functionAppName
output functionAppDefaultHostname string = functionApp.outputs.functionAppDefaultHostname
output flexPlanId string = functionApp.outputs.flexPlanId
output flexPlanName string = functionApp.outputs.flexPlanName
