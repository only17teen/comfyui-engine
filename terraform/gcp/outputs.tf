output "cluster_endpoint" {
  description = "GKE cluster endpoint"
  value       = google_container_cluster.main.endpoint
}

output "cluster_name" {
  description = "GKE cluster name"
  value       = google_container_cluster.main.name
}

output "cluster_version" {
  description = "GKE cluster version"
  value       = google_container_cluster.main.master_version
}

output "cluster_location" {
  description = "GKE cluster location"
  value       = google_container_cluster.main.location
}

output "network" {
  description = "VPC network name"
  value       = google_compute_network.main.name
}

output "subnetwork" {
  description = "Subnet name"
  value       = google_compute_subnetwork.main.name
}

output "gpu_node_pool_name" {
  description = "GPU node pool name"
  value       = google_container_node_pool.gpu.name
}

output "gpu_node_pool_instance_group_urls" {
  description = "GPU node pool instance group URLs"
  value       = google_container_node_pool.gpu.managed_instance_group_urls
}

output "kubeconfig" {
  description = "Kubeconfig command"
  value       = "gcloud container clusters get-credentials ${var.cluster_name} --region ${var.region} --project ${var.project_id}"
}

output "backup_bucket" {
  description = "GCS backup bucket name"
  value       = var.enable_backup ? google_storage_bucket.backups[0].name : null
}

output "monitoring_endpoint" {
  description = "Prometheus/Grafana endpoint"
  value       = var.enable_monitoring ? "http://comfyui-engine-grafana.${var.cluster_name}.local" : null
}
