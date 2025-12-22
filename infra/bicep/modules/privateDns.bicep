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

var privateDnsZoneNames = [
  'privatelink.blob.${storageDnsSuffix}'
  'privatelink.queue.${storageDnsSuffix}'
  'privatelink.table.${storageDnsSuffix}'
]

resource privateDnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zoneName in privateDnsZoneNames: {
  name: zoneName
  location: 'global'
  tags: tags
}]

resource privateDnsZoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zoneName, i) in privateDnsZoneNames: {
  name: '${zoneName}/${linkNameSuffix}'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnetId }
    registrationEnabled: false
  }
  dependsOn: [
    privateDnsZones[i]
  ]
}]

resource privateDnsAzureWebsites 'Microsoft.Network/privateDnsZones@2024-06-01' = if (deployPrivateEndpoints) {
  name: 'privatelink.azurewebsites.net'
  location: 'global'
  tags: tags
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

output privateDnsZoneBlobId string = privateDnsZones[0].id
output privateDnsZoneQueueId string = privateDnsZones[1].id
output privateDnsZoneTableId string = privateDnsZones[2].id
output privateDnsZoneAzureWebsitesId string = privateDnsAzureWebsites.id
