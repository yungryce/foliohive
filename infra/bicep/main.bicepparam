using './main.bicep'

param location = 'westus2'
param namePrefix = 'cloudfolio'

param tags = {
  Environment: 'v0.2.0'
  ManagedBy: 'Bicep'
  Purpose: 'AKS-Cluster'
}
