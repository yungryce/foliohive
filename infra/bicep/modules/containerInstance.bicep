targetScope = 'resourceGroup'

type Tags = {
  *: string
}

param location string
param tags Tags
param namePrefix string
param uniqueSuffix string

@description('Principal ID of the user-assigned managed identity that should access storage')
param uamiPrincipalId string

@description('Container image URI (e.g., myregistry.azurecr.io/training-worker:latest)')
param containerImageUri string

@description('Storage account name for queue and blob access')
param storageAccountName string

@description('User-Assigned Managed Identity ID')
param uamiId string

@description('User-Assigned Managed Identity Client ID')
param uamiClientId string

@description('Log Analytics workspace ID for container logs')
param logAnalyticsWorkspaceId string

var logAnalyticsWorkspaceKeys = listKeys(logAnalyticsWorkspaceId, '2021-12-01')

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

var containerInstanceName = '${namePrefix}-train-${uniqueSuffix}'

resource containerInstance 'Microsoft.ContainerInstance/containerGroups@2024-05-01-preview' = {
  name: containerInstanceName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiId}': {}
    }
  }
  properties: {
    diagnostics: {
      logAnalytics: {
        workspaceId: logAnalyticsWorkspaceId
        workspaceKey: logAnalyticsWorkspaceKeys.primarySharedKey
        logType: 'ContainerInstanceLogs'
      }
    }
    containers: [
      {
        name: 'training-worker'
        properties: {
          image: containerImageUri
          resources: {
            requests: {
              cpu: json(cpuCores)
              memoryInGB: json(memoryGb)
            }
          }
          environmentVariables: [
            {
              name: 'AZURE_CLIENT_ID'
              value: uamiClientId
            }
            {
              name: 'AZURE_TENANT_ID'
              value: subscription().tenantId
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT_NAME'
              value: storageAccountName
            }
            {
              name: 'QUEUE_NAME'
              value: queueName
            }
            {
              name: 'BLOB_CONTAINER_NAME'
              value: blobContainerName
            }
            {
              name: 'TRAINING_MODE'
              value: trainingMode
            }
            {
              name: 'LOG_LEVEL'
              value: 'INFO'
            }
          ]
        }
      }
    ]
    osType: 'Linux'
    restartPolicy: restartPolicy
    imageRegistryCredentials: []  // Use UAMI for ACR auth; override if needed
  }
}

var acrPullRoleId = '7f951dda-4355-4d97-8421-6a9a7ce869d1'

// Role assignment: AcrPull for pulling container image from registry
resource roleAssignmentAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerRegistryId)) {
  scope: resourceGroup()
  name: guid(uamiPrincipalId, containerRegistryId, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output containerInstanceId string = containerInstance.id
output containerInstanceName string = containerInstance.name
