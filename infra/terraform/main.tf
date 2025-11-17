# Resource Group
resource "azurerm_resource_group" "aks" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# AKS Cluster
resource "azurerm_kubernetes_cluster" "aks" {
  name                = var.cluster_name
  location            = azurerm_resource_group.aks.location
  resource_group_name = azurerm_resource_group.aks.name
  dns_prefix          = var.cluster_name
  kubernetes_version  = var.kubernetes_version

  # System node pool (regular VMs for critical workloads)
  default_node_pool {
    name                = "system"
    vm_size             = var.vm_size
    type                = "VirtualMachineScaleSets"
    # auto_scaling_enabled = true
    node_count          = var.node_count
    # min_count           = 1
    # max_count           = 3
    os_disk_size_gb     = 30
    
    tags = merge(
      var.tags,
      {
        NodePool = "system"
        Type     = "regular"
      }
    )
  }

  # Managed identity for cluster
  identity {
    type = "SystemAssigned"
  }

  # Network configuration
  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
    service_cidr      = "10.0.0.0/16"
    dns_service_ip    = "10.0.0.10"
  }

  tags = var.tags
}

# Spot instance node pool for workloads
resource "azurerm_kubernetes_cluster_node_pool" "spot" {
  name                  = "spot"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = var.spot_vm_size
  
  # Spot instance configuration
  priority        = "Spot"
  eviction_policy = "Delete"
  spot_max_price  = -1  # Pay up to regular price (recommended)
  
  # Auto-scaling
  auto_scaling_enabled = true
  node_count          = var.spot_node_count
  min_count           = 0  # Can scale to zero
  max_count           = 2
  
  os_disk_size_gb = 30
  
  # Taints to mark spot nodes
  node_taints = ["kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
  
  # Labels for targeting spot nodes
  node_labels = {
    "workload"     = "spot"
    "tier"         = "best-effort"
    "node-type"    = "spot"
  }

  tags = merge(
    var.tags,
    {
      NodePool = "spot"
      Type     = "spot-instance"
    }
  )
}

