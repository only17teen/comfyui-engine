output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "eks_cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = module.eks.cluster_endpoint
  sensitive   = true
}

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "eks_cluster_certificate_authority" {
  description = "EKS cluster CA certificate"
  value       = module.eks.cluster_certificate_authority_data
  sensitive   = true
}

output "ecr_repository_url" {
  description = "ECR repository URL"
  value       = module.ecr.repository_url
}

output "load_balancer_dns" {
  description = "Load balancer DNS name"
  value       = module.eks.load_balancer_dns
}

output "kubeconfig_command" {
  description = "Command to update kubeconfig"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "helm_install_command" {
  description = "Command to install Helm chart"
  value       = "helm install comfyui-engine ../../helm/comfyui-engine --namespace comfyui-engine --create-namespace"
}

output "grafana_url" {
  description = "Grafana URL"
  value       = var.enable_monitoring ? "https://grafana.${var.domain_name}" : null
}

output "prometheus_url" {
  description = "Prometheus URL"
  value       = var.enable_monitoring ? "https://prometheus.${var.domain_name}" : null
}
