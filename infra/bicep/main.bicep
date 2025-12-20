targetScope = 'resourceGroup'

@description('Azure region for resources')
param location string = 'westus2'

type Tags = {
  *: string
}

@description('Tags applied to all resources')
param tags Tags = {}

@description('Prefix used for resource naming')
param namePrefix string = 'cloudfolio'

@description('Virtual network address space')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Subnet CIDR for Function Apps VNet integration')
param functionsSubnetPrefix string = '10.20.1.0/24'

@description('Subnet CIDR for Private Endpoints')
param privateEndpointsSubnetPrefix string = '10.20.2.0/24'

var uniqueSuffix = uniqueString(resourceGroup().id, namePrefix)

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

module identity './modules/identity.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
  }
}

module monitoring './modules/monitoring.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
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

module functionApps './modules/functionApps.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix

    functionsSubnetId: network.outputs.functionsSubnetId
    privateEndpointsSubnetId: network.outputs.privateEndpointsSubnetId

    storageAccountName: storage.outputs.storageAccountName
    uamiId: identity.outputs.uamiId
    uamiClientId: identity.outputs.uamiClientId

    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    privateDnsZoneAzureWebsitesId: privateDns.outputs.privateDnsZoneAzureWebsitesId
  }
}

output storageAccountName string = storage.outputs.storageAccountName
output functionAppNames array = functionApps.outputs.functionAppNames
