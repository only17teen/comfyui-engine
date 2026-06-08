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
    name  = "devicePlugin.enabled"
    value = "true"
  }

  depends_on = [module.eks]
}

resource "helm_release" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler ? 1 : 0

  name       = "cluster-autoscaler"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = "9.29.0"
  namespace  = "kube-system"

  set {
    name  = "autoDiscovery.clusterName"
    value = local.cluster_name
  }

  set {
    name  = "awsRegion"
    value = var.region
  }

  set {
    name  = "rbac.serviceAccount.annotations.eks\.amazonaws\.com/role-arn"
    value = module.eks.cluster_iam_role_arn
  }

  depends_on = [module.eks]
}

resource "helm_release" "ingress_nginx" {
  count = var.enable_ingress_nginx ? 1 : 0

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

  set {
    name  = "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-type"
    value = "nlb"
  }

  set {
    name  = "controller.service.annotations.service\.beta\.kubernetes\.io/aws-load-balancer-cross-zone-load-balancing-enabled"
    value = var.enable_cross_zone_load_balancing ? "true" : "false"
  }

  depends_on = [module.eks]
}

resource "helm_release" "cert_manager" {
  count = var.enable_cert_manager ? 1 : 0

  name       = "cert-manager"
  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  version    = "v1.12.0"
  namespace  = "cert-manager"

  create_namespace = true

  set {
    name  = "installCRDs"
    value = "true"
  }

  depends_on = [module.eks]
}

resource "helm_release" "prometheus" {
  count = var.enable_prometheus ? 1 : 0

  name       = "prometheus"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = "50.0.0"
  namespace  = "monitoring"

  create_namespace = true

  values = [
    templatefile("${path.module}/templates/prometheus-values.yaml", {
      storage_class = "gp3"
    })
  ]

  depends_on = [module.eks]
}

resource "helm_release" "grafana" {
  count = var.enable_grafana ? 1 : 0

  name       = "grafana"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "grafana"
  version    = "6.58.0"
  namespace  = "monitoring"

  create_namespace = true

  set {
    name  = "adminPassword"
    value = "admin"
  }

  depends_on = [module.eks]
}

resource "helm_release" "velero" {
  count = var.enable_velero ? 1 : 0

  name       = "velero"
  repository = "https://vmware-tanzu.github.io/helm-charts"
  chart      = "velero"
  version    = "5.0.0"
  namespace  = "velero"

  create_namespace = true

  set {
    name  = "configuration.provider"
    value = "aws"
  }

  set {
    name  = "configuration.backupStorageLocation.bucket"
    value = var.backup_bucket
  }

  set {
    name  = "configuration.backupStorageLocation.config.region"
    value = var.region
  }

  depends_on = [module.eks]
}

resource "helm_release" "comfyui_engine" {
  name       = "comfyui-engine"
  repository = "oci://ghcr.io/only17teen/comfyui-engine/charts"
  chart      = "comfyui-engine"
  version    = "1.0.0"
  namespace  = "comfyui-engine"

  create_namespace = true

  values = [
    templatefile("${path.module}/templates/comfyui-values.yaml", {
      domain_name = var.domain_name
    })
  ]

  depends_on = [
    helm_release.gpu_operator,
    helm_release.ingress_nginx,
    helm_release.cert_manager
  ]
}