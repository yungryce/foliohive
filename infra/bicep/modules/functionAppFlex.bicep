targetScope = 'resourceGroup'

type Tags = {
  *: string
}

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags Tags = {}

@description('Function app name')
param functionAppName string

@description('Subnet ID for Function Apps VNet integration')
param functionsSubnetId string

@description('Storage account name for managed identity bindings')
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

@description('Maximum number of Flex instances allowed for this app')
@minValue(1)
@maxValue(1000)
param flexMaximumInstanceCount int = 100

@description('Memory per Flex instance in MB (512 MB increments)')
@minValue(512)
@maxValue(4096)
param flexInstanceMemoryMb int = 2048

@description('HTTP concurrency per instance for Flex Consumption')
@minValue(1)
@maxValue(100)
param httpPerInstanceConcurrency int = 20

@description('Always Ready instance count; set to >0 to keep warm workers available')
@minValue(0)
@maxValue(10)
param flexAlwaysReadyInstanceCount int = 0

@description('Public network access mode for the function app')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Enabled'

var baseAppSettings = [
  {
    name: 'FUNCTIONS_EXTENSION_VERSION'
    value: '~4'
  }
  {
    name: 'FUNCTIONS_WORKER_RUNTIME'
    value: 'python'
  }
  {
    name: 'WEBSITE_RUN_FROM_PACKAGE'
    value: '1'
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsightsConnectionString
  }
  {
    name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING'
    value: 'ClientId=${uamiClientId};Authorization=AAD'
  }
  {
    name: 'AzureWebJobsStorage__accountName'
    value: storageAccountName
  }
  {
    name: 'AzureWebJobsStorage__credential'
    value: 'ManagedIdentity'
  }
  {
    name: 'AzureWebJobsStorage__clientId'
    value: uamiClientId
  }
  {
    name: 'AZURE_CLIENT_ID'
    value: uamiClientId
  }
  {
    name: 'AzureWebJobsStorage__blobServiceUri'
    value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}/'
  }
  {
    name: 'AzureWebJobsStorage__queueServiceUri'
    value: 'https://${storageAccountName}.queue.${environment().suffixes.storage}/'
  }
  {
    name: 'AzureWebJobsStorage__tableServiceUri'
    value: 'https://${storageAccountName}.table.${environment().suffixes.storage}/'
  }
  {
    name: 'WEBSITE_VNET_ROUTE_ALL'
    value: '1'
  }
]

var alwaysReadyConfig = flexAlwaysReadyInstanceCount > 0 ? [
  {
    name: 'global'
    instanceCount: flexAlwaysReadyInstanceCount
  }
] : []

resource flexPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: flexPlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'FlexConsumption'
    tier: 'FlexConsumption'
  }
}

resource functionApp 'Microsoft.Web/sites@2025-03-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiId}': {}
    }
  }
  properties: {
    serverFarmId: flexPlan.id
    httpsOnly: true
    publicNetworkAccess: publicNetworkAccess
    virtualNetworkSubnetId: functionsSubnetId
    functionAppConfig: {
      runtime: {
        name: 'python'
        version: '3.13'
      }
      scaleAndConcurrency: {
        instanceMemoryMB: flexInstanceMemoryMb
        maximumInstanceCount: flexMaximumInstanceCount
        triggers: {
          http: {
            perInstanceConcurrency: httpPerInstanceConcurrency
          }
        }
        alwaysReady: alwaysReadyConfig
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      linuxFxVersion: 'Python|3.13'
      ftpsState: 'Disabled'
      appSettings: baseAppSettings
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-10-01' = if (deployPrivateEndpoint) {
  name: '${functionAppName}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'sites'
        properties: {
          privateLinkServiceId: functionApp.id
          groupIds: [
            'sites'
          ]
        }
      }
    ]
  }
}

resource privateEndpointDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = if (deployPrivateEndpoint) {
  name: 'default'
  parent: privateEndpoint
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'azurewebsites'
        properties: {
          privateDnsZoneId: privateDnsZoneAzureWebsitesId
        }
      }
    ]
  }
}

output functionAppId string = functionApp.id
output functionAppName string = functionApp.name
output functionAppDefaultHostname string = functionApp.properties.defaultHostName
output flexPlanId string = flexPlan.id
output flexPlanName string = flexPlan.name
