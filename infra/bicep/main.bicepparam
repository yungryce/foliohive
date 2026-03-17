using './main.bicep'

param location = 'westus2'
param namePrefix = 'foliohiveASP'

param tags = {
  Environment: 'v0.2.0'
  ManagedBy: 'Bicep'
  Purpose: 'App Services and Function Apps with Private Endpoints'
}

// CI/CD typically overrides these at deploy time

