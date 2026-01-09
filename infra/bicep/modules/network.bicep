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

resource vnet 'Microsoft.Network/virtualNetworks@2024-07-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [ vnetAddressPrefix ]
    }

    subnets: [
      {
        name: 'snet-functions'
        properties: {
          addressPrefix: functionsSubnetPrefix
          delegations: [
            {
              name: 'functions-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: privateEndpointsSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

var functionsSubnet = vnet.properties.subnets[0]
var privateEndpointsSubnet = vnet.properties.subnets[1]

output vnetId string = vnet.id
output functionsSubnetId string = functionsSubnet.id
output privateEndpointsSubnetId string = privateEndpointsSubnet.id
