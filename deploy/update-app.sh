#!/bin/bash

###############################################################################
# Free AutoFrame - Quick Update Script
# Run this script to manually update the application on Hostinger VPS
###############################################################################

set -e  # Exit on error

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

APP_NAME="autoframe"
APP_DIR="/var/www/${APP_NAME}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Free AutoFrame - Quick Update${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if we're in the app directory
if [ ! -d "${APP_DIR}" ]; then
    echo -e "${RED}Error: Application directory not found: ${APP_DIR}${NC}"
    echo "Run setup-server.sh first!"
    exit 1
fi

cd ${APP_DIR}

# Create backup
echo -e "${YELLOW}Creating backup...${NC}"
BACKUP_DIR="backups/backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p ${BACKUP_DIR}
cp -r app.py static templates *.py ${BACKUP_DIR}/ 2>/dev/null || true
echo -e "${GREEN}✓ Backup created: ${BACKUP_DIR}${NC}"

# Pull latest code
echo ""
echo -e "${YELLOW}Pulling latest code from GitHub...${NC}"
git fetch origin
git reset --hard origin/main
echo -e "${GREEN}✓ Code updated${NC}"

# Activate virtual environment and update dependencies
echo ""
echo -e "${YELLOW}Updating dependencies...${NC}"
source venv/bin/activate
pip install -r requirements.txt --upgrade --quiet
echo -e "${GREEN}✓ Dependencies updated${NC}"

# Set permissions
echo ""
echo -e "${YELLOW}Setting permissions...${NC}"
sudo chown -R www-data:www-data uploads outputs
sudo chmod -R 755 uploads outputs
echo -e "${GREEN}✓ Permissions set${NC}"

# Restart service
echo ""
echo -e "${YELLOW}Restarting application...${NC}"
sudo systemctl restart ${APP_NAME}

# Wait for service to start
sleep 3

# Health check
echo ""
echo -e "${YELLOW}Performing health check...${NC}"
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/health || echo "000")

if [ "$HEALTH_STATUS" = "200" ]; then
    echo -e "${GREEN}✓ Health check passed!${NC}"
    echo -e "${GREEN}✓ Application updated successfully!${NC}"
else
    echo -e "${RED}✗ Health check failed (Status: ${HEALTH_STATUS})${NC}"
    echo -e "${YELLOW}Rolling back to backup...${NC}"

    sudo systemctl stop ${APP_NAME}
    cp -r ${BACKUP_DIR}/* ${APP_DIR}/
    sudo systemctl start ${APP_NAME}

    echo -e "${RED}Update failed. Rolled back to previous version.${NC}"
    exit 1
fi

# Show status
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Update Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Application status:"
sudo systemctl status ${APP_NAME} --no-pager | head -5
echo ""
echo "View logs:"
echo "  sudo tail -f /var/log/${APP_NAME}/error.log"
echo ""
