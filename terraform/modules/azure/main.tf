terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.75"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.11"
    }
  }
}

# ───────────────────────────────────────────────────────────────
# Variables
# ───────────────────────────────────────────────────────────────
variable "cluster_name" {
  description = "AKS cluster name"
  type        = string
  default     = "comfyui-engine"
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "westus2"
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
  default     = "comfyui-engine-rg"
}

variable "node_vm_sizes" {
  description = "VM sizes for standard nodes"
  type        = list(string)
  default     = ["Standard_D8s_v5", "Standard_D16s_v5"]
}

variable "node_count" {
  description = "Initial node count"
  type        = number
  default     = 3
}

variable "min_count" {
  description = "Minimum node count"
  type        = number
  default     = 2
}

variable "max_count" {
  description = "Maximum node count"
  type        = number
  default     = 20
}

variable "enable_gpu_nodes" {
  description = "Enable GPU node pool for ComfyUI"
  type        = bool
  default     = true
}

variable "gpu_vm_sizes" {
  description = "GPU VM sizes"
  type        = list(string)
  default     = ["Standard_NC6s_v3", "Standard_NC12s_v3"]
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# ───────────────────────────────────────────────────────────────
# Provider Configuration
# ───────────────────────────────────────────────────────────────
provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

# ───────────────────────────────────────────────────────────────
# Resource Group
# ───────────────────────────────────────────────────────────────
resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }
}

# ───────────────────────────────────────────────────────────────
# Virtual Network
# ───────────────────────────────────────────────────────────────
resource "azurerm_virtual_network" "main" {
  name                = "${var.cluster_name}-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Environment = var.environment
    Terraform   = "true"
  }
}

resource "azurerm_subnet" "aks" {
  name                 = "aks-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.1.0/24"]
}

# ───────────────────────────────────────────────────────────────
# AKS Cluster
# ───────────────────────────────────────────────────────────────
resource "azurerm_kubernetes_cluster" "main" {
  name                = var.cluster_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.cluster_name
  kubernetes_version  = "1.28"

  default_node_pool {
    name                = "general"
    node_count          = var.node_count
    vm_size             = var.node_vm_sizes[0]
    vnet_subnet_id      = azurerm_subnet.aks.id
    type                = "VirtualMachineScaleSets"
    enable_auto_scaling = true
    min_count           = var.min_count
    max_count           = var.max_count
    os_disk_size_gb     = 128
    os_disk_type        = "Managed"

    tags = {
      Environment = var.environment
      NodePool    = "general"
      Terraform   = "true"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku  = "standard"
    service_cidr       = "10.1.0.0/16"
    dns_service_ip     = "10.1.0.10"
  }

  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  azure_policy_enabled = true

  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    secret_rotation_interval = "2m"
  }

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }

  depends_on = [azurerm_resource_group.main]
}

# ───────────────────────────────────────────────────────────────
# GPU Node Pool (for ComfyUI)
# ───────────────────────────────────────────────────────────────
resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
  count = var.enable_gpu_nodes ? 1 : 0

  name                  = "gpu"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.main.id
  vm_size               = var.gpu_vm_sizes[0]
  node_count            = 1
  vnet_subnet_id        = azurerm_subnet.aks.id
  enable_auto_scaling   = true
  min_count             = 0
  max_count             = 5
  os_disk_size_gb       = 200
  os_disk_type          = "Managed"

  node_taints = [
    "nvidia.com/gpu=true:NoSchedule"
  ]

  node_labels = {
    "workload"         = "gpu"
    "nvidia.com/gpu"   = "true"
    "environment"      = var.environment
  }

  tags = {
    Environment = var.environment
    NodePool    = "gpu"
    Terraform   = "true"
  }
}

# ───────────────────────────────────────────────────────────────
# Container Registry (ACR)
# ───────────────────────────────────────────────────────────────
resource "azurerm_container_registry" "engine" {
  name                = replace("${var.cluster_name}acr", "-", "")
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"
  admin_enabled       = false

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }
}

# ACR pull permission for AKS
resource "azurerm_role_assignment" "aks_acr_pull" {
  principal_id                     = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
  role_definition_name             = "AcrPull"
  scope                            = azurerm_container_registry.engine.id
  skip_service_principal_aad_check = true
}

# ───────────────────────────────────────────────────────────────
# Storage Account for Models
# ───────────────────────────────────────────────────────────────
resource "azurerm_storage_account" "models" {
  name                     = replace("${var.cluster_name}models", "-", "")
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
  access_tier              = "Hot"

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }
  }

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }
}

resource "azurerm_storage_container" "models" {
  name                  = "models"
  storage_account_name  = azurerm_storage_account.models.name
  container_access_type = "private"
}

# ───────────────────────────────────────────────────────────────
# Managed Identity for Workload Identity
# ───────────────────────────────────────────────────────────────
resource "azurerm_user_assigned_identity" "engine" {
  name                = "${var.cluster_name}-engine"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_federated_identity_credential" "engine" {
  name                = "${var.cluster_name}-engine-federated"
  resource_group_name = azurerm_resource_group.main.name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  parent_id           = azurerm_user_assigned_identity.engine.id
  subject             = "system:serviceaccount:comfyui-engine:comfyui-engine"
}

# Storage Blob Data Contributor role
resource "azurerm_role_assignment" "engine_storage" {
  scope                = azurerm_storage_account.models.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.engine.principal_id
}

# ───────────────────────────────────────────────────────────────
# Log Analytics Workspace
# ───────────────────────────────────────────────────────────────
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.cluster_name}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = {
    Environment = var.environment
    Terraform   = "true"
  }
}

# Container Insights
resource "azurerm_monitor_diagnostic_setting" "aks" {
  name                       = "aks-diagnostics"
  target_resource_id         = azurerm_kubernetes_cluster.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "kube-apiserver"
  }
  enabled_log {
    category = "kube-audit"
  }
  enabled_log {
    category = "kube-controller-manager"
  }
  enabled_log {
    category = "kube-scheduler"
  }
  enabled_log {
    category = "cluster-autoscaler"
  }

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}

# ───────────────────────────────────────────────────────────────
# Outputs
# ───────────────────────────────────────────────────────────────
output "cluster_name" {
  description = "AKS cluster name"
  value       = azurerm_kubernetes_cluster.main.name
}

output "cluster_endpoint" {
  description = "AKS cluster endpoint"
  value       = azurerm_kubernetes_cluster.main.kube_config.0.host
}

output "resource_group_name" {
  description = "Resource group name"
  value       = azurerm_resource_group.main.name
}

output "acr_login_server" {
  description = "ACR login server"
  value       = azurerm_container_registry.engine.login_server
}

output "storage_account_name" {
  description = "Storage account for models"
  value       = azurerm_storage_account.models.name
}

output "kubeconfig_command" {
  description = "Command to get kubeconfig"
  value       = "az aks get-credentials --resource-group ${azurerm_resource_group.main.name} --name ${azurerm_kubernetes_cluster.main.name}"
}

output "workload_identity_client_id" {
  description = "Workload Identity client ID"
  value       = azurerm_user_assigned_identity.engine.client_id
}
