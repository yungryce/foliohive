targetScope = 'resourceGroup'

type Tags = {
  *: string
}

param location string
param tags Tags
param namePrefix string
param uniqueSuffix string

@description('Whether to deploy SWA linked backend (Azure Functions integration). If false, SWA is standalone.')
param enableLinkedBackend bool = true

@description('Resource ID of the backend Function App')
param backendFunctionAppId string

@description('Default hostname of the backend Function App, used to configure SWA API_BASE_URL')
param backendFunctionAppDefaultHostname string

@description('GitHub repository URL for the Static Web App (required by Azure, even for manual deployments)')
param repositoryUrl string = 'https://dev.azure.com/chxgbx/cloudfolio/_git/cloudfolio'

var swaName = '${namePrefix}-swa-${uniqueSuffix}'

resource staticWebApp 'Microsoft.Web/staticSites@2024-11-01' = {
  name: swaName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    repositoryUrl: repositoryUrl
    branch: 'main'
    provider: 'AzureDevOps'
    buildProperties: {
      appLocation: 'ui'
      apiLocation: ''
      outputLocation: 'dist/browser'
      skipGithubActionWorkflowGeneration: true 
    }
  }
}

// Link the Function App backend to SWA
resource linkedBackend 'Microsoft.Web/staticSites/linkedBackends@2024-11-01' = if (enableLinkedBackend) {
  name: 'backend'
  parent: staticWebApp
  properties: {
    backendResourceId: backendFunctionAppId
    region: location
  }
}

resource staticWebAppConfig 'Microsoft.Web/staticSites/config@2024-11-01' = if (!empty(backendFunctionAppDefaultHostname)) {
  parent: staticWebApp
  name: 'appsettings'
  properties: {
    API_BASE_URL: 'https://${backendFunctionAppDefaultHostname}/api'
  }
}

output staticWebAppUrl string = staticWebApp.properties.defaultHostname
output staticWebAppId string = staticWebApp.id
output staticWebAppName string = staticWebApp.name

