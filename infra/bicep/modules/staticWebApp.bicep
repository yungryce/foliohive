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

@description('Resource ID of the api-gateway Function App')
param apiGatewayId string

@description('Default hostname of the api-gateway Function App, used to configure SWA API_BASE_URL')
param apiGatewayDefaultHostname string

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
    buildProperties: {
      appLocation: 'ui'  // Adjust to your SPA source folder (e.g., 'src', 'dist', 'ui')
      apiLocation: ''  // Not used; API is external (api-gateway)
      outputLocation: 'dist/browser'
      skipGithubActionWorkflowGeneration: true  // Use custom workflow or manual deploy
    }
  }
}

// Link the Function App Api Gateway to SWA
resource linkedBackend 'Microsoft.Web/staticSites/linkedBackends@2024-11-01' = if (enableLinkedBackend) {
  name: 'api-gateway'
  parent: staticWebApp
  properties: {
    backendResourceId: apiGatewayId
    region: location
  }
}

resource staticWebAppConfig 'Microsoft.Web/staticSites/config@2024-11-01' = if (!empty(apiGatewayDefaultHostname)) {
  parent: staticWebApp
  name: 'appsettings'
  properties: {
    API_BASE_URL: 'https://${apiGatewayDefaultHostname}/api'
  }
}

output staticWebAppUrl string = staticWebApp.properties.defaultHostname
output staticWebAppId string = staticWebApp.id
output staticWebAppName string = staticWebApp.name

