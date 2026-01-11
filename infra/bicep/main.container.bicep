targetScope = 'resourceGroup'

type Tags = {
  *: string
}

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags Tags = {}

@description('Prefix used for resource naming')
param namePrefix string = 'cloudfolio'

@description('Unique suffix to keep resource names stable across deployments')
param uniqueSuffix string = uniqueString(resourceGroup().id, namePrefix)

@description('Container image URI (e.g., myregistry.azurecr.io/training-worker:latest)')
param containerImageUri string

@description('Principal ID of the user-assigned managed identity that should access storage')
param uamiPrincipalId string

@description('User-assigned managed identity resource ID')
param uamiId string

@description('User-assigned managed identity client ID')
param uamiClientId string

@description('Storage account name for queue and blob access')
param storageAccountName string

@description('Log Analytics workspace ID for container logs')
param logAnalyticsWorkspaceId string

@description('CPU cores for container instance (0.5 to 4.0)')
param cpuCores string = '2.0'

@description('Memory in GB for container instance (1 to 16)')
param memoryGb string = '4.0'

@description('Restart policy for container instance')
param restartPolicy string = 'OnFailure'

@description('Training mode: "serverless" exits after processing one batch; "continuous" polls indefinitely')
param trainingMode string = 'serverless'

@description('Queue name for training jobs')
param queueName string = 'model-training'

@description('Container name for blob storage (model artifacts and metadata)')
param blobContainerName string = 'github-cache'

@description('Resource ID of the container registry for RBAC role assignments')
param containerRegistryId string = ''

module containerInstance './modules/containerInstance.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    uamiPrincipalId: uamiPrincipalId
    containerImageUri: containerImageUri
    storageAccountName: storageAccountName
    uamiId: uamiId
    uamiClientId: uamiClientId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceId
    cpuCores: cpuCores
    memoryGb: memoryGb
    restartPolicy: restartPolicy
    trainingMode: trainingMode
    queueName: queueName
    blobContainerName: blobContainerName
    containerRegistryId: containerRegistryId
  }
}

output containerInstanceId string = containerInstance.outputs.containerInstanceId
output containerInstanceName string = containerInstance.outputs.containerInstanceName
