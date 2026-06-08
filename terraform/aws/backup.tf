# Backup configuration for AWS

# S3 bucket for backups
resource "aws_s3_bucket" "backups" {
  count  = var.enable_backup ? 1 : 0
  bucket = "${var.cluster_name}-backups-${data.aws_caller_identity.current.account_id}"

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-backups"
  })
}

resource "aws_s3_bucket_versioning" "backups" {
  count  = var.enable_backup ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  count  = var.enable_backup ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  rule {
    id     = "backup-retention"
    status = "Enabled"

    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

# IAM role for backup
resource "aws_iam_role" "backup" {
  count = var.enable_backup ? 1 : 0
  name  = "${var.cluster_name}-backup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "backup.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "backup" {
  count = var.enable_backup ? 1 : 0
  name  = "${var.cluster_name}-backup-policy"
  role  = aws_iam_role.backup[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.backups[0].arn,
          "${aws_s3_bucket.backups[0].arn}/*"
        ]
      }
    ]
  })
}

# EBS backup vault
resource "aws_backup_vault" "main" {
  count = var.enable_backup ? 1 : 0
  name  = "${var.cluster_name}-backup-vault"

  tags = var.tags
}

resource "aws_backup_plan" "main" {
  count = var.enable_backup ? 1 : 0
  name  = "${var.cluster_name}-backup-plan"

  rule {
    rule_name         = "daily-backup"
    target_vault_name = aws_backup_vault.main[0].name
    schedule          = "cron(0 2 * * ? *)"

    lifecycle {
      delete_after = 30
    }
  }

  tags = var.tags
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
    value = "aws"
  }

  set {
    name  = "configuration.backupStorageLocation.bucket"
    value = aws_s3_bucket.backups[0].id
  }

  set {
    name  = "configuration.backupStorageLocation.config.region"
    value = var.region
  }

  set {
    name  = "configuration.volumeSnapshotLocation.config.region"
    value = var.region
  }

  set {
    name  = "initContainers[0].name"
    value = "velero-plugin-for-aws"
  }

  set {
    name  = "initContainers[0].image"
    value = "velero/velero-plugin-for-aws:v1.7.0"
  }

  set {
    name  = "initContainers[0].volumeMounts[0].mountPath"
    value = "/target"
  }

  set {
    name  = "initContainers[0].volumeMounts[0].name"
    value = "plugins"
  }

  depends_on = [module.eks]
}
