using './main.bicep'

param location = 'westus2'
param namePrefix = 'cloudfolio'

param tags = {
  Environment: 'v0.0.1-prod'
  ManagedBy: 'Bicep'
  Purpose: 'AKS-Cluster'
}
