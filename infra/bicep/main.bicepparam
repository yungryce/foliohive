using './main.bicep'

param location = 'westus2'
param namePrefix = 'cloudfolioASP'

param tags = {
  Environment: 'v0.2.0'
  ManagedBy: 'Bicep'
  Purpose: 'App Services and Function Apps with Private Endpoints'
}

