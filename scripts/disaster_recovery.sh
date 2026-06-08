#!/bin/bash
set -euo pipefail

# ComfyUI Engine Disaster Recovery Script
# Handles failover, restoration, and system recovery

# Configuration
PRIMARY_REGION="${PRIMARY_REGION:-us-east-1}"
BACKUP_REGIONS="${BACKUP_REGIONS:-us-west-2,eu-west-1}"
FAILOVER_STRATEGY="${FAILOVER_STRATEGY:-active-active}"
HEALTH_CHECK_INTERVAL="${HEALTH_CHECK_INTERVAL:-30}"
FAILOVER_THRESHOLD="${FAILOVER_THRESHOLD:-3}"
RECOVERY_TIMEOUT="${RECOVERY_TIMEOUT:-300}"

# Service endpoints
PRIMARY_ENDPOINT="${PRIMARY_ENDPOINT:-http://localhost:8080}"
BACKUP_ENDPOINTS="${BACKUP_ENDPOINTS:-}"

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

# Backup settings
BACKUP_BUCKET="${BACKUP_BUCKET:-comfyui-engine-backups}"
BACKUP_PREFIX="${BACKUP_PREFIX:-comfyui-engine}"
BACKUP_DESTINATION="${BACKUP_DESTINATION:-s3}"

# Notification settings
SLACK_WEBHOOK="${SLACK_WEBHOOK:-}"
PAGERDUTY_KEY="${PAGERDUTY_KEY:-}"
EMAIL_RECIPIENTS="${EMAIL_RECIPIENTS:-}"

# Logging
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LOG_FILE="${LOG_FILE:-/var/log/comfyui-dr.log}"

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

# Send notification
send_notification() {
    local severity="$1"
    local message="$2"
    
    log_info "Sending notification: $message"
    
    # Slack notification
    if [ -n "$SLACK_WEBHOOK" ]; then
        curl -s -X POST "$SLACK_WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{
                \"text\": \"ComfyUI Engine DR Alert: ${severity}\",
                \"attachments\": [{
                    \"color\": \"${severity}\",
                    \"text\": \"${message}\",
                    \"footer\": \"Disaster Recovery System\",
                    \"ts\": $(date +%s)
                }]
            }" > /dev/null 2>&1 || true
    fi
    
    # PagerDuty notification
    if [ -n "$PAGERDUTY_KEY" ] && [ "$severity" = "critical" ]; then
        curl -s -X POST "https://events.pagerduty.com/v2/enqueue" \
            -H 'Content-Type: application/json' \
            -d "{
                \"routing_key\": \"${PAGERDUTY_KEY}\",
                \"event_action\": \"trigger\",
                \"dedup_key\": \"comfyui-dr-$(date +%Y%m%d)\",
                \"payload\": {
                    \"summary\": \"${message}\",
                    \"severity\": \"critical\",
                    \"source\": \"comfyui-dr\",
                    \"component\": \"disaster-recovery\",
                    \"group\": \"comfyui-engine\",
                    \"class\": \"failover\"
                }
            }" > /dev/null 2>&1 || true
    fi
    
    # Email notification
    if [ -n "$EMAIL_RECIPIENTS" ]; then
        echo "$message" | mail -s "ComfyUI Engine DR Alert: $severity" $EMAIL_RECIPIENTS 2>&1 || true
    fi
}

# Health check function
health_check() {
    local endpoint="$1"
    local timeout="${2:-10}"
    
    log_debug "Health check: $endpoint"
    
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time "$timeout" \
        "${endpoint}/health" 2>/dev/null || echo "000")
    
    if [ "$response" = "200" ]; then
        return 0
    else
        return 1
    fi
}

# Get system status
get_system_status() {
    local endpoint="$1"
    
    log_debug "Getting system status: $endpoint"
    
    local response
    response=$(curl -s --max-time 10 "${endpoint}/api/system/info" 2>/dev/null || echo '{}')
    
    echo "$response"
}

