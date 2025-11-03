#!/bin/bash

###############################################################################
# Free AutoFrame - Enable Remote Debugging
# Run this script to enable VSCode remote debugging on the server
###############################################################################

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

APP_NAME="autoframe"
APP_DIR="/var/www/${APP_NAME}"
DEBUG_PORT=5678

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Enable Remote Debugging${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

cd ${APP_DIR}

# Check if debugpy is installed
echo -e "${YELLOW}Checking debugpy installation...${NC}"
source venv/bin/activate
pip show debugpy > /dev/null 2>&1 || pip install debugpy
echo -e "${GREEN}✓ debugpy installed${NC}"

# Create debug wrapper script
echo ""
echo -e "${YELLOW}Creating debug wrapper...${NC}"

cat > ${APP_DIR}/debug_app.py <<'EOF'
"""
Debug wrapper for Flask app with remote debugging support
"""
import debugpy
import os

# Enable debugpy
DEBUG_PORT = int(os.getenv('DEBUG_PORT', 5678))
debugpy.listen(('0.0.0.0', DEBUG_PORT))
print(f"🐛 Debugpy listening on port {DEBUG_PORT}")
print("Waiting for debugger to attach...")
# debugpy.wait_for_client()  # Uncomment to wait for debugger before starting

# Import and run the app
from app import app

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
EOF

echo -e "${GREEN}✓ Debug wrapper created${NC}"

# Update systemd service for debugging
echo ""
echo -e "${YELLOW}Updating systemd service...${NC}"

sudo tee /etc/systemd/system/${APP_NAME}-debug.service > /dev/null <<EOF
[Unit]
Description=Free AutoFrame Flask Application (Debug Mode)
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin"
Environment="DEBUG_PORT=${DEBUG_PORT}"
Environment="FLASK_DEBUG=1"
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/debug_app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

echo -e "${GREEN}✓ Debug service created${NC}"

# Open firewall port
echo ""
echo -e "${YELLOW}Configuring firewall...${NC}"
sudo ufw allow ${DEBUG_PORT}/tcp comment 'Python debugpy' 2>/dev/null || echo "Firewall not active or already configured"
echo -e "${GREEN}✓ Firewall configured${NC}"

# Show instructions
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Remote Debugging Enabled!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "To start debugging:"
echo ""
echo "1. Stop production service:"
echo "   sudo systemctl stop ${APP_NAME}"
echo ""
echo "2. Start debug service:"
echo "   sudo systemctl start ${APP_NAME}-debug"
echo ""
echo "3. In VSCode, press F5 to attach debugger"
echo "   (Make sure you have .vscode/launch.json configured)"
echo ""
echo "4. Set breakpoints and debug your code"
echo ""
echo "To return to production mode:"
echo "   sudo systemctl stop ${APP_NAME}-debug"
echo "   sudo systemctl start ${APP_NAME}"
echo ""
echo "Debug port: ${DEBUG_PORT}"
echo ""
echo "SSH tunnel command (run on your local machine):"
echo "   ssh -L ${DEBUG_PORT}:localhost:${DEBUG_PORT} user@your-server"
echo ""
