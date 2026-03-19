targetScope = 'resourceGroup'

@description('Azure region for resources')
param location string = 'westus2'

type Tags = {
  *: string
}

@description('Tags applied to all resources')
param tags Tags = {}

@description('Prefix used for resource naming')
param namePrefix string = 'foliohive'

@description('Virtual network address space')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Subnet CIDR for Function Apps VNet integration')
param functionsSubnetPrefix string = '10.20.1.0/24'

@description('Subnet CIDR for Private Endpoints')
param privateEndpointsSubnetPrefix string = '10.20.2.0/24'

var uniqueSuffix = uniqueString(resourceGroup().id, namePrefix)

module identity './modules/identity.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
  }
}

module network './modules/network.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    vnetAddressPrefix: vnetAddressPrefix
    functionsSubnetPrefix: functionsSubnetPrefix
    privateEndpointsSubnetPrefix: privateEndpointsSubnetPrefix
  }
}

module privateDns './modules/privateDns.bicep' = {
  params: {
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    vnetId: network.outputs.vnetId
  }
}

module monitoring './modules/monitoring.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    uamiPrincipalId: identity.outputs.uamiPrincipalId
  }
}

module storage './modules/storage.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    privateEndpointsSubnetId: network.outputs.privateEndpointsSubnetId
    privateDnsZoneBlobId: privateDns.outputs.privateDnsZoneBlobId
    privateDnsZoneQueueId: privateDns.outputs.privateDnsZoneQueueId
    privateDnsZoneTableId: privateDns.outputs.privateDnsZoneTableId
    uamiPrincipalId: identity.outputs.uamiPrincipalId
  }
}


// Core outputs for downstream deployments
output uamiId string = identity.outputs.uamiId
output uamiClientId string = identity.outputs.uamiClientId
output uamiPrincipalId string = identity.outputs.uamiPrincipalId

output appInsightsName string = monitoring.outputs.monitoring.appInsightsName
output logAnalyticsWorkspaceId string = monitoring.outputs.logAnalyticsWorkspaceId
output vnetId string = network.outputs.vnetId
output functionsSubnetId string = network.outputs.functionsSubnetId
output privateEndpointsSubnetId string = network.outputs.privateEndpointsSubnetId

output privateDnsZoneBlobId string = privateDns.outputs.privateDnsZoneBlobId
output privateDnsZoneQueueId string = privateDns.outputs.privateDnsZoneQueueId
output privateDnsZoneTableId string = privateDns.outputs.privateDnsZoneTableId

output storageAccountName string = storage.outputs.storageAccountName
output storageAccountId string = storage.outputs.storageAccountId

output namePrefixOut string = namePrefix
output uniqueSuffixOut string = uniqueSuffix
