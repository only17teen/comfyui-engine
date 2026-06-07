module "vpc" {
  source = "../../modules/vpc"

  environment        = var.environment
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
  cluster_name       = var.cluster_name
}

module "ecr" {
  source = "../../modules/ecr"

  repository_name = "comfyui-engine"
  environment     = var.environment
}

module "eks" {
  source = "../../modules/eks"

  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version
  environment     = var.environment
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids

  node_instance_types = var.node_instance_types
  node_desired_size   = var.node_desired_size
  node_min_size       = var.node_min_size
  node_max_size       = var.node_max_size

  gpu_instance_types    = var.gpu_instance_types
  gpu_node_desired_size = var.gpu_node_desired_size
  gpu_node_min_size     = var.gpu_node_min_size
  gpu_node_max_size     = var.gpu_node_max_size
  enable_gpu_nodes      = var.enable_gpu_nodes
  enable_spot_instances = var.enable_spot_instances

  domain_name   = var.domain_name
  enable_waf    = var.enable_waf
  enable_acm    = true
}

module "monitoring" {
  source = "../../modules/monitoring"

  count = var.enable_monitoring ? 1 : 0

  environment     = var.environment
  cluster_name    = module.eks.cluster_name
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids
  domain_name     = var.domain_name
  grafana_enabled = true
}

# Install Helm chart after EKS is ready
resource "helm_release" "comfyui_engine" {
  name       = "comfyui-engine"
  namespace  = "comfyui-engine"
  chart      = "../../../helm/comfyui-engine"
  version    = "4.0.0"
  create_namespace = true

  set {
    name  = "image.repository"
    value = module.ecr.repository_url
  }

  set {
    name  = "image.tag"
    value = "v4.0.0"
  }

  set {
    name  = "replicaCount"
    value = "3"
  }

  set {
    name  = "autoscaling.enabled"
    value = "true"
  }

  set {
    name  = "autoscaling.minReplicas"
    value = "3"
  }

  set {
    name  = "autoscaling.maxReplicas"
    value = "20"
  }

  set {
    name  = "config.comfyui.url"
    value = "http://comfyui-service.comfyui:8188"
  }

  set {
    name  = "ingress.enabled"
    value = "true"
  }

  set {
    name  = "ingress.hosts[0].host"
    value = var.domain_name
  }

  depends_on = [module.eks]
}
