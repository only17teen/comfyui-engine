#!/bin/bash
# ComfyUI Engine v2.0 - Installation script for Arch Linux
# Usage: sudo ./install.sh [username]

set -euo pipefail

ENGINE_USER="${1:-comfyui-engine}"
ENGINE_DIR="/opt/comfyui-engine"
ENGINE_DATA="/var/lib/comfyui-engine"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}ComfyUI Engine v2.0 - Arch Linux Installer${NC}"
echo "=========================================="

# Check root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    exit 1
fi

# Check pacman
if ! command -v pacman &> /dev/null; then
    echo -e "${RED}Error: This script is for Arch Linux only${NC}"
    exit 1
fi

echo "Installing dependencies..."
pacman -S --needed --noconfirm python python-pip git curl

echo "Creating user: $ENGINE_USER"
if ! id "$ENGINE_USER" &> /dev/null; then
    useradd -r -s /bin/false -d "$ENGINE_DATA" -m "$ENGINE_USER"
fi

echo "Setting up directories..."
mkdir -p "$ENGINE_DIR"
mkdir -p "$ENGINE_DATA"/{output_models,logs,sessions,workflows,config}
chown -R "$ENGINE_USER:$ENGINE_USER" "$ENGINE_DATA"

echo "Copying engine files..."
# Assuming script is run from engine directory
if [[ -d "engine" && -f "main.py" ]]; then
    cp -r engine main.py pyproject.toml README.md "$ENGINE_DIR/"
    cp -r config/prompts.yaml "$ENGINE_DATA/config/"
else
    echo -e "${YELLOW}Warning: Engine files not found in current directory${NC}"
    echo "Please copy files manually to $ENGINE_DIR"
fi

echo "Setting up Python virtual environment..."
cd "$ENGINE_DIR"
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install aiohttp pyyaml pydantic

echo "Installing engine package..."
pip install -e .

echo "Setting permissions..."
chown -R "$ENGINE_USER:$ENGINE_USER" "$ENGINE_DIR"
chmod 750 "$ENGINE_DIR"
chmod 750 "$ENGINE_DATA"

echo "Installing systemd service..."
cp systemd/comfyui-engine@.service /etc/systemd/system/
systemctl daemon-reload

echo "Creating logrotate config..."
cat > /etc/logrotate.d/comfyui-engine <> 'EOF'
/var/lib/comfyui-engine/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 comfyui-engine comfyui-engine
    sharedscripts
    postrotate
        /bin/kill -HUP $(cat /var/run/syslogd.pid 2> /dev/null) 2> /dev/null || true
    endscript
}
EOF

echo "Creating firewall rules (ufw)..."
if command -v ufw &> /dev/null; then
    # Allow metrics endpoint
    ufw allow 9090/tcp comment 'ComfyUI Engine Metrics'
fi

echo ""
echo -e "${GREEN}Installation complete!${NC}"
echo ""
echo "Usage:"
echo "  Start:   sudo systemctl start comfyui-engine@$ENGINE_USER"
echo "  Enable:  sudo systemctl enable comfyui-engine@$ENGINE_USER"
echo "  Status:  sudo systemctl status comfyui-engine@$ENGINE_USER"
echo "  Logs:    sudo journalctl -u comfyui-engine@$ENGINE_USER -f"
echo ""
echo "Metrics: http://localhost:9090/metrics"
echo "Health:  http://localhost:9090/health"
echo ""
echo "Next steps:"
echo "  1. Copy your ComfyUI workflow to $ENGINE_DATA/workflows/"
echo "  2. Edit $ENGINE_DATA/config/prompts.yaml"
echo "  3. Start the service"
echo "  4. Configure git remote: cd $ENGINE_DIR && git remote add origin ..."
