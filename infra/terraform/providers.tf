terraform {
  required_version = ">=1.0"
  
  required_providers {
    azapi = {
      source  = "azure/azapi"
      version = "~>2.5.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~>4.53.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~>3.5.0"
    }
  }
}
provider "azurerm" {
  features {}

  subscription_id = "105a58e6-5ddb-4fca-a952-fc0f81314fdc"
}