# GKE Cluster
resource "google_container_cluster" "main" {
  name     = var.cluster_name
  location = var.region
  project  = var.project_id

  release_channel {
    channel = "REGULAR"
  }

  min_master_version = var.kubernetes_version

  # We can't create a cluster with no node pool defined, but we want to only use
  # separately managed node pools. So we create the smallest possible default
  # node pool and immediately delete it.
  remove_default_node_pool = true
  initial_node_count       = 1

  network    = google_compute_network.main.name
  subnetwork = google_compute_subnetwork.main.name

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

  addons_config {
    http_load_balancing {
      disabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
    network_policy_config {
      disabled = false
    }
  }

  network_policy {
    enabled = true
  }

  resource_labels = var.labels

  depends_on = [
    google_compute_network.main,
    google_compute_subnetwork.main,
  ]
}

# General node pool
resource "google_container_node_pool" "general" {
  name       = "general"
  location   = var.region
  cluster    = google_container_cluster.main.name
  project    = var.project_id
  node_count = 2

  node_config {
    machine_type = "e2-medium"
    disk_size_gb = 50
    disk_type    = "pd-standard"

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = merge(var.labels, {
      workload = "general"
    })

    tags = ["general"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  depends_on = [google_container_cluster.main]
}

# GPU node pool
resource "google_container_node_pool" "gpu" {
  name     = "gpu"
  location = var.region
  cluster  = google_container_cluster.main.name
  project  = var.project_id

  autoscaling {
    min_node_count = var.node_min_count
    max_node_count = var.node_max_count
  }

  node_count = var.node_count

  node_config {
    machine_type = var.machine_type
    disk_size_gb = var.node_disk_size
    disk_type    = "pd-ssd"

    guest_accelerator {
      type  = var.gpu_type
      count = var.gpu_count
      gpu_sharing_config {
        gpu_sharing_strategy = "TIME_SHARING"
        max_shared_clients_per_gpu = 2
      }
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    labels = merge(var.labels, {
      workload    = "gpu"
      accelerator = "nvidia-gpu"
    })

    taint {
      key    = "nvidia.com/gpu"
      value  = "true"
      effect = "NO_SCHEDULE"
    }

    tags = ["gpu"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  depends_on = [google_container_cluster.main]
}

# Network
resource "google_compute_network" "main" {
  name                    = "${var.cluster_name}-network"
  project                 = var.project_id
  auto_create_subnetworks = false
  routing_mode            = "GLOBAL"
}

resource "google_compute_subnetwork" "main" {
  name          = "${var.cluster_name}-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.main.name
  ip_cidr_range = "10.0.0.0/16"

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.1.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.2.0.0/16"
  }

  private_ip_google_access = true
}

# Firewall rules
resource "google_compute_firewall" "allow_internal" {
  name    = "${var.cluster_name}-allow-internal"
  project = var.project_id
  network = google_compute_network.main.name

  allow {
    protocol = "icmp"
  }

  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }

  allow {
    protocol = "udp"
    ports    = ["0-65535"]
  }

  source_ranges = ["10.0.0.0/16", "10.1.0.0/16", "10.2.0.0/16"]
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

  depends_on = [google_container_cluster.main]
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
    value = "gce"
  }

  set {
    name  = "extraArgs.balance-similar-node-groups"
    value = "true"
  }

  depends_on = [google_container_cluster.main]
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
