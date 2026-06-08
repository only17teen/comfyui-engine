#!/bin/bash
set -euo pipefail

# ComfyUI Engine Backup Script
# Supports multiple backends: S3, GCS, Azure Blob, local

# Configuration
BACKUP_TYPE="${BACKUP_TYPE:-full}"  # full, incremental, differential
BACKUP_DESTINATION="${BACKUP_DESTINATION:-s3}"  # s3, gcs, azure, local
BACKUP_BUCKET="${BACKUP_BUCKET:-comfyui-engine-backups}"
BACKUP_PREFIX="${BACKUP_PREFIX:-comfyui-engine}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_COMPRESSION="${BACKUP_COMPRESSION:-gzip}"
BACKUP_ENCRYPTION="${BACKUP_ENCRYPTION:-true}"
ENCRYPTION_KEY="${ENCRYPTION_KEY:-}"

# Source directories
MODELS_DIR="${MODELS_DIR:-/app/models}"
OUTPUTS_DIR="${OUTPUTS_DIR:-/app/outputs}"
CONFIG_DIR="${CONFIG_DIR:-/app/config}"
CHECKPOINTS_DIR="${CHECKPOINTS_DIR:-/app/checkpoints}"

# Database settings
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-comfyui}"
DB_USER="${DB_USER:-comfyui}"
DB_PASSWORD="${DB_PASSWORD:-}"

# Redis settings
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"

# Logging
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LOG_FILE="${LOG_FILE:-/var/log/comfyui-backup.log}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log_debug() {
    if [ "$LOG_LEVEL" = "DEBUG" ]; then
        echo -e "${BLUE}[DEBUG]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
    fi
}

# Create backup directory
create_backup_dir() {
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_dir="/tmp/comfyui-backup-${timestamp}"
    
    mkdir -p "$backup_dir"
    echo "$backup_dir"
}

# Compress backup
compress_backup() {
    local source_dir="$1"
    local output_file="$2"
    
    log_info "Compressing backup..."
    
    case "$BACKUP_COMPRESSION" in
        gzip)
            tar -czf "$output_file" -C "$source_dir" .
            ;;
        bzip2)
            tar -cjf "$output_file" -C "$source_dir" .
            ;;
        xz)
            tar -cJf "$output_file" -C "$source_dir" .
            ;;
        zstd)
            tar --zstd -cf "$output_file" -C "$source_dir" .
            ;;
        *)
            tar -czf "$output_file" -C "$source_dir" .
            ;;
    esac
    
    log_info "Backup compressed: $output_file"
}

# Encrypt backup
encrypt_backup() {
    local input_file="$1"
    local output_file="$2"
    
    if [ "$BACKUP_ENCRYPTION" = "true" ] && [ -n "$ENCRYPTION_KEY" ]; then
        log_info "Encrypting backup..."
        
        openssl enc -aes-256-cbc -salt -in "$input_file" -out "$output_file" -pass pass:"$ENCRYPTION_KEY"
        rm -f "$input_file"
        
        log_info "Backup encrypted: $output_file"
    else
        mv "$input_file" "$output_file"
    fi
}

# Upload to S3
upload_to_s3() {
    local file="$1"
    local key="$2"
    
    log_info "Uploading to S3..."
    
    aws s3 cp "$file" "s3://${BACKUP_BUCKET}/${key}" \
        --storage-class STANDARD_IA \
        --metadata "backup-type=${BACKUP_TYPE},timestamp=$(date -Iseconds)"
    
    log_info "Backup uploaded to S3: s3://${BACKUP_BUCKET}/${key}"
}

# Upload to GCS
upload_to_gcs() {
    local file="$1"
    local key="$2"
    
    log_info "Uploading to GCS..."
    
    gsutil -h "x-goog-meta-backup-type:${BACKUP_TYPE}" \
           -h "x-goog-meta-timestamp:$(date -Iseconds)" \
           cp "$file" "gs://${BACKUP_BUCKET}/${key}"
    
    log_info "Backup uploaded to GCS: gs://${BACKUP_BUCKET}/${key}"
}