# Check primary region
check_primary_region() {
    log_info "Checking primary region: $PRIMARY_REGION"
    
    local failures=0
    
    while [ $failures -lt $FAILOVER_THRESHOLD ]; do
        if health_check "$PRIMARY_ENDPOINT"; then
            log_info "Primary region is healthy"
            return 0
        fi
        
        failures=$((failures + 1))
        log_warn "Primary region health check failed ($failures/$FAILOVER_THRESHOLD)"
        sleep "$HEALTH_CHECK_INTERVAL"
    done
    
    log_error "Primary region is unhealthy after $FAILOVER_THRESHOLD attempts"
    return 1
}

# Check backup regions
check_backup_regions() {
    log_info "Checking backup regions..."
    
    local healthy_regions=()
    
    IFS=',' read -ra regions <<< "$BACKUP_REGIONS"
    
    for region in "${regions[@]}"; do
        local endpoint
        endpoint=$(echo "$BACKUP_ENDPOINTS" | grep -o "${region}=[^,]*" | cut -d= -f2)
        
        if [ -z "$endpoint" ]; then
            endpoint="http://comfyui-engine-${region}.internal:8080"
        fi
        
        log_info "Checking region: $region ($endpoint)"
        
        if health_check "$endpoint"; then
            log_info "Region $region is healthy"
            healthy_regions+=("$region:$endpoint")
        else
            log_warn "Region $region is unhealthy"
        fi
    done
    
    if [ ${#healthy_regions[@]} -eq 0 ]; then
        log_error "No healthy backup regions found"
        return 1
    fi
    
    echo "${healthy_regions[@]}"
    return 0
}

# Perform failover
perform_failover() {
    local target_region="$1"
    local target_endpoint="$2"
    
    log_info "Performing failover to $target_region ($target_endpoint)"
    
    send_notification "critical" "Failover initiated to ${target_region}"
    
    # Update DNS/load balancer
    update_traffic_routing "$target_region" "$target_endpoint"
    
    # Update configuration
    update_configuration "$target_region"
    
    # Verify failover
    local attempts=0
    local max_attempts=10
    
    while [ $attempts -lt $max_attempts ]; do
        if health_check "$target_endpoint"; then
            log_info "Failover to $target_region successful"
            send_notification "info" "Failover to ${target_region} successful"
            return 0
        fi
        
        attempts=$((attempts + 1))
        log_warn "Failover verification attempt $attempts/$max_attempts failed"
        sleep 10
    done
    
    log_error "Failover to $target_region failed"
    send_notification "critical" "Failover to ${target_region} failed"
    return 1
}

# Update traffic routing
update_traffic_routing() {
    local region="$1"
    local endpoint="$2"
    
    log_info "Updating traffic routing to $region"
    
    # Update Kubernetes ingress
    if command -v kubectl >/dev/null 2>&1; then
        kubectl patch ingress comfyui-engine \
            -n comfyui-engine \
            --type merge \
            -p "{
                \"metadata\": {
                    \"annotations\": {
                        \"nginx.ingress.kubernetes.io/upstream-vhost\": \"${endpoint#http://}\"
                    }
                }
            }" 2>&1 || true
    fi
    
    # Update AWS Route53
    if command -v aws >/dev/null 2>&1; then
        aws route53 change-resource-record-sets \
            --hosted-zone-id "$HOSTED_ZONE_ID" \
            --change-batch "{
                \"Changes\": [{
                    \"Action\": \"UPSERT\",
                    \"ResourceRecordSet\": {
                        \"Name\": \"comfyui-engine.example.com\",
                        \"Type\": \"CNAME\",
                        \"TTL\": 60,
                        \"ResourceRecords\": [{\"Value\": \"${endpoint#http://}\"}]
                    }
                }]
            }" 2>&1 || true
    fi
    
    # Update Cloudflare
    if [ -n "$CLOUDFLARE_API_TOKEN" ]; then
        curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${CLOUDFLARE_RECORD_ID}" \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{
                \"type\": \"CNAME\",
                \"name\": \"comfyui-engine\",
                \"content\": \"${endpoint#http://}\",
                \"ttl\": 60,
                \"proxied\": false
            }" > /dev/null 2>&1 || true
    fi
}

