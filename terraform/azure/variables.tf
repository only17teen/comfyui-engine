variable "resource_group" {
  description = "Azure resource group name"
  type        = string
}

variable "cluster_name" {
  description = "AKS cluster name"
  type        = string
  default     = "comfyui-engine"
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus"
}

variable "node_count" {
  description = "Number of GPU nodes"
  type        = number
  default     = 2
}

variable "gpu_type" {
  description = "GPU type"
  type        = string
  default     = "nvidia-tesla-t4"
}

variable "gpu_count" {
  description = "GPUs per node"
  type        = number
  default     = 1
}

variable "vm_size" {
  description = "Azure VM size"
  type        = string
  default     = "Standard_NC4as_T4_v3"
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