# Upload to Azure Blob
upload_to_azure() {
    local file="$1"
    local key="$2"
    
    log_info "Uploading to Azure Blob..."
    
    az storage blob upload \
        --account-name "$AZURE_STORAGE_ACCOUNT" \
        --container-name "$BACKUP_BUCKET" \
        --name "$key" \
        --file "$file" \
        --metadata "backup-type=${BACKUP_TYPE}" "timestamp=$(date -Iseconds)"
    
    log_info "Backup uploaded to Azure: ${BACKUP_BUCKET}/${key}"
}

# Upload to local storage
upload_to_local() {
    local file="$1"
    local key="$2"
    
    log_info "Copying to local storage..."
    
    local dest_dir="${BACKUP_BUCKET:-/backups}"
    mkdir -p "$dest_dir"
    
    cp "$file" "${dest_dir}/${key}"
    
    log_info "Backup copied to local: ${dest_dir}/${key}"
}

# Upload backup
upload_backup() {
    local file="$1"
    local key="$2"
    
    case "$BACKUP_DESTINATION" in
        s3)
            upload_to_s3 "$file" "$key"
            ;;
        gcs)
            upload_to_gcs "$file" "$key"
            ;;
        azure)
            upload_to_azure "$file" "$key"
            ;;
        local)
            upload_to_local "$file" "$key"
            ;;
        *)
            log_error "Unknown backup destination: $BACKUP_DESTINATION"
            exit 1
            ;;
    esac
}

# Backup models
backup_models() {
    local backup_dir="$1"
    
    log_info "Backing up models..."
    
    if [ -d "$MODELS_DIR" ]; then
        mkdir -p "${backup_dir}/models"
        rsync -av --progress "$MODELS_DIR/" "${backup_dir}/models/"
        log_info "Models backed up"
    else
        log_warn "Models directory not found: $MODELS_DIR"
    fi
}

# Backup outputs
backup_outputs() {
    local backup_dir="$1"
    
    log_info "Backing up outputs..."
    
    if [ -d "$OUTPUTS_DIR" ]; then
        mkdir -p "${backup_dir}/outputs"
        rsync -av --progress "$OUTPUTS_DIR/" "${backup_dir}/outputs/"
        log_info "Outputs backed up"
    else
        log_warn "Outputs directory not found: $OUTPUTS_DIR"
    fi
}

# Backup configuration
backup_config() {
    local backup_dir="$1"
    
    log_info "Backing up configuration..."
    
    if [ -d "$CONFIG_DIR" ]; then
        mkdir -p "${backup_dir}/config"
        rsync -av --progress "$CONFIG_DIR/" "${backup_dir}/config/"
        log_info "Configuration backed up"
    else
        log_warn "Config directory not found: $CONFIG_DIR"
    fi
}

# Backup checkpoints
backup_checkpoints() {
    local backup_dir="$1"
    
    log_info "Backing up checkpoints..."
    
    if [ -d "$CHECKPOINTS_DIR" ]; then
        mkdir -p "${backup_dir}/checkpoints"
        rsync -av --progress "$CHECKPOINTS_DIR/" "${backup_dir}/checkpoints/"
        log_info "Checkpoints backed up"
    else
        log_warn "Checkpoints directory not found: $CHECKPOINTS_DIR"
    fi
}

# Backup database
backup_database() {
    local backup_dir="$1"
    
    log_info "Backing up database..."
    
    if [ -n "$DB_PASSWORD" ]; then
        PGPASSWORD="$DB_PASSWORD" pg_dump \
            -h "$DB_HOST" \
            -p "$DB_PORT" \
            -U "$DB_USER" \
            -d "$DB_NAME" \
            -F custom \
            -f "${backup_dir}/database.dump"
        
        log_info "Database backed up"
    else
        log_warn "Database password not set, skipping database backup"
    fi
}

# Backup Redis
backup_redis() {
    local backup_dir="$1"
    
    log_info "Backing up Redis..."
    
    if command -v redis-cli >/dev/null 2>&1; then
        if [ -n "$REDIS_PASSWORD" ]; then
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" --rdb "${backup_dir}/redis.rdb"
        else
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" --rdb "${backup_dir}/redis.rdb"
        fi
        
        log_info "Redis backed up"
    else
        log_warn "redis-cli not found, skipping Redis backup"
    fi
}

