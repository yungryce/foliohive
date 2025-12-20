targetScope = 'resourceGroup'

type Tags = {
  *: string
}

param location string
param tags Tags
param namePrefix string
param uniqueSuffix string

param vnetAddressPrefix string
param functionsSubnetPrefix string
param privateEndpointsSubnetPrefix string

var vnetName = '${namePrefix}-vnet-${uniqueSuffix}'

resource vnet 'Microsoft.Network/virtualNetworks@2024-03-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
  }
}

resource functionsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-03-01' = {
  name: 'snet-functions'
  parent: vnet
  properties: {
    addressPrefix: functionsSubnetPrefix
    delegations: [
      {
        name: 'delegation-appservice'
        properties: {
          serviceName: 'Microsoft.Web/serverFarms'
        }
      }
    ]
  }
}

resource privateEndpointsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-03-01' = {
  name: 'snet-private-endpoints'
  parent: vnet
  properties: {
    addressPrefix: privateEndpointsSubnetPrefix
    privateEndpointNetworkPolicies: 'Disabled'
  }
}

output vnetId string = vnet.id
output functionsSubnetId string = functionsSubnet.id
output privateEndpointsSubnetId string = privateEndpointsSubnet.id
