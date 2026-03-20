targetScope = 'resourceGroup'

type Tags = {
  *: string
}

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags Tags = {}

@description('Function app name')
param functionAppName string

@description('Subnet ID for Function Apps VNet integration')
param functionsSubnetId string

@description('Storage account name for managed identity bindings')
param storageAccountName string

@description('User-assigned managed identity resource ID')
param uamiId string

@description('User-assigned managed identity client ID')
param uamiClientId string

@description('Application Insights resource name for connection string resolution')
param appInsightsName string

@description('Log Analytics Workspace resource ID for diagnostics')
param logAnalyticsWorkspaceId string = ''

@description('Additional CORS origins (e.g. static web app hostname)')
param corsAllowedOrigins array = []

@description('Name of the Flex Consumption plan created for this app')
param flexPlanName string = '${functionAppName}-flex-plan'

@description('Maximum number of Flex instances allowed for this app')
@minValue(1)
@maxValue(1000)
param flexMaximumInstanceCount int = 100

@description('Memory per Flex instance in MB (512 MB increments)')
@minValue(512)
@maxValue(4096)
param flexInstanceMemoryMb int = 2048

@description('HTTP concurrency per instance for Flex Consumption')
@minValue(1)
@maxValue(100)
param httpPerInstanceConcurrency int = 20

@description('Always Ready instance count; set to >0 to keep warm workers available')
@minValue(0)
@maxValue(10)
param flexAlwaysReadyInstanceCount int = 0

resource appInsightsRef 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource flexPlan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: flexPlanName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  sku: {
    // Flex Consumption SKU
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    maximumElasticWorkerCount: flexMaximumInstanceCount
    perSiteScaling: false
    reserved: true
    targetWorkerCount: flexAlwaysReadyInstanceCount
  }
}

resource functionApp 'Microsoft.Web/sites@2025-03-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiId}': {}
    }
  }
  properties: {
    serverFarmId: flexPlan.id
    httpsOnly: true
    outboundVnetRouting: {
      applicationTraffic: true
    }

    siteConfig: {
      alwaysOn: false
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      scmType: 'None'

      cors: {
        allowedOrigins: corsAllowedOrigins
        supportCredentials: false
      }
    }

    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}/function-deployments'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: uamiId
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.13'
      }
      scaleAndConcurrency: {
        instanceMemoryMB: flexInstanceMemoryMb
        maximumInstanceCount: flexMaximumInstanceCount
        triggers: {
          http: {
            perInstanceConcurrency: httpPerInstanceConcurrency
          }
        }
      }
    }
  }
}

resource functionAppVnetIntegration 'Microsoft.Web/sites/networkConfig@2024-11-01' = {
  parent: functionApp
  name: 'virtualNetwork'
  dependsOn: [
    functionAppAppSettings
  ]
  properties: {
    subnetResourceId: functionsSubnetId
    swiftSupported: true
  }
}

resource functionAppDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsWorkspaceId)) {
  scope: functionApp
  name: 'functionapp-diagnostics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      { category: 'FunctionAppLogs', enabled: true }
      { category: 'AppServicePlatformLogs', enabled: true }
      { category: 'AppServiceHTTPLogs', enabled: true }
      { category: 'AppServiceConsoleLogs', enabled: true }
      { category: 'AppServiceAuthenticationLogs', enabled: true }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

resource functionAppAppSettings 'Microsoft.Web/sites/config@2024-11-01' = {
  parent: functionApp
  name: 'appsettings'
  properties: {
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsRef.properties.ConnectionString
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'ClientId=${uamiClientId};Authorization=AAD'
    APPINSIGHTS_INSTRUMENTATIONKEY: appInsightsRef.properties.InstrumentationKey
    APPLICATIONINSIGHTS_ROLE_NAME: functionAppName
    AzureWebJobsStorage__credential: 'managedidentity'
    AzureWebJobsStorage__blobServiceUri: 'https://${storageAccountName}.blob.${environment().suffixes.storage}'
    AzureWebJobsStorage__queueServiceUri: 'https://${storageAccountName}.queue.${environment().suffixes.storage}'
    AzureWebJobsStorage__tableServiceUri: 'https://${storageAccountName}.table.${environment().suffixes.storage}'
    AzureWebJobsStorage__ClientId: uamiClientId
    AzureWebJobsStorage__accountName: storageAccountName
    AZURE_CLIENT_ID: uamiClientId
    ENABLE_CONFIG_DISCOVERY_GRAPHQL: 'true'
  }
}

output functionAppId string = functionApp.id
output functionAppName string = functionApp.name
output functionAppDefaultHostname string = functionApp.properties.defaultHostName
output flexPlanId string = flexPlan.id
output flexPlanName string = flexPlan.name