# Update configuration
update_configuration() {
    local region="$1"
    
    log_info "Updating configuration for region: $region"
    
    # Update environment variables
    export COMFYUI_PRIMARY_REGION="$region"
    export COMFYUI_FAILOVER_ACTIVE="true"
    
    # Update config file
    if [ -f "/app/config/production.env" ]; then
        sed -i "s/COMFYUI_PRIMARY_REGION=.*/COMFYUI_PRIMARY_REGION=${region}/" /app/config/production.env
        sed -i "s/COMFYUI_FAILOVER_ACTIVE=.*/COMFYUI_FAILOVER_ACTIVE=true/" /app/config/production.env
    fi
    
    # Restart services if needed
    if [ -f "/app/scripts/restart.sh" ]; then
        /app/scripts/restart.sh 2>&1 || true
    fi
}

# Restore from backup
restore_from_backup() {
    local backup_file="$1"
    local target_region="${2:-$PRIMARY_REGION}"
    
    log_info "Restoring from backup: $backup_file"
    
    if [ ! -f "$backup_file" ]; then
        log_error "Backup file not found: $backup_file"
        return 1
    fi
    
    # Decrypt if needed
    local decrypted_file="$backup_file"
    if [[ "$backup_file" == *.enc ]]; then
        decrypted_file="${backup_file%.enc}"
        openssl enc -d -aes-256-cbc -in "$backup_file" -out "$decrypted_file" -pass pass:"$ENCRYPTION_KEY"
    fi
    
    # Extract backup
    local restore_dir="/tmp/comfyui-restore-$(date +%s)"
    mkdir -p "$restore_dir"
    
    case "$BACKUP_COMPRESSION" in
        gzip)
            tar -xzf "$decrypted_file" -C "$restore_dir"
            ;;
        bzip2)
            tar -xjf "$decrypted_file" -C "$restore_dir"
            ;;
        xz)
            tar -xJf "$decrypted_file" -C "$restore_dir"
            ;;
        *)
            tar -xzf "$decrypted_file" -C "$restore_dir"
            ;;
    esac
    
    # Restore models
    if [ -d "${restore_dir}/models" ]; then
        log_info "Restoring models..."
        rsync -av "${restore_dir}/models/" "$MODELS_DIR/"
    fi
    
    # Restore outputs
    if [ -d "${restore_dir}/outputs" ]; then
        log_info "Restoring outputs..."
        rsync -av "${restore_dir}/outputs/" "$OUTPUTS_DIR/"
    fi
    
    # Restore configuration
    if [ -d "${restore_dir}/config" ]; then
        log_info "Restoring configuration..."
        rsync -av "${restore_dir}/config/" "$CONFIG_DIR/"
    fi
    
    # Restore checkpoints
    if [ -d "${restore_dir}/checkpoints" ]; then
        log_info "Restoring checkpoints..."
        rsync -av "${restore_dir}/checkpoints/" "$CHECKPOINTS_DIR/"
    fi
    
    # Restore database
    if [ -f "${restore_dir}/database.dump" ]; then
        log_info "Restoring database..."
        PGPASSWORD="$DB_PASSWORD" pg_restore \
            -h "$DB_HOST" \
            -p "$DB_PORT" \
            -U "$DB_USER" \
            -d "$DB_NAME" \
            -c \
            "${restore_dir}/database.dump"
    fi
    
    # Restore Redis
    if [ -f "${restore_dir}/redis.rdb" ]; then
        log_info "Restoring Redis..."
        if [ -n "$REDIS_PASSWORD" ]; then
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" \
                --rdb "${restore_dir}/redis.rdb"
        else
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" \
                --rdb "${restore_dir}/redis.rdb"
        fi
    fi
    
    # Cleanup
    rm -rf "$restore_dir"
    
    log_info "Restore completed"
}

