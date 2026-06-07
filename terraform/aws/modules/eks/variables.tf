variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for worker nodes"
  type        = list(string)
}

variable "node_instance_types" {
  description = "EC2 instance types for worker nodes"
  type        = list(string)
}

variable "node_desired_size" {
  description = "Desired number of worker nodes"
  type        = number
}

variable "node_min_size" {
  description = "Minimum number of worker nodes"
  type        = number
}

variable "node_max_size" {
  description = "Maximum number of worker nodes"
  type        = number
}

variable "gpu_instance_types" {
  description = "EC2 instance types for GPU nodes"
  type        = list(string)
}

variable "gpu_node_desired_size" {
  description = "Desired number of GPU nodes"
  type        = number
}

variable "gpu_node_min_size" {
  description = "Minimum number of GPU nodes"
  type        = number
}

variable "gpu_node_max_size" {
  description = "Maximum number of GPU nodes"
  type        = number
}

variable "enable_gpu_nodes" {
  description = "Enable GPU node group"
  type        = bool
}

variable "enable_spot_instances" {
  description = "Use spot instances"
  type        = bool
}

variable "domain_name" {
  description = "Domain name for ingress"
  type        = string
}

variable "enable_waf" {
  description = "Enable AWS WAF"
  type        = bool
}

variable "enable_acm" {
  description = "Enable ACM certificate"
  type        = bool
}
