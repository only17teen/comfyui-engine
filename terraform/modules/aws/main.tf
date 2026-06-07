terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
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
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

# ───────────────────────────────────────────────────────────────
# Variables
# ───────────────────────────────────────────────────────────────
variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "comfyui-engine"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-west-2"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "node_instance_types" {
  description = "EC2 instance types for worker nodes"
  type        = list(string)
  default     = ["m6i.2xlarge", "m6i.4xlarge"]
}

variable "desired_capacity" {
  description = "Desired number of worker nodes"
  type        = number
  default     = 3
}

variable "min_size" {
  description = "Minimum number of worker nodes"
  type        = number
  default     = 2
}

variable "max_size" {
  description = "Maximum number of worker nodes"
  type        = number
  default     = 20
}

variable "enable_gpu_nodes" {
  description = "Enable GPU node group for ComfyUI"
  type        = bool
  default     = true
}

variable "gpu_instance_types" {
  description = "GPU instance types"
  type        = list(string)
  default     = ["g5.2xlarge", "g5.4xlarge"]
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# ───────────────────────────────────────────────────────────────
# Data Sources
# ───────────────────────────────────────────────────────────────
data "aws_availability_zones" "available" {
  state = "available"
}

# ───────────────────────────────────────────────────────────────
# VPC and Networking
# ───────────────────────────────────────────────────────────────
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr

  azs             = slice(data.aws_availability_zones.available.names, 0, 3)
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway     = true
  single_nat_gateway     = false
  enable_dns_hostnames   = true
  enable_dns_support     = true
  map_public_ip_on_launch = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }
}

# ───────────────────────────────────────────────────────────────
# EKS Cluster
# ───────────────────────────────────────────────────────────────
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"

  cluster_name    = var.cluster_name
  cluster_version = "1.28"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access = true

  cluster_addons = {
    coredns = {
      most_recent = true
    }
    kube-proxy = {
      most_recent = true
    }
    vpc-cni = {
      most_recent = true
    }
    aws-ebs-csi-driver = {
      most_recent = true
    }
  }

  eks_managed_node_groups = {
    general = {
      desired_size = var.desired_capacity
      min_size     = var.min_size
      max_size     = var.max_size

      instance_types = var.node_instance_types
      capacity_type  = "ON_DEMAND"

      labels = {
        workload = "general"
      }

      taints = []

      tags = {
        Environment = var.environment
        NodeGroup   = "general"
      }
    }
  }

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }
}

# GPU Node Group (for ComfyUI)
resource "aws_eks_node_group" "gpu" {
  count = var.enable_gpu_nodes ? 1 : 0

  cluster_name    = module.eks.cluster_name
  node_group_name = "gpu-nodes"
  node_role_arn   = module.eks.eks_managed_node_groups["general"].iam_role_arn
  subnet_ids      = module.vpc.private_subnets

  instance_types = var.gpu_instance_types
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = 1
    min_size     = 0
    max_size     = 5
  }

  labels = {
    workload = "gpu"
    "nvidia.com/gpu" = "true"
  }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  tags = {
    Environment = var.environment
    NodeGroup   = "gpu"
    Terraform   = "true"
  }

  depends_on = [module.eks]
}

# ───────────────────────────────────────────────────────────────
# IRSA for EBS CSI Driver
# ───────────────────────────────────────────────────────────────
module "ebs_csi_driver_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${var.cluster_name}-ebs-csi-driver"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }

  tags = {
    Environment = var.environment
    Terraform   = "true"
  }
}

# ───────────────────────────────────────────────────────────────
# IRSA for Cluster Autoscaler
# ───────────────────────────────────────────────────────────────
module "cluster_autoscaler_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${var.cluster_name}-cluster-autoscaler"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:cluster-autoscaler"]
    }
  }

  tags = {
    Environment = var.environment
    Terraform   = "true"
  }
}

# ───────────────────────────────────────────────────────────────
# IRSA for Load Balancer Controller
# ───────────────────────────────────────────────────────────────
module "lb_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${var.cluster_name}-lb-controller"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }

  tags = {
    Environment = var.environment
    Terraform   = "true"
  }
}

# ───────────────────────────────────────────────────────────────
# S3 Bucket for Model Storage
# ───────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "models" {
  bucket = "${var.cluster_name}-models-${data.aws_caller_identity.current.account_id}"

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }
}

resource "aws_s3_bucket_versioning" "models" {
  bucket = aws_s3_bucket.models.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "models" {
  bucket = aws_s3_bucket.models.id

  rule {
    id     = "transition-to-glacier"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }
  }
}

data "aws_caller_identity" "current" {}

# ───────────────────────────────────────────────────────────────
# ECR Repository
# ───────────────────────────────────────────────────────────────
resource "aws_ecr_repository" "engine" {
  name                 = var.cluster_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true

  tags = {
    Environment = var.environment
    Project     = "comfyui-engine"
    Terraform   = "true"
  }
}

resource "aws_ecr_lifecycle_policy" "engine" {
  repository = aws_ecr_repository.engine.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 images"
        selection = {
          tagStatus     = "any"
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ───────────────────────────────────────────────────────────────
# Outputs
# ───────────────────────────────────────────────────────────────
output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = module.eks.cluster_endpoint
}

output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_certificate_authority_data" {
  description = "Cluster CA certificate"
  value       = module.eks.cluster_certificate_authority_data
}

output "oidc_provider_arn" {
  description = "OIDC provider ARN for IRSA"
  value       = module.eks.oidc_provider_arn
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "private_subnets" {
  description = "Private subnet IDs"
  value       = module.vpc.private_subnets
}

output "s3_bucket_name" {
  description = "S3 bucket for models"
  value       = aws_s3_bucket.models.id
}

output "ecr_repository_url" {
  description = "ECR repository URL"
  value       = aws_ecr_repository.engine.repository_url
}

output "kubeconfig_command" {
  description = "Command to update kubeconfig"
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
