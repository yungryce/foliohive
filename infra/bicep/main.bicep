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

@description('Deploy shared Elastic Premium plan for legacy Function Apps')
param deployPremiumPlan bool = true

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

// App Service Plan (shared by all Function Apps)
var appServicePlanName = '${namePrefix}-plan-${uniqueSuffix}'

resource appServicePlan 'Microsoft.Web/serverfarms@2024-04-01' = if (deployPremiumPlan) {
  name: appServicePlanName
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

// Core outputs for downstream deployments
output uamiId string = identity.outputs.uamiId
output uamiClientId string = identity.outputs.uamiClientId
output uamiPrincipalId string = identity.outputs.uamiPrincipalId

output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output logAnalyticsWorkspaceId string = monitoring.outputs.logAnalyticsWorkspaceId
output logAnalyticsWorkspaceKey string = monitoring.outputs.logAnalyticsWorkspaceKey

output vnetId string = network.outputs.vnetId
output functionsSubnetId string = network.outputs.functionsSubnetId
output privateEndpointsSubnetId string = network.outputs.privateEndpointsSubnetId

output privateDnsZoneBlobId string = privateDns.outputs.privateDnsZoneBlobId
output privateDnsZoneQueueId string = privateDns.outputs.privateDnsZoneQueueId
output privateDnsZoneTableId string = privateDns.outputs.privateDnsZoneTableId
output privateDnsZoneAzureWebsitesId string = privateDns.outputs.privateDnsZoneAzureWebsitesId

output storageAccountName string = storage.outputs.storageAccountName
output storageAccountId string = storage.outputs.storageAccountId

output appServicePlanId string = deployPremiumPlan ? appServicePlan.id : ''
output appServicePlanName string = deployPremiumPlan ? appServicePlan.name : ''
output namePrefixOut string = namePrefix
output uniqueSuffixOut string = uniqueSuffix
