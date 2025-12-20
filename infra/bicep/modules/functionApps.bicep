targetScope = 'resourceGroup'

type Tags = {
  *: string
}

param location string
param tags Tags
param namePrefix string
param uniqueSuffix string
param deployPrivateEndpoints bool = false  // Matches the param from main.bicep

param functionsSubnetId string
param privateEndpointsSubnetId string

param storageAccountName string
param uamiId string
param uamiClientId string

param appInsightsConnectionString string
param privateDnsZoneAzureWebsitesId string

var planName = '${namePrefix}-plan-${uniqueSuffix}'

var appApiGatewayName = '${namePrefix}-api-gateway-${uniqueSuffix}'
var appMergeWorkerName = '${namePrefix}-merge-worker-${uniqueSuffix}'
var appSyncWorkerName = '${namePrefix}-sync-worker-${uniqueSuffix}'

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'EP1'
    tier: 'ElasticPremium'
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

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
    value: 'https://${storageAccountName}.blob.core.windows.net/'
  }
  {
    name: 'AzureWebJobsStorage__queueServiceUri'
    value: 'https://${storageAccountName}.queue.core.windows.net/'
  }
  {
    name: 'AzureWebJobsStorage__tableServiceUri'
    value: 'https://${storageAccountName}.table.core.windows.net/'
  }
  {
    name: 'WEBSITE_VNET_ROUTE_ALL'
    value: '1'
  }
]

resource apiGateway 'Microsoft.Web/sites@2024-04-01' = {
  name: appApiGatewayName
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
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Disabled'
    virtualNetworkSubnetId: functionsSubnetId
    siteConfig: {
      minTlsVersion: '1.2'
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      vnetRouteAllEnabled: true
      appSettings: baseAppSettings
    }
  }
}

resource mergeWorker 'Microsoft.Web/sites@2024-04-01' = {
  name: appMergeWorkerName
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
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Disabled'
    virtualNetworkSubnetId: functionsSubnetId
    siteConfig: {
      linuxFxVersion: 'Python|3.13'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      vnetRouteAllEnabled: true
      appSettings: baseAppSettings
    }
  }
}

resource syncWorker 'Microsoft.Web/sites@2024-04-01' = {
  name: appSyncWorkerName
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
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Disabled'
    virtualNetworkSubnetId: functionsSubnetId
    siteConfig: {
      linuxFxVersion: 'Python|3.13'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      vnetRouteAllEnabled: true
      appSettings: baseAppSettings
    }
  }
}

resource peApiGateway 'Microsoft.Network/privateEndpoints@2024-10-01' = if (deployPrivateEndpoints) {
  name: '${appApiGatewayName}-pe'
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
          privateLinkServiceId: apiGateway.id
          groupIds: [
            'sites'
          ]
        }
      }
    ]
  }
}

resource peApiGatewayDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = if (deployPrivateEndpoints) {
  name: 'default'
  parent: peApiGateway
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

resource peMergeWorker 'Microsoft.Network/privateEndpoints@2024-10-01' = if (deployPrivateEndpoints) {
  name: '${appMergeWorkerName}-pe'
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
          privateLinkServiceId: mergeWorker.id
          groupIds: [
            'sites'
          ]
        }
      }
    ]
  }
}

resource peMergeWorkerDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = if (deployPrivateEndpoints) {
  name: 'default'
  parent: peMergeWorker
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

resource peSyncWorker 'Microsoft.Network/privateEndpoints@2024-10-01' = if (deployPrivateEndpoints) {
  name: '${appSyncWorkerName}-pe'
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
          privateLinkServiceId: syncWorker.id
          groupIds: [
            'sites'
          ]
        }
      }
    ]
  }
}

resource peSyncWorkerDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = if (deployPrivateEndpoints) {
  name: 'default'
  parent: peSyncWorker
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

output functionAppNames array = [
  apiGateway.name
  mergeWorker.name
  syncWorker.name
]