# Download backup from cloud storage
download_backup() {
    local backup_key="$1"
    local output_file="$2"
    
    log_info "Downloading backup: $backup_key"
    
    case "$BACKUP_DESTINATION" in
        s3)
            aws s3 cp "s3://${BACKUP_BUCKET}/${backup_key}" "$output_file"
            ;;
        gcs)
            gsutil cp "gs://${BACKUP_BUCKET}/${backup_key}" "$output_file"
            ;;
        azure)
            az storage blob download \
                --account-name "$AZURE_STORAGE_ACCOUNT" \
                --container-name "$BACKUP_BUCKET" \
                --name "$backup_key" \
                --file "$output_file"
            ;;
        local)
            cp "${BACKUP_BUCKET}/${backup_key}" "$output_file"
            ;;
        *)
            log_error "Unknown backup destination: $BACKUP_DESTINATION"
            return 1
            ;;
    esac
    
    log_info "Backup downloaded: $output_file"
}

# Find latest backup
find_latest_backup() {
    local backup_type="${1:-full}"
    
    log_info "Finding latest $backup_type backup..."
    
    local latest_backup=""
    
    case "$BACKUP_DESTINATION" in
        s3)
            latest_backup=$(aws s3 ls "s3://${BACKUP_BUCKET}/${BACKUP_PREFIX}/${backup_type}/" \
                --recursive | sort | tail -1 | awk '{print $4}')
            ;;
        gcs)
            latest_backup=$(gsutil ls "gs://${BACKUP_BUCKET}/${BACKUP_PREFIX}/${backup_type}/" | sort | tail -1)
            latest_backup="${latest_backup#gs://${BACKUP_BUCKET}/}"
            ;;
        azure)
            latest_backup=$(az storage blob list \
                --account-name "$AZURE_STORAGE_ACCOUNT" \
                --container-name "$BACKUP_BUCKET" \
                --prefix "${BACKUP_PREFIX}/${backup_type}/" \
                --query "[-1].name" -o tsv)
            ;;
        local)
            latest_backup=$(ls -1 "${BACKUP_BUCKET}/${BACKUP_PREFIX}/${backup_type}/" | sort | tail -1)
            latest_backup="${BACKUP_PREFIX}/${backup_type}/${latest_backup}"
            ;;
    esac
    
    if [ -z "$latest_backup" ]; then
        log_error "No backup found"
        return 1
    fi
    
    log_info "Latest backup: $latest_backup"
    echo "$latest_backup"
}

# Recovery procedure
perform_recovery() {
    local recovery_type="${1:-auto}"
    
    log_info "Starting recovery procedure: $recovery_type"
    
    send_notification "critical" "Recovery procedure initiated: ${recovery_type}"
    
    case "$recovery_type" in
        auto)
            # Automatic recovery - find healthy region and failover
            if ! check_primary_region; then
                local healthy_regions
                healthy_regions=$(check_backup_regions)
                
                if [ $? -eq 0 ]; then
                    local first_region=$(echo "$healthy_regions" | awk '{print $1}')
                    local region_name=$(echo "$first_region" | cut -d: -f1)
                    local region_endpoint=$(echo "$first_region" | cut -d: -f2-)
                    
                    perform_failover "$region_name" "$region_endpoint"
                else
                    log_error "No healthy regions available for failover"
                    send_notification "critical" "No healthy regions available for failover"
                    return 1
                fi
            fi
            ;;
        
        manual)
            # Manual recovery - restore from backup
            log_info "Manual recovery - please specify backup file or region"
            ;;
        
        backup)
            # Restore from latest backup
            local latest_backup
            latest_backup=$(find_latest_backup)
            
            if [ $? -eq 0 ]; then
                local temp_file="/tmp/backup-$(date +%s).tar.gz"
                download_backup "$latest_backup" "$temp_file"
                restore_from_backup "$temp_file"
                rm -f "$temp_file"
            else
                log_error "Failed to find latest backup"
                return 1
            fi
            ;;
        
        region)
            # Failover to specific region
            local target_region="$2"
            local target_endpoint="$3"
            
            if [ -z "$target_region" ] || [ -z "$target_endpoint" ]; then
                log_error "Target region and endpoint required for region recovery"
                return 1
            fi
            
            perform_failover "$target_region" "$target_endpoint"
            ;;
        
        *)
            log_error "Unknown recovery type: $recovery_type"
            return 1
            ;;
    esac
    
    log_info "Recovery procedure completed"
    send_notification "info" "Recovery procedure completed: ${recovery_type}"
}