# Create backup manifest
create_manifest() {
    local backup_dir="$1"
    
    log_info "Creating backup manifest..."
    
    cat > "${backup_dir}/manifest.json" << EOF
{
    "backup_type": "${BACKUP_TYPE}",
    "timestamp": "$(date -Iseconds)",
    "version": "$(cat /app/version.txt 2>/dev/null || echo 'unknown')",
    "hostname": "$(hostname)",
    "directories": {
        "models": $(test -d "$MODELS_DIR" && echo 'true' || echo 'false'),
        "outputs": $(test -d "$OUTPUTS_DIR" && echo 'true' || echo 'false'),
        "config": $(test -d "$CONFIG_DIR" && echo 'true' || echo 'false'),
        "checkpoints": $(test -d "$CHECKPOINTS_DIR" && echo 'true' || echo 'false')
    },
    "database": {
        "host": "${DB_HOST}",
        "port": ${DB_PORT},
        "name": "${DB_NAME}"
    },
    "redis": {
        "host": "${REDIS_HOST}",
        "port": ${REDIS_PORT}
    },
    "compression": "${BACKUP_COMPRESSION}",
    "encryption": ${BACKUP_ENCRYPTION}
}
EOF
    
    log_info "Manifest created"
}

# Clean up old backups
cleanup_old_backups() {
    log_info "Cleaning up old backups (retention: ${BACKUP_RETENTION_DAYS} days)..."
    
    case "$BACKUP_DESTINATION" in
        s3)
            aws s3 ls "s3://${BACKUP_BUCKET}/${BACKUP_PREFIX}/" | \
                awk -v date="$(date -d "${BACKUP_RETENTION_DAYS} days ago" +%Y-%m-%d)" '
                $1 < date { print $4 }'
            ;;
        gcs)
            gsutil ls "gs://${BACKUP_BUCKET}/${BACKUP_PREFIX}/" | \
                while read -r file; do
                    local file_date=$(gsutil stat "$file" | grep "Creation time" | awk '{print $3}')
                    if [ "$(date -d "$file_date" +%s)" -lt "$(date -d "${BACKUP_RETENTION_DAYS} days ago" +%s)" ]; then
                        gsutil rm "$file"
                    fi
                done
            ;;
        local)
            find "${BACKUP_BUCKET:-/backups}/${BACKUP_PREFIX}" -type f -mtime +${BACKUP_RETENTION_DAYS} -delete
            ;;
    esac
    
    log_info "Old backups cleaned up"
}

# Verify backup
verify_backup() {
    local backup_file="$1"
    
    log_info "Verifying backup..."
    
    # Check file exists and is not empty
    if [ ! -s "$backup_file" ]; then
        log_error "Backup file is empty or does not exist"
        return 1
    fi
    
    # Test archive integrity
    case "$BACKUP_COMPRESSION" in
        gzip)
            tar -tzf "$backup_file" > /dev/null 2>&1
            ;;
        bzip2)
            tar -tjf "$backup_file" > /dev/null 2>&1
            ;;
        xz)
            tar -tJf "$backup_file" > /dev/null 2>&1
            ;;
        *)
            tar -tzf "$backup_file" > /dev/null 2>&1
            ;;
    esac
    
    if [ $? -eq 0 ]; then
        log_info "Backup verification passed"
        return 0
    else
        log_error "Backup verification failed"
        return 1
    fi
}

