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

@description('App Service Plan ID')
param appServicePlanId string

@description('Subnet ID for Function Apps VNet integration')
param functionsSubnetId string

@description('Storage account name')
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
]

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
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
    serverFarmId: appServicePlanId
    httpsOnly: true
    publicNetworkAccess: 'Disabled'
    virtualNetworkSubnetId: functionsSubnetId
    siteConfig: {
      minTlsVersion: '1.2'
      linuxFxVersion: 'Python|3.13'
      ftpsState: 'Disabled'
      vnetRouteAllEnabled: true
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
