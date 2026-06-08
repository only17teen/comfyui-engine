# Backup configuration for GCP

# GCS bucket for backups
resource "google_storage_bucket" "backups" {
  count         = var.enable_backup ? 1 : 0
  name          = "${var.cluster_name}-backups-${data.google_project.current.number}"
  location      = var.region
  project       = var.project_id
  force_destroy = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 30
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      num_newer_versions = 7
    }
  }

  labels = var.labels
}

# Service account for backups
resource "google_service_account" "backup" {
  count      = var.enable_backup ? 1 : 0
  account_id = "${var.cluster_name}-backup"
  project    = var.project_id
  display_name = "ComfyUI Engine Backup Service Account"
}

resource "google_storage_bucket_iam_member" "backup" {
  count  = var.enable_backup ? 1 : 0
  bucket = google_storage_bucket.backups[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backup[0].email}"
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
    value = "gcp"
  }

  set {
    name  = "configuration.backupStorageLocation.bucket"
    value = google_storage_bucket.backups[0].name
  }

  set {
    name  = "configuration.backupStorageLocation.config.serviceAccount"
    value = google_service_account.backup[0].email
  }

  set {
    name  = "initContainers[0].name"
    value = "velero-plugin-for-gcp"
  }

  set {
    name  = "initContainers[0].image"
    value = "velero/velero-plugin-for-gcp:v1.7.0"
  }

  set {
    name  = "initContainers[0].volumeMounts[0].mountPath"
    value = "/target"
  }

  set {
    name  = "initContainers[0].volumeMounts[0].name"
    value = "plugins"
  }

  depends_on = [google_container_cluster.main]
}
