output "cluster_endpoint" {
  description = "AKS cluster endpoint"
  value       = azurerm_kubernetes_cluster.main.kube_config.0.host
}

output "cluster_name" {
  description = "AKS cluster name"
  value       = azurerm_kubernetes_cluster.main.name
}

output "cluster_version" {
  description = "AKS cluster version"
  value       = azurerm_kubernetes_cluster.main.kubernetes_version
}

output "cluster_location" {
  description = "AKS cluster location"
  value       = azurerm_kubernetes_cluster.main.location
}

output "resource_group" {
  description = "Resource group name"
  value       = azurerm_resource_group.main.name
}

output "resource_group_id" {
  description = "Resource group ID"
  value       = azurerm_resource_group.main.id
}

output "vnet_id" {
  description = "Virtual network ID"
  value       = azurerm_virtual_network.main.id
}

output "subnet_id" {
  description = "Subnet ID"
  value       = azurerm_subnet.main.id
}

output "gpu_node_pool_name" {
  description = "GPU node pool name"
  value       = azurerm_kubernetes_cluster_node_pool.gpu.name
}

output "gpu_node_pool_vm_size" {
  description = "GPU node pool VM size"
  value       = azurerm_kubernetes_cluster_node_pool.gpu.vm_size
}

output "kubeconfig" {
  description = "Kubeconfig command"
  value       = "az aks get-credentials --resource-group ${azurerm_resource_group.main.name} --name ${azurerm_kubernetes_cluster.main.name}"
}

output "backup_storage_account" {
  description = "Backup storage account name"
  value       = var.enable_backup ? azurerm_storage_account.backups[0].name : null
}

output "monitoring_endpoint" {
  description = "Prometheus/Grafana endpoint"
  value       = var.enable_monitoring ? "http://comfyui-engine-grafana.${var.cluster_name}.local" : null
}
