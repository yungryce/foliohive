targetScope = 'resourceGroup'

type Tags = {
  *: string
}

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags Tags = {}

@description('Prefix used for resource naming')
param namePrefix string = 'foliohive'

@description('Unique suffix to keep resource names stable across deployments')
param uniqueSuffix string = uniqueString(resourceGroup().id, namePrefix)

@description('GitHub repository URL for the Static Web App (required by Azure, even for manual deployments)')
param repositoryUrl string = 'https://dev.azure.com/chxgbx/foliohive/_git/foliohive'

@description('Resource ID of the backend Function App (optional when SWA is standalone)')
param backendFunctionAppId string = ''

@description('Default hostname of the backend Function App (optional when SWA is standalone)')
param backendFunctionAppDefaultHostname string = ''

@description('Whether to link SWA to the Function backend')
param enableLinkedBackend bool = false

module staticWebApp './modules/staticWebApp.bicep' = {
  params: {
    location: location
    tags: tags
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    backendFunctionAppId: backendFunctionAppId
    backendFunctionAppDefaultHostname: backendFunctionAppDefaultHostname
    enableLinkedBackend: enableLinkedBackend
    repositoryUrl: repositoryUrl
  }
}

output staticWebAppUrl string = staticWebApp.outputs.staticWebAppUrl
output staticWebAppId string = staticWebApp.outputs.staticWebAppId
output staticWebAppName string = staticWebApp.outputs.staticWebAppName
