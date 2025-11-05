#!/bin/bash

###############################################################################
# Free AutoFrame - Hostinger VPS Setup Script
# Run this script ONCE on your Hostinger VPS to set up the application
###############################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Free AutoFrame - Server Setup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Configuration variables (modify these)
APP_NAME="autoframe"
APP_DIR="/var/www/${APP_NAME}"
APP_USER="www-data"
DOMAIN="yourdomain.com"  # Change this to your domain
EMAIL="your-email@domain.com"  # Change this for Let's Encrypt

# Prompt for configuration
read -p "Enter your domain name (e.g., api.yourdomain.com): " DOMAIN
read -p "Enter your email for SSL certificate: " EMAIL
read -p "Enter GitHub repository URL: " REPO_URL

echo ""
echo -e "${YELLOW}Installing system dependencies...${NC}"

# Update system
sudo apt update
sudo apt upgrade -y

# Install required packages
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    nginx \
    ffmpeg \
    git \
    curl \
    certbot \
    python3-certbot-nginx \
    supervisor

echo -e "${GREEN}✓ System dependencies installed${NC}"

# Create application directory
echo ""
echo -e "${YELLOW}Setting up application directory...${NC}"

sudo mkdir -p ${APP_DIR}
cd ${APP_DIR}

# Clone repository
echo -e "${YELLOW}Cloning repository...${NC}"
sudo git clone ${REPO_URL} .

# Create virtual environment
echo -e "${YELLOW}Creating Python virtual environment...${NC}"
sudo python3 -m venv venv

# Activate and install dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
sudo ${APP_DIR}/venv/bin/pip install --upgrade pip
sudo ${APP_DIR}/venv/bin/pip install -r requirements.txt
sudo ${APP_DIR}/venv/bin/pip install gunicorn debugpy

# Create necessary directories
echo -e "${YELLOW}Creating upload/output directories...${NC}"
sudo mkdir -p ${APP_DIR}/uploads ${APP_DIR}/outputs ${APP_DIR}/backups
sudo chown -R ${APP_USER}:${APP_USER} ${APP_DIR}
sudo chmod -R 755 ${APP_DIR}/uploads ${APP_DIR}/outputs

# Create environment file
echo -e "${YELLOW}Creating environment configuration...${NC}"
sudo tee ${APP_DIR}/.env > /dev/null <<EOF
DEPLOYMENT_MODE=hostinger
FLASK_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
BACKEND_API_URL=https://${DOMAIN}
MAX_PARALLEL_JOBS=2
MAX_CONTENT_LENGTH_MB=200
AUTO_CLEANUP_HOURS=24
CORS_ORIGINS=https://autoframe.vercel.app,https://${DOMAIN}
EOF

sudo chown ${APP_USER}:${APP_USER} ${APP_DIR}/.env
sudo chmod 600 ${APP_DIR}/.env

echo -e "${GREEN}✓ Application directory configured${NC}"

# Configure systemd service
echo ""
echo -e "${YELLOW}Configuring systemd service...${NC}"

sudo tee /etc/systemd/system/${APP_NAME}.service > /dev/null <<EOF
[Unit]
Description=Free AutoFrame Flask Application
After=network.target

[Service]
Type=notify
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin"
ExecStart=${APP_DIR}/venv/bin/gunicorn --config ${APP_DIR}/gunicorn.conf.py app:app
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

# Create gunicorn config
sudo tee ${APP_DIR}/gunicorn.conf.py > /dev/null <<EOF
import multiprocessing
import os

# Server socket
bind = "127.0.0.1:5000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'sync'
worker_connections = 1000
timeout = 600  # 10 minutes for large video processing (PAID tier: 20 videos × 300MB)
keepalive = 2

# Logging
accesslog = '/var/log/${APP_NAME}/access.log'
errorlog = '/var/log/${APP_NAME}/error.log'
loglevel = 'info'

# Process naming
proc_name = '${APP_NAME}'

# Server mechanics
daemon = False
pidfile = '/var/run/${APP_NAME}.pid'
umask = 0o007
user = '${APP_USER}'
group = '${APP_USER}'
tmp_upload_dir = None

# Performance
preload_app = False
EOF

# Create log directory
sudo mkdir -p /var/log/${APP_NAME}
sudo chown -R ${APP_USER}:${APP_USER} /var/log/${APP_NAME}

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable ${APP_NAME}
sudo systemctl start ${APP_NAME}

echo -e "${GREEN}✓ Systemd service configured and started${NC}"

# Configure Nginx
echo ""
echo -e "${YELLOW}Configuring Nginx...${NC}"

sudo tee /etc/nginx/sites-available/${APP_NAME} > /dev/null <<'NGINX_EOF'
# Rate limiting
limit_req_zone $binary_remote_addr zone=upload_limit:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=30r/m;

upstream autoframe_app {
    server 127.0.0.1:5000 fail_timeout=0;
}

