variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "cluster_name" {
  description = "GKE cluster name"
  type        = string
  default     = "comfyui-engine"
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "us-central1-a"
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

variable "machine_type" {
  description = "GCE machine type"
  type        = string
  default     = "n1-standard-4"
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

variable "labels" {
  description = "Resource labels"
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
