# Resource Group
resource "azurerm_resource_group" "main" {
  name     = var.resource_group
  location = var.location
  tags     = var.tags
}

# Virtual Network
resource "azurerm_virtual_network" "main" {
  name                = "${var.cluster_name}-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_subnet" "main" {
  name                 = "${var.cluster_name}-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.1.0/24"]
}

# AKS Cluster
resource "azurerm_kubernetes_cluster" "main" {
  name                = var.cluster_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.cluster_name
  kubernetes_version  = var.kubernetes_version

  default_node_pool {
    name       = "general"
    node_count = 2
    vm_size    = "Standard_D4s_v3"
    os_disk_size_gb = 50
    vnet_subnet_id = azurerm_subnet.main.id

    tags = var.tags
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
  }

  tags = var.tags

  depends_on = [azurerm_resource_group.main]
}

# GPU Node Pool
resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
  name                  = "gpu"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.main.id
  vm_size               = var.vm_size
  node_count            = var.node_count
  os_disk_size_gb       = var.node_disk_size
  vnet_subnet_id        = azurerm_subnet.main.id

  min_count = var.node_min_count
  max_count = var.node_max_count

  node_labels = {
    workload    = "gpu"
    accelerator = "nvidia-gpu"
  }

  node_taints = [
    "nvidia.com/gpu=true:NoSchedule"
  ]

  tags = var.tags

  depends_on = [azurerm_kubernetes_cluster.main]
}

# GPU Operator
resource "helm_release" "gpu_operator" {
  count = var.enable_gpu_operator ? 1 : 0

  name       = "gpu-operator"
  repository = "https://nvidia.github.io/gpu-operator"
  chart      = "gpu-operator"
  version    = "v23.6.1"
  namespace  = "gpu-operator"

  create_namespace = true

  set {
    name  = "driver.enabled"
    value = "true"
  }

  set {
    name  = "toolkit.enabled"
    value = "true"
  }

  set {
    name  = "dcgmExporter.enabled"
    value = "true"
  }

  depends_on = [azurerm_kubernetes_cluster.main]
}

# Cluster Autoscaler
resource "helm_release" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler ? 1 : 0

  name       = "cluster-autoscaler"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = "9.29.0"
  namespace  = "kube-system"

  set {
    name  = "autoDiscovery.clusterName"
    value = var.cluster_name
  }

  set {
    name  = "cloudProvider"
    value = "azure"
  }

  set {
    name  = "azureSubscriptionID"
    value = data.azurerm_subscription.current.subscription_id
  }

  set {
    name  = "azureResourceGroup"
    value = azurerm_resource_group.main.name
  }

  set {
    name  = "azureVMType"
    value = "AKS"
  }

  set {
    name  = "azureClusterName"
    value = var.cluster_name
  }

  set {
    name  = "azureNodeResourceGroup"
    value = azurerm_kubernetes_cluster.main.node_resource_group
  }

  depends_on = [azurerm_kubernetes_cluster.main]
}

# ComfyUI Engine Helm Chart
resource "helm_release" "comfyui_engine" {
  name       = "comfyui-engine"
  repository = "https://only17teen.github.io/comfyui-engine"
  chart      = "comfyui-engine"
  version    = var.helm_chart_version
  namespace  = "comfyui-engine"

  create_namespace = true

  values = var.helm_values_file != "" ? [file(var.helm_values_file)] : []

  set {
    name  = "gpu.enabled"
    value = "true"
  }

  set {
    name  = "gpu.count"
    value = var.gpu_count
  }

  set {
    name  = "nodeSelector.accelerator"
    value = "nvidia-gpu"
  }

  set {
    name  = "tolerations[0].key"
    value = "nvidia.com/gpu"
  }

  set {
    name  = "tolerations[0].operator"
    value = "Exists"
  }

  set {
    name  = "tolerations[0].effect"
    value = "NoSchedule"
  }

  depends_on = [helm_release.gpu_operator]
}
