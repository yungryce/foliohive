targetScope = 'resourceGroup'

type Tags = {
  *: string
}

param location string
param tags Tags
param namePrefix string
param uniqueSuffix string

param privateEndpointsSubnetId string
param privateDnsZoneBlobId string
param privateDnsZoneQueueId string
param privateDnsZoneTableId string

@description('Principal ID of the user-assigned managed identity that should access storage')
param uamiPrincipalId string

var storageAccountName = take('cfsa${uniqueSuffix}', 24)
var blobPeName = '${namePrefix}-pe-blob-${uniqueSuffix}'
var queuePeName = '${namePrefix}-pe-queue-${uniqueSuffix}'
var tablePeName = '${namePrefix}-pe-table-${uniqueSuffix}'

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  tags: tags
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    dnsEndpointType: 'Standard'
    minimumTlsVersion: 'TLS1_2'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
    resource blobServices 'blobServices' = {
    name: 'default'
    properties: {
      deleteRetentionPolicy: {}
    }
    resource storageContainerDeployment 'containers' = {
      name: 'function-deployments'
      properties: {
        publicAccess: 'None'
      }
    }
  }
}

resource peBlob 'Microsoft.Network/privateEndpoints@2024-10-01' = {
  name: blobPeName
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointsSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'storage-blob'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [ 'blob' ]
        }
      }
    ]
  }
}

resource peBlobDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = {
  name: 'default'
  parent: peBlob
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: privateDnsZoneBlobId
        }
      }
    ]
  }
}

resource peQueue 'Microsoft.Network/privateEndpoints@2024-10-01' = {
  name: queuePeName
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointsSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'storage-queue'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [ 'queue' ]
        }
      }
    ]
  }
}

resource peQueueDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = {
  name: 'default'
  parent: peQueue
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'queue'
        properties: {
          privateDnsZoneId: privateDnsZoneQueueId
        }
      }
    ]
  }
}

resource peTable 'Microsoft.Network/privateEndpoints@2024-10-01' = {
  name: tablePeName
  location: location
  tags: tags
  properties: {
    subnet: { id: privateEndpointsSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'storage-table'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [ 'table' ]
        }
      }
    ]
  }
}

resource peTableDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = {
  name: 'default'
  parent: peTable
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'table'
        properties: {
          privateDnsZoneId: privateDnsZoneTableId
        }
      }
    ]
  }
}

var roleStorageAccountContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '17d1049b-9a84-46fb-8f53-869881c3d3ab')
var roleBlobDataOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
var roleBlobDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var roleQueueDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
var roleTableDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')

resource raStorageAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, uamiPrincipalId, roleStorageAccountContributor)
  scope: storage
  properties: {
    roleDefinitionId: roleStorageAccountContributor
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource raBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, uamiPrincipalId, roleBlobDataOwner)
  scope: storage
  properties: {
    roleDefinitionId: roleBlobDataOwner
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource raBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, uamiPrincipalId, roleBlobDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: roleBlobDataContributor
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource raQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, uamiPrincipalId, roleQueueDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: roleQueueDataContributor
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource raTable 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, uamiPrincipalId, roleTableDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: roleTableDataContributor
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output storageAccountName string = storage.name
output storageAccountId string = storage.id
