variable "location" {
  description = "Azure region for resources"
  type        = string
  default     = "westus2"
}

variable "resource_group_name" {
  description = "Name of the resource group"
  type        = string
  default     = "rg-cloudfolio"
}

variable "cluster_name" {
  description = "Name of the AKS cluster"
  type        = string
  default     = "cloudfolio-cluster"
}

variable "node_count" {
  description = "Initial number of nodes in the default node pool"
  type        = number
  default     = 2
}

variable "spot_node_count" {
  description = "Initial number of spot nodes"
  type        = number
  default     = 2
}

variable "vm_size" {
  description = "VM size for regular nodes"
  type        = string
  default     = "Standard_D2s_v5"
}

variable "spot_vm_size" {
  description = "VM size for spot nodes"
  type        = string
  default     = "Standard_D2s_v5"
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.33.5"
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default = {
    Environment = "v0.0.1-prod"
    ManagedBy   = "Terraform"
    Purpose     = "AKS-Cluster"
  }
}