# Monitor and auto-recover
monitor_and_recover() {
    log_info "Starting monitoring and auto-recovery"
    
    while true; do
        if ! check_primary_region; then
            log_warn "Primary region unhealthy, initiating auto-recovery"
            perform_recovery "auto"
        fi
        
        sleep "$HEALTH_CHECK_INTERVAL"
    done
}

# Show usage
usage() {
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo
    echo "Commands:"
    echo "  monitor          Start monitoring and auto-recovery"
    echo "  check            Check system health"
    echo "  failover         Perform failover to backup region"
    echo "  recover          Perform recovery procedure"
    echo "  restore          Restore from backup"
    echo "  status           Show system status"
    echo
    echo "Options:"
    echo "  -r, --region REGION     Target region for failover"
    echo "  -e, --endpoint URL      Target endpoint for failover"
    echo "  -b, --backup FILE       Backup file to restore from"
    echo "  -t, --type TYPE         Recovery type (auto, manual, backup, region)"
    echo "  -h, --help              Show this help message"
}

# Parse arguments
COMMAND=""
TARGET_REGION=""
TARGET_ENDPOINT=""
BACKUP_FILE=""
RECOVERY_TYPE="auto"

while [[ $# -gt 0 ]]; do
    case $1 in
        monitor|check|failover|recover|restore|status)
            COMMAND="$1"
            shift
            ;;
        -r|--region)
            TARGET_REGION="$2"
            shift 2
            ;;
        -e|--endpoint)
            TARGET_ENDPOINT="$2"
            shift 2
            ;;
        -b|--backup)
            BACKUP_FILE="$2"
            shift 2
            ;;
        -t|--type)
            RECOVERY_TYPE="$2"
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
    
    log_info "ComfyUI Engine Disaster Recovery"
    log_info "Command: ${COMMAND:-monitor}"
    log_info "Primary region: $PRIMARY_REGION"
    log_info "Backup regions: $BACKUP_REGIONS"
    
    case "${COMMAND:-monitor}" in
        monitor)
            monitor_and_recover
            ;;
        check)
            check_primary_region
            check_backup_regions
            ;;
        failover)
            if [ -n "$TARGET_REGION" ] && [ -n "$TARGET_ENDPOINT" ]; then
                perform_failover "$TARGET_REGION" "$TARGET_ENDPOINT"
            else
                log_error "Target region and endpoint required for failover"
                usage
                exit 1
            fi
            ;;
        recover)
            perform_recovery "$RECOVERY_TYPE" "$TARGET_REGION" "$TARGET_ENDPOINT"
            ;;
        restore)
            if [ -n "$BACKUP_FILE" ]; then
                restore_from_backup "$BACKUP_FILE" "$TARGET_REGION"
            else
                local latest_backup
                latest_backup=$(find_latest_backup)
                
                if [ $? -eq 0 ]; then
                    local temp_file="/tmp/backup-$(date +%s).tar.gz"
                    download_backup "$latest_backup" "$temp_file"
                    restore_from_backup "$temp_file" "$TARGET_REGION"
                    rm -f "$temp_file"
                else
                    log_error "Failed to find latest backup"
                    exit 1
                fi
            fi
            ;;
        status)
            log_info "System Status"
            log_info "Primary region: $PRIMARY_REGION"
            
            if health_check "$PRIMARY_ENDPOINT"; then
                log_info "Primary region: HEALTHY"
            else
                log_warn "Primary region: UNHEALTHY"
            fi
            
            local status
            status=$(get_system_status "$PRIMARY_ENDPOINT")
            log_info "System info: $status"
            ;;
        *)
            log_error "Unknown command: $COMMAND"
            usage
            exit 1
            ;;
    esac
}

main