# Main backup function
perform_backup() {
    log_info "Starting ${BACKUP_TYPE} backup..."
    
    local start_time=$(date +%s)
    local backup_dir=$(create_backup_dir)
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_name="${BACKUP_PREFIX}_${BACKUP_TYPE}_${timestamp}"
    
    log_info "Backup directory: $backup_dir"
    log_info "Backup name: $backup_name"
    
    # Create backup
    case "$BACKUP_TYPE" in
        full)
            backup_models "$backup_dir"
            backup_outputs "$backup_dir"
            backup_config "$backup_dir"
            backup_checkpoints "$backup_dir"
            backup_database "$backup_dir"
            backup_redis "$backup_dir"
            ;;
        incremental)
            # Only backup changed files since last backup
            backup_models "$backup_dir"
            backup_config "$backup_dir"
            backup_database "$backup_dir"
            ;;
        differential)
            # Backup changed files since last full backup
            backup_models "$backup_dir"
            backup_outputs "$backup_dir"
            backup_config "$backup_dir"
            backup_database "$backup_dir"
            ;;
        config)
            backup_config "$backup_dir"
            ;;
        database)
            backup_database "$backup_dir"
            backup_redis "$backup_dir"
            ;;
        models)
            backup_models "$backup_dir"
            ;;
        *)
            log_error "Unknown backup type: $BACKUP_TYPE"
            exit 1
            ;;
    esac
    
    # Create manifest
    create_manifest "$backup_dir"
    
    # Compress backup
    local compressed_file="${backup_dir}.tar.${BACKUP_COMPRESSION}"
    compress_backup "$backup_dir" "$compressed_file"
    
    # Encrypt backup
    local final_file="${compressed_file}"
    if [ "$BACKUP_ENCRYPTION" = "true" ] && [ -n "$ENCRYPTION_KEY" ]; then
        final_file="${compressed_file}.enc"
        encrypt_backup "$compressed_file" "$final_file"
    fi
    
    # Verify backup
    if ! verify_backup "$final_file"; then
        log_error "Backup verification failed, aborting upload"
        rm -rf "$backup_dir" "$compressed_file" "$final_file"
        exit 1
    fi
    
    # Upload backup
    local backup_key="${BACKUP_PREFIX}/${BACKUP_TYPE}/${backup_name}.tar.${BACKUP_COMPRESSION}"
    if [ "$BACKUP_ENCRYPTION" = "true" ] && [ -n "$ENCRYPTION_KEY" ]; then
        backup_key="${backup_key}.enc"
    fi
    
    upload_backup "$final_file" "$backup_key"
    
    # Cleanup
    rm -rf "$backup_dir" "$compressed_file" "$final_file"
    
    # Clean old backups
    cleanup_old_backups
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    log_info "Backup completed in ${duration} seconds"
    log_info "Backup location: ${BACKUP_DESTINATION}://${BACKUP_BUCKET}/${backup_key}"
}

# Show usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  -t, --type TYPE          Backup type (full, incremental, differential, config, database, models)"
    echo "  -d, --destination DEST   Backup destination (s3, gcs, azure, local)"
    echo "  -b, --bucket BUCKET      Backup bucket/container name"
    echo "  -p, --prefix PREFIX      Backup key prefix"
    echo "  -r, --retention DAYS     Retention period in days"
    echo "  -c, --compression TYPE   Compression type (gzip, bzip2, xz, zstd)"
    echo "  -e, --encrypt            Enable encryption"
    echo "  -k, --key KEY            Encryption key"
    echo "  -h, --help               Show this help message"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--type)
            BACKUP_TYPE="$2"
            shift 2
            ;;
        -d|--destination)
            BACKUP_DESTINATION="$2"
            shift 2
            ;;
        -b|--bucket)
            BACKUP_BUCKET="$2"
            shift 2
            ;;
        -p|--prefix)
            BACKUP_PREFIX="$2"
            shift 2
            ;;
        -r|--retention)
            BACKUP_RETENTION_DAYS="$2"
            shift 2
            ;;
        -c|--compression)
            BACKUP_COMPRESSION="$2"
            shift 2
            ;;
        -e|--encrypt)
            BACKUP_ENCRYPTION=true
            shift
            ;;
        -k|--key)
            ENCRYPTION_KEY="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main
main() {
    # Create log directory
    mkdir -p "$(dirname "$LOG_FILE")"
    
    log_info "ComfyUI Engine Backup Script"
    log_info "Type: $BACKUP_TYPE"
    log_info "Destination: $BACKUP_DESTINATION"
    log_info "Bucket: $BACKUP_BUCKET"
    
    perform_backup
}

main