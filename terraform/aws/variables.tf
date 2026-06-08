variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "comfyui-engine"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for subnets"
  type        = list(string)
  default     = ["a", "b", "c"]
}

variable "node_count" {
  description = "Number of GPU nodes"
  type        = number
  default     = 2
}

variable "gpu_type" {
  description = "GPU instance type"
  type        = string
  default     = "nvidia-tesla-t4"
}

variable "gpu_count" {
  description = "GPUs per node"
  type        = number
  default     = 1
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "g4dn.xlarge"
}

variable "enable_monitoring" {
  description = "Enable Prometheus/Grafana"
  type        = bool
  default     = true
}

variable "enable_backup" {
  description = "Enable automated backups"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Resource tags"
  type        = map(string)
  default     = {}
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.28"
}

variable "node_disk_size" {
  description = "Node disk size in GB"
  type        = number
  default     = 100
}

variable "node_min_count" {
  description = "Minimum number of GPU nodes"
  type        = number
  default     = 1
}

variable "node_max_count" {
  description = "Maximum number of GPU nodes"
  type        = number
  default     = 10
}

variable "enable_cluster_autoscaler" {
  description = "Enable cluster autoscaler"
  type        = bool
  default     = true
}

variable "enable_gpu_operator" {
  description = "Enable NVIDIA GPU Operator"
  type        = bool
  default     = true
}

variable "helm_chart_version" {
  description = "ComfyUI Engine Helm chart version"
  type        = string
  default     = "1.0.0"
}

variable "helm_values_file" {
  description = "Path to Helm values file"
  type        = string
  default     = ""
}
