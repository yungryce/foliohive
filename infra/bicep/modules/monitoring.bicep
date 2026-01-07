targetScope = 'resourceGroup'

type Tags = {
  *: string
}

param location string
param tags Tags
param namePrefix string
param uniqueSuffix string

@description('Principal ID of the user-assigned managed identity that should access app insights and log analytics')
param uamiPrincipalId string

var workspaceName = '${namePrefix}-law-${uniqueSuffix}'
var appInsightsName = '${namePrefix}-appi-${uniqueSuffix}'

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableLocalAuth: true
  }
}

var metricsPublisher = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')

resource raMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, appInsights.id, metricsPublisher)
  scope: workspace
  properties: {
    roleDefinitionId: metricsPublisher
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output appInsightsConnectionString string = appInsights.properties.ConnectionString
output logAnalyticsWorkspaceId string = workspace.id
output logAnalyticsWorkspaceKey string = workspace.listKeys().primarySharedKey
