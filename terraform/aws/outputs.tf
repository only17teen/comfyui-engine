output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = module.eks.cluster_endpoint
}

output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_version" {
  description = "EKS cluster version"
  value       = module.eks.cluster_version
}

output "cluster_security_group_id" {
  description = "EKS cluster security group ID"
  value       = module.eks.cluster_security_group_id
}

output "node_security_group_id" {
  description = "EKS node security group ID"
  value       = module.eks.node_security_group_id
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "private_subnets" {
  description = "Private subnet IDs"
  value       = module.vpc.private_subnets
}

output "public_subnets" {
  description = "Public subnet IDs"
  value       = module.vpc.public_subnets
}

output "gpu_node_group_name" {
  description = "GPU node group name"
  value       = module.eks.eks_managed_node_groups["gpu"].node_group_name
}

output "gpu_node_group_arn" {
  description = "GPU node group ARN"
  value       = module.eks.eks_managed_node_groups["gpu"].node_group_arn
}

output "kubeconfig" {
  description = "Kubeconfig command"
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${var.cluster_name}"
}

output "backup_bucket" {
  description = "S3 backup bucket name"
  value       = var.enable_backup ? aws_s3_bucket.backups[0].id : null
}

output "monitoring_endpoint" {
  description = "Prometheus/Grafana endpoint"
  value       = var.enable_monitoring ? "http://comfyui-engine-grafana.${var.cluster_name}.local" : null
}
