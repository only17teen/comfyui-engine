terraform {
  required_version = ">= 1.5.0"

  backend "s3" {
    bucket         = "comfyui-engine-terraform-state"
    key            = "production/terraform.tfstate"
    region         = "us-west-2"
    encrypt        = true
    dynamodb_table = "comfyui-engine-terraform-locks"
  }
}

module "aws_infrastructure" {
  source = "../modules/aws"

  cluster_name = "comfyui-engine-prod"
  region       = "us-west-2"
  environment  = "production"

  desired_capacity = 5
  min_size         = 3
  max_size         = 30

  node_instance_types = ["m6i.4xlarge", "m6i.8xlarge"]

  enable_gpu_nodes    = true
  gpu_instance_types  = ["g5.4xlarge", "g5.8xlarge"]

  vpc_cidr = "10.0.0.0/16"
}

# Kubernetes provider configuration
provider "kubernetes" {
  host                   = module.aws_infrastructure.cluster_endpoint
  cluster_ca_certificate = base64decode(module.aws_infrastructure.cluster_certificate_authority_data)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.aws_infrastructure.cluster_name]
  }
}

provider "helm" {
  kubernetes {
    host                   = module.aws_infrastructure.cluster_endpoint
    cluster_ca_certificate = base64decode(module.aws_infrastructure.cluster_certificate_authority_data)
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.aws_infrastructure.cluster_name]
    }
  }
}

# Deploy ComfyUI Engine via Helm
resource "helm_release" "comfyui_engine" {
  name       = "comfyui-engine"
  repository = "oci://${module.aws_infrastructure.ecr_repository_url}"
  chart      = "comfyui-engine"
  version    = "4.0.0"
  namespace  = "comfyui-engine"

  create_namespace = true

  values = [
    file("${path.module}/values-production.yaml")
  ]

  set {
    name  = "image.repository"
    value = module.aws_infrastructure.ecr_repository_url
  }

  set {
    name  = "config.comfyui.url"
    value = "http://comfyui-service.comfyui.svc.cluster.local:8188"
  }

  depends_on = [module.aws_infrastructure]
}

# Deploy monitoring stack
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

  depends_on = [module.aws_infrastructure]
}

# Deploy ingress controller
resource "helm_release" "ingress_nginx" {
  name       = "ingress-nginx"
  repository = "https://kubernetes.github.io/ingress-nginx"
  chart      = "ingress-nginx"
  version    = "4.8.0"
  namespace  = "ingress-nginx"

  create_namespace = true

  set {
    name  = "controller.service.type"
    value = "LoadBalancer"
  }

  depends_on = [module.aws_infrastructure]
}

# Deploy cert-manager
resource "helm_release" "cert_manager" {
  name       = "cert-manager"
  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  version    = "1.13.0"
  namespace  = "cert-manager"

  create_namespace = true

  set {
    name  = "installCRDs"
    value = "true"
  }

  depends_on = [module.aws_infrastructure]
}

# Deploy cluster autoscaler
resource "helm_release" "cluster_autoscaler" {
  name       = "cluster-autoscaler"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = "9.34.0"
  namespace  = "kube-system"

  set {
    name  = "autoDiscovery.clusterName"
    value = module.aws_infrastructure.cluster_name
  }

  set {
    name  = "awsRegion"
    value = "us-west-2"
  }

  set {
    name  = "rbac.serviceAccount.annotations.eks\.amazonaws\.com/role-arn"
    value = module.aws_infrastructure.cluster_autoscaler_irsa_role_arn
  }

  depends_on = [module.aws_infrastructure]
}

# Deploy AWS Load Balancer Controller
resource "helm_release" "aws_lb_controller" {
  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = "1.6.0"
  namespace  = "kube-system"

  set {
    name  = "clusterName"
    value = module.aws_infrastructure.cluster_name
  }

  set {
    name  = "serviceAccount.annotations.eks\.amazonaws\.com/role-arn"
    value = module.aws_infrastructure.lb_controller_irsa_role_arn
  }

  depends_on = [module.aws_infrastructure]
}

# Outputs
output "cluster_endpoint" {
  value = module.aws_infrastructure.cluster_endpoint
}

output "ecr_repository_url" {
  value = module.aws_infrastructure.ecr_repository_url
}

output "kubeconfig_command" {
  value = module.aws_infrastructure.kubeconfig_command
}

output "s3_bucket_name" {
  value = module.aws_infrastructure.s3_bucket_name
}
