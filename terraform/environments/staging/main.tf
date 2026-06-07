terraform {
  required_version = ">= 1.5.0"

  backend "gcs" {
    bucket = "comfyui-engine-terraform-state"
    prefix = "staging/terraform.tfstate"
  }
}

module "gcp_infrastructure" {
  source = "../modules/gcp"

  project_id   = "comfyui-engine-staging"
  cluster_name = "comfyui-engine-staging"
  region       = "us-central1"
  environment  = "staging"

  desired_node_count = 1
  min_node_count     = 1
  max_node_count     = 5

  node_machine_types = ["n2-standard-4"]

  enable_gpu_nodes    = false
  gpu_machine_types   = []
}

# Kubernetes provider
provider "kubernetes" {
  host                   = "https://${module.gcp_infrastructure.cluster_endpoint}"
  cluster_ca_certificate = base64decode(module.gcp_infrastructure.cluster_ca_certificate)
  token                  = data.google_client_config.default.access_token
}

provider "helm" {
  kubernetes {
    host                   = "https://${module.gcp_infrastructure.cluster_endpoint}"
    cluster_ca_certificate = base64decode(module.gcp_infrastructure.cluster_ca_certificate)
    token                  = data.google_client_config.default.access_token
  }
}

data "google_client_config" "default" {}

# Deploy ComfyUI Engine via Helm
resource "helm_release" "comfyui_engine" {
  name       = "comfyui-engine"
  repository = "oci://${module.gcp_infrastructure.artifact_registry_url}"
  chart      = "comfyui-engine"
  version    = "4.0.0-staging"
  namespace  = "comfyui-engine"

  create_namespace = true

  values = [
    file("${path.module}/values-staging.yaml")
  ]

  set {
    name  = "image.repository"
    value = module.gcp_infrastructure.artifact_registry_url
  }

  set {
    name  = "replicaCount"
    value = "2"
  }

  set {
    name  = "config.engine.log_level"
    value = "DEBUG"
  }

  depends_on = [module.gcp_infrastructure]
}

# Deploy monitoring
resource "helm_release" "prometheus" {
  name       = "prometheus"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = "55.0.0"
  namespace  = "monitoring"

  create_namespace = true

  values = [
    file("${path.module}/monitoring-values.yaml")
  ]

  depends_on = [module.gcp_infrastructure]
}

# Outputs
output "cluster_name" {
  value = module.gcp_infrastructure.cluster_name
}

output "artifact_registry_url" {
  value = module.gcp_infrastructure.artifact_registry_url
}

output "kubeconfig_command" {
  value = module.gcp_infrastructure.kubeconfig_command
}

output "storage_bucket_name" {
  value = module.gcp_infrastructure.storage_bucket_name
}