server {
    listen 80;
    server_name DOMAIN_PLACEHOLDER;

    # Redirect HTTP to HTTPS (will be added after SSL setup)
    # return 301 https://$server_name$request_uri;

    # Client body size (for large video uploads - PAID tier supports up to 300MB)
    client_max_body_size 400M;
    client_body_timeout 600s;

    # Timeouts (increased for large video processing)
    proxy_connect_timeout 600s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;
    send_timeout 600s;

    # CRITICAL: COOP/COEP headers for ffmpeg.wasm SharedArrayBuffer support
    # Without these headers, browser rendering will be 10x slower or fail
    add_header Cross-Origin-Opener-Policy "same-origin" always;
    add_header Cross-Origin-Embedder-Policy "require-corp" always;

    # Logging
    access_log /var/log/nginx/autoframe_access.log;
    error_log /var/log/nginx/autoframe_error.log;

    # Static files
    location /static {
        alias /var/www/autoframe/static;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Health check endpoint (no rate limit)
    location /health {
        proxy_pass http://autoframe_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Upload endpoint (strict rate limit)
    location /upload {
        limit_req zone=upload_limit burst=5 nodelay;

        proxy_pass http://autoframe_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Buffering settings for large uploads
        proxy_request_buffering off;
        proxy_buffering off;
    }

    # API endpoints (moderate rate limit)
    location /api {
        limit_req zone=api_limit burst=10 nodelay;

        proxy_pass http://autoframe_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # All other requests
    location / {
        proxy_pass http://autoframe_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX_EOF

# Replace domain placeholder
sudo sed -i "s/DOMAIN_PLACEHOLDER/${DOMAIN}/g" /etc/nginx/sites-available/${APP_NAME}

# Enable site
sudo ln -sf /etc/nginx/sites-available/${APP_NAME} /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Test nginx configuration
sudo nginx -t

# Restart nginx
sudo systemctl restart nginx

echo -e "${GREEN}✓ Nginx configured${NC}"

# Setup SSL with Let's Encrypt
echo ""
echo -e "${YELLOW}Setting up SSL certificate...${NC}"
echo -e "${YELLOW}Note: Make sure your domain DNS is pointing to this server!${NC}"
read -p "Press Enter to continue with SSL setup, or Ctrl+C to skip..."

sudo certbot --nginx -d ${DOMAIN} --non-interactive --agree-tos --email ${EMAIL} --redirect

echo -e "${GREEN}✓ SSL certificate installed${NC}"

# Setup cron job for cleanup
echo ""
echo -e "${YELLOW}Setting up automated cleanup...${NC}"

sudo tee /etc/cron.daily/${APP_NAME}-cleanup > /dev/null <<EOF
#!/bin/bash
# Clean up old uploads and outputs
find ${APP_DIR}/uploads -type f -mtime +1 -delete
find ${APP_DIR}/outputs -type f -mtime +1 -delete
find ${APP_DIR}/uploads -type d -empty -delete
find ${APP_DIR}/outputs -type d -empty -delete

# Clean old backups (keep last 5)
cd ${APP_DIR}/backups && ls -t | tail -n +6 | xargs -r rm -rf
EOF

sudo chmod +x /etc/cron.daily/${APP_NAME}-cleanup

echo -e "${GREEN}✓ Automated cleanup configured${NC}"

# Print status
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Application Status:"
sudo systemctl status ${APP_NAME} --no-pager | head -10
echo ""
echo -e "${GREEN}✓ Application is running at: https://${DOMAIN}${NC}"
echo ""
echo "Next steps:"
echo "1. Configure GitHub secrets for auto-deployment:"
echo "   - HOSTINGER_HOST: Your server IP or hostname"
echo "   - HOSTINGER_USER: SSH username"
echo "   - HOSTINGER_SSH_KEY: SSH private key"
echo "   - HOSTINGER_APP_PATH: ${APP_DIR}"
echo ""
echo "2. Update your Vercel environment variables:"
echo "   - BACKEND_API_URL: https://${DOMAIN}"
echo ""
echo "3. Test the deployment:"
echo "   curl https://${DOMAIN}/health"
echo ""
echo -e "${YELLOW}Important files:${NC}"
echo "  - Application: ${APP_DIR}"
echo "  - Logs: /var/log/${APP_NAME}/"
echo "  - Nginx config: /etc/nginx/sites-available/${APP_NAME}"
echo "  - Systemd service: /etc/systemd/system/${APP_NAME}.service"
echo ""
echo "Useful commands:"
echo "  sudo systemctl restart ${APP_NAME}  # Restart app"
echo "  sudo systemctl status ${APP_NAME}   # Check status"
echo "  sudo tail -f /var/log/${APP_NAME}/error.log  # View logs"
echo "  sudo certbot renew --dry-run       # Test SSL renewal"
echo ""
