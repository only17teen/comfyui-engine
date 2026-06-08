# Backup configuration for Azure

# Storage account for backups
resource "azurerm_storage_account" "backups" {
  count                    = var.enable_backup ? 1 : 0
  name                     = "${var.cluster_name}backups"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
  min_tls_version          = "TLS1_2"

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 30
    }
  }

  tags = var.tags
}

resource "azurerm_storage_container" "backups" {
  count                 = var.enable_backup ? 1 : 0
  name                  = "backups"
  storage_account_name  = azurerm_storage_account.backups[0].name
  container_access_type = "private"
}

# Velero for Kubernetes backups
resource "helm_release" "velero" {
  count = var.enable_backup ? 1 : 0

  name       = "velero"
  repository = "https://vmware-tanzu.github.io/helm-charts"
  chart      = "velero"
  version    = "5.0.0"
  namespace  = "velero"

  create_namespace = true

  set {
    name  = "configuration.provider"
    value = "azure"
  }

  set {
    name  = "configuration.backupStorageLocation.bucket"
    value = azurerm_storage_container.backups[0].name
  }

  set {
    name  = "configuration.backupStorageLocation.config.storageAccount"
    value = azurerm_storage_account.backups[0].name
  }

  set {
    name  = "configuration.backupStorageLocation.config.resourceGroup"
    value = azurerm_resource_group.main.name
  }

  set {
    name  = "configuration.backupStorageLocation.config.subscriptionId"
    value = data.azurerm_subscription.current.subscription_id
  }

  set {
    name  = "initContainers[0].name"
    value = "velero-plugin-for-azure"
  }

  set {
    name  = "initContainers[0].image"
    value = "velero/velero-plugin-for-azure:v1.7.0"
  }

  set {
    name  = "initContainers[0].volumeMounts[0].mountPath"
    value = "/target"
  }

  set {
    name  = "initContainers[0].volumeMounts[0].name"
    value = "plugins"
  }

  depends_on = [azurerm_kubernetes_cluster.main]
}
