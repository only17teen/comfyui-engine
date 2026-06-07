terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
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

variable "network_name" {
  description = "VPC network name"
  type        = string
  default     = "comfyui-engine-network"
}

variable "node_machine_types" {
  description = "Machine types for standard nodes"
  type        = list(string)
  default     = ["n2-standard-8", "n2-standard-16"]
}

variable "desired_node_count" {
  description = "Desired number of nodes per zone"
  type        = number
  default     = 1
}

variable "min_node_count" {
  description = "Minimum number of nodes per zone"
  type        = number
  default     = 1
}

variable "max_node_count" {
  description = "Maximum number of nodes per zone"
  type        = number
  default     = 10
}

variable "enable_gpu_nodes" {
  description = "Enable GPU node pool for ComfyUI"
  type        = bool
  default     = true
}

variable "gpu_machine_types" {
  description = "GPU machine types"
  type        = list(string)
  default     = ["n1-standard-4"]
}

variable "gpu_accelerator_type" {
  description = "GPU accelerator type"
  type        = string
  default     = "nvidia-tesla-t4"
}

variable "gpu_accelerator_count" {
  description = "Number of GPUs per node"
  type        = number
  default     = 1
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# ───────────────────────────────────────────────────────────────
# Provider Configuration
# ───────────────────────────────────────────────────────────────
provider "google" {
  project = var.project_id
  region  = var.region
}

# ───────────────────────────────────────────────────────────────
# VPC Network
# ───────────────────────────────────────────────────────────────
resource "google_compute_network" "vpc" {
  name                    = var.network_name
  auto_create_subnetworks = false
  routing_mode            = "GLOBAL"

  depends_on = [google_project_service.compute]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.network_name}-subnet"
  ip_cidr_range = "10.0.0.0/16"
  region        = var.region
  network       = google_compute_network.vpc.id

  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.1.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.2.0.0/20"
  }
}

# ───────────────────────────────────────────────────────────────
# Enable APIs
# ───────────────────────────────────────────────────────────────
resource "google_project_service" "compute" {
  service = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "container" {
  service = "container.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry" {
  service = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "storage" {
  service = "storage.googleapis.com"
  disable_on_destroy = false
}

# ───────────────────────────────────────────────────────────────
# GKE Cluster
# ───────────────────────────────────────────────────────────────
resource "google_container_cluster" "primary" {
  name     = var.cluster_name
  location = var.region

  network    = google_compute_network.vpc.name
  subnetwork = google_compute_subnetwork.subnet.name

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "0.0.0.0/0"
      display_name = "All"
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  addons_config {
    http_load_balancing {
      disabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
    gcp_filestore_csi_driver_config {
      enabled = true
    }
  }

  depends_on = [
    google_project_service.compute,
    google_project_service.container,
  ]
}

# ───────────────────────────────────────────────────────────────
# Standard Node Pool
# ───────────────────────────────────────────────────────────────
resource "google_container_node_pool" "general" {
  name       = "general-pool"
  location   = var.region
  cluster    = google_container_cluster.primary.name
  node_count = var.desired_node_count

  autoscaling {
    min_node_count = var.min_node_count
    max_node_count = var.max_node_count
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.node_machine_types[0]
    disk_size_gb = 100
    disk_type    = "pd-ssd"

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      workload = "general"
      environment = var.environment
    }

    tags = ["comfyui-engine", var.environment]
  }
}

# ───────────────────────────────────────────────────────────────
# GPU Node Pool (for ComfyUI)
# ───────────────────────────────────────────────────────────────
resource "google_container_node_pool" "gpu" {
  count = var.enable_gpu_nodes ? 1 : 0

  name     = "gpu-pool"
  location = var.region
  cluster  = google_container_cluster.primary.name
  node_count = 1

  autoscaling {
    min_node_count = 0
    max_node_count = 5
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.gpu_machine_types[0]
    disk_size_gb = 200
    disk_type    = "pd-ssd"

    guest_accelerator {
      type  = var.gpu_accelerator_type
      count = var.gpu_accelerator_count
      gpu_sharing_config {
        gpu_sharing_strategy = "TIME_SHARING"
        max_shared_clients_per_gpu = 2
      }
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      workload = "gpu"
      "nvidia.com/gpu" = "true"
      environment = var.environment
    }

    taint {
      key    = "nvidia.com/gpu"
      value  = "true"
      effect = "NO_SCHEDULE"
    }

    tags = ["comfyui-engine", "gpu", var.environment]
  }
}

# ───────────────────────────────────────────────────────────────
# Artifact Registry
# ───────────────────────────────────────────────────────────────
resource "google_artifact_registry_repository" "engine" {
  location      = var.region
  repository_id = var.cluster_name
  format        = "DOCKER"
  description   = "ComfyUI Engine Docker images"

  depends_on = [google_project_service.artifactregistry]
}

# ───────────────────────────────────────────────────────────────
# Cloud Storage Bucket for Models
# ───────────────────────────────────────────────────────────────
resource "google_storage_bucket" "models" {
  name          = "${var.project_id}-${var.cluster_name}-models"
  location      = var.region
  force_destroy = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  uniform_bucket_level_access = true

  depends_on = [google_project_service.storage]
}

# ───────────────────────────────────────────────────────────────
# Service Account for Workload Identity
# ───────────────────────────────────────────────────────────────
resource "google_service_account" "engine" {
  account_id   = "${var.cluster_name}-engine"
  display_name = "ComfyUI Engine Service Account"
  project      = var.project_id
}

resource "google_project_iam_member" "engine_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.engine.email}"
}

resource "google_project_iam_member" "engine_artifact_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.engine.email}"
}

# ───────────────────────────────────────────────────────────────
# Workload Identity Binding
# ───────────────────────────────────────────────────────────────
resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.engine.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[comfyui-engine/comfyui-engine]"
}

# ───────────────────────────────────────────────────────────────
# Outputs
# ───────────────────────────────────────────────────────────────
output "cluster_name" {
  description = "GKE cluster name"
  value       = google_container_cluster.primary.name
}

output "cluster_endpoint" {
  description = "GKE cluster endpoint"
  value       = google_container_cluster.primary.endpoint
}

output "cluster_location" {
  description = "GKE cluster location"
  value       = google_container_cluster.primary.location
}

output "network_name" {
  description = "VPC network name"
  value       = google_compute_network.vpc.name
}

output "artifact_registry_url" {
  description = "Artifact Registry URL"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.engine.repository_id}"
}

output "storage_bucket_name" {
  description = "Cloud Storage bucket for models"
  value       = google_storage_bucket.models.name
}

output "kubeconfig_command" {
  description = "Command to get kubeconfig"
  value       = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --region ${var.region} --project ${var.project_id}"
}

output "workload_identity_service_account" {
  description = "Service account for Workload Identity"
  value       = google_service_account.engine.email
}
