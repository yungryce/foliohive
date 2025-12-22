targetScope = 'resourceGroup'

@description('Azure region for resources')
param location string = 'westus2'

type Tags = {
  *: string
}

@description('Whether to deploy private endpoints for Function Apps')
param deployFunctionAppPrivateEndpoints bool = false  // Default to false to skip deployment

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

@description('Container image URI for training worker (e.g., myregistry.azurecr.io/training-worker:latest)')
param trainingWorkerImageUri string = ''

@description('Whether to deploy the training worker container instance')
param deployTrainingWorker bool = false

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

module functionApps './modules/functionApps.bicep' = {
  name: 'functionApps'
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
    deployPrivateEndpoints: deployFunctionAppPrivateEndpoints  // Pass the control param
    privateDnsZoneAzureWebsitesId: privateDns.outputs.privateDnsZoneAzureWebsitesId
  }
}

module staticWebApp './modules/staticWebApp.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    apiGatewayId: functionApps.outputs.apiGatewayId
    apiGatewayDefaultHostname: functionApps.outputs.apiGatewayDefaultHostname
  }
}

module containerInstance './modules/containerInstance.bicep' = if (deployTrainingWorker && !empty(trainingWorkerImageUri)) {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    uamiPrincipalId: identity.outputs.uamiPrincipalId
    containerImageUri: trainingWorkerImageUri
    storageAccountName: storage.outputs.storageAccountName
    uamiId: identity.outputs.uamiId
    uamiClientId: identity.outputs.uamiClientId
    cpuCores: '2.0'
    memoryGb: '4.0'
    restartPolicy: 'OnFailure'
    trainingMode: 'serverless'
    queueName: 'model-training'
    blobContainerName: 'github-cache'
  }
}

output storageAccountName string = storage.outputs.storageAccountName
output storageAccountId string = storage.outputs.storageAccountId
output functionAppNames array = functionApps.outputs.functionAppNames
output staticWebAppUrl string = staticWebApp.outputs.staticWebAppUrl
output containerInstanceId string = deployTrainingWorker ? containerInstance.outputs.containerInstanceId : ''
output containerInstanceName string = deployTrainingWorker ? containerInstance.outputs.containerInstanceName : ''
