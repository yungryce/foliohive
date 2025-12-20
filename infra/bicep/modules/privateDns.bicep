targetScope = 'resourceGroup'

type Tags = {
  *: string
}

param tags Tags
param namePrefix string
param uniqueSuffix string
param vnetId string
param deployPrivateEndpoints bool = false  // Matches the param from main.bicep

var linkNameSuffix = '${namePrefix}-vnetlink-${uniqueSuffix}'
var storageDnsSuffix = environment().suffixes.storage

resource privateDnsBlob 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${storageDnsSuffix}'
  location: 'global'
  tags: tags
}

resource privateDnsQueue 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.queue.${storageDnsSuffix}'
  location: 'global'
  tags: tags
}

resource privateDnsTable 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.table.${storageDnsSuffix}'
  location: 'global'
  tags: tags
}

resource privateDnsAzureWebsites 'Microsoft.Network/privateDnsZones@2024-06-01' = if (deployPrivateEndpoints) {
  name: 'privatelink.azurewebsites.net'
  location: 'global'
  tags: tags
}

resource blobLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  name: '${linkNameSuffix}-blob'
  parent: privateDnsBlob
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource queueLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  name: '${linkNameSuffix}-queue'
  parent: privateDnsQueue
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource tableLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  name: '${linkNameSuffix}-table'
  parent: privateDnsTable
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource azureWebsitesLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (deployPrivateEndpoints) {
  name: '${linkNameSuffix}-azurewebsites'
  parent: privateDnsAzureWebsites
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

output privateDnsZoneBlobId string = privateDnsBlob.id
output privateDnsZoneQueueId string = privateDnsQueue.id
output privateDnsZoneTableId string = privateDnsTable.id
output privateDnsZoneAzureWebsitesId string = privateDnsAzureWebsites.id
