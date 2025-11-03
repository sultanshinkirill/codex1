# Free AutoFrame - Deployment Guide

Complete guide for deploying Free AutoFrame with auto-deployment to both Vercel (free) and Hostinger VPS (paid).

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Vercel Deployment (Free)](#vercel-deployment-free)
4. [Hostinger VPS Setup (Paid)](#hostinger-vps-setup-paid)
5. [GitHub Actions Configuration](#github-actions-configuration)
6. [Environment Variables](#environment-variables)
7. [Testing Deployment](#testing-deployment)
8. [Remote Debugging](#remote-debugging)
9. [Maintenance](#maintenance)
10. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

### Hybrid Deployment Strategy

```
┌─────────────┐
│   GitHub    │  Push code
│  Repository │────────────┐
└─────────────┘            │
                           ▼
            ┌──────────────────────────┐
            │   GitHub Actions         │
            │   (Auto-Deploy)          │
            └────┬──────────────┬──────┘
                 │              │
                 ▼              ▼
        ┌────────────┐   ┌──────────────┐
        │   Vercel   │   │  Hostinger   │
        │  (Frontend)│   │     VPS      │
        │   FREE     │   │   (Backend)  │
        └────────────┘   └──────────────┘
                │              │
                └──────┬───────┘
                       ▼
               ┌───────────────┐
               │     Users     │
               └───────────────┘
```

### How It Works

**Vercel (Free Tier):**
- Serves static files (HTML, CSS, JS)
- Client-side rendering with FFmpeg.wasm (browser processing)
- Handles videos ≤75 seconds and <50MB
- Global CDN delivery
- 10-second serverless timeout

**Hostinger VPS (Paid):**
- Full Flask application
- Server-side processing for large videos (up to 200MB × 10 files)
- No timeout limits (5 min max per video)
- Batch processing support
- No restrictions

**Smart Routing:**
- Small videos → Browser processing (FREE)
- Large videos (>50MB or >75s) → Hostinger VPS
- Automatic fallback chain

---

## Prerequisites

### Required Accounts
1. **GitHub Account** - For code repository
2. **Vercel Account** - Free tier (no credit card required)
3. **Hostinger VPS** - Paid hosting (recommended: VPS 4, 4 vCPU, 16GB RAM)

### Required Tools
- Git
- SSH client
- Text editor (VSCode recommended)

### Domain (Optional but Recommended)
- Custom domain for Hostinger VPS
- DNS access for SSL certificate

---

## Vercel Deployment (Free)

### Step 1: Connect GitHub Repository

1. Go to [https://vercel.com](https://vercel.com)
2. Sign in with GitHub
3. Click "New Project"
4. Import your GitHub repository
5. Vercel will auto-detect the configuration from `vercel.json`

### Step 2: Configure Environment Variables

In Vercel dashboard:
1. Go to Project Settings → Environment Variables
2. Add the following:

```
DEPLOYMENT_MODE=vercel
FLASK_ENV=production
BACKEND_API_URL=https://api.yourdomain.com
```

Replace `https://api.yourdomain.com` with your Hostinger VPS URL.

### Step 3: Deploy

1. Click "Deploy"
2. Wait 30-60 seconds
3. Your app is live!

### Auto-Deploy

Every push to `main` branch automatically deploys to Vercel (no configuration needed).

---

## Hostinger VPS Setup (Paid)

### Step 1: Initial Server Setup

SSH into your Hostinger VPS:

```bash
ssh root@your-hostinger-ip
```

Clone the repository:

```bash
cd /var/www
git clone https://github.com/yourusername/your-repo.git autoframe
cd autoframe
```

Run the setup script:

```bash
chmod +x deploy/setup-server.sh
./deploy/setup-server.sh
```

The script will prompt you for:
- Domain name (e.g., `api.yourdomain.com`)
- Email for SSL certificate
- GitHub repository URL

The script will automatically:
- Install Python, FFmpeg, Nginx, and dependencies
- Create virtual environment
- Configure systemd service
- Setup Nginx reverse proxy
- Install SSL certificate with Let's Encrypt
- Start the application

### Step 2: Verify Installation

Check if the application is running:

```bash
sudo systemctl status autoframe
```

Test the health endpoint:

```bash
curl https://yourdomain.com/health
```

Expected response:
```json
{
  "status": "healthy",
  "deployment_mode": "hostinger",
  "timestamp": "2025-11-03T12:00:00Z"
}
```

---

## GitHub Actions Configuration

### Step 1: Generate SSH Key

On your local machine:

```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/hostinger_deploy
```

### Step 2: Add Public Key to Hostinger

Copy the public key:

```bash
cat ~/.ssh/hostinger_deploy.pub
```

On Hostinger VPS:

```bash
mkdir -p ~/.ssh
nano ~/.ssh/authorized_keys
# Paste the public key
chmod 600 ~/.ssh/authorized_keys
chmod 700 ~/.ssh
```

### Step 3: Configure GitHub Secrets

Go to GitHub repository → Settings → Secrets and variables → Actions

Add the following secrets:

| Secret Name | Value | Example |
|------------|-------|---------|
| `HOSTINGER_HOST` | Your VPS IP or domain | `123.456.789.0` |
| `HOSTINGER_USER` | SSH username | `root` or `ubuntu` |
| `HOSTINGER_SSH_KEY` | Private SSH key | Contents of `~/.ssh/hostinger_deploy` |
| `HOSTINGER_PORT` | SSH port (optional) | `22` |
| `HOSTINGER_APP_PATH` | App directory | `/var/www/autoframe` |

### Step 4: Test Auto-Deploy

Make a small change and push:

```bash
git add .
git commit -m "Test auto-deploy"
git push origin main
```

Watch the deployment:
1. Go to GitHub → Actions tab
2. See the workflow running
3. Both Vercel and Hostinger deploy automatically

---

## Environment Variables

### Vercel Environment Variables

Set in Vercel dashboard:

```env
DEPLOYMENT_MODE=vercel
FLASK_ENV=production
BACKEND_API_URL=https://api.yourdomain.com
MAX_CONTENT_LENGTH_MB=50
CLIENT_DURATION_LIMIT_SECONDS=75
```

### Hostinger Environment Variables

Edit `/var/www/autoframe/.env` on the server:

```env
DEPLOYMENT_MODE=hostinger
FLASK_ENV=production
SECRET_KEY=your-generated-secret-key
BACKEND_API_URL=https://api.yourdomain.com
MAX_CONTENT_LENGTH_MB=200
MAX_SERVER_DURATION_SECONDS=300
MAX_BATCH_SIZE=10
MAX_PARALLEL_JOBS=2
AUTO_CLEANUP_HOURS=24
CORS_ORIGINS=https://autoframe.vercel.app,https://yourdomain.com
```

The setup script auto-generates `SECRET_KEY`. Update `CORS_ORIGINS` with your actual Vercel URL.

---

## Testing Deployment

### Test Client-Side Rendering (Free)

1. Visit your Vercel URL: `https://your-app.vercel.app`
2. Upload a short video (<75 seconds, <50MB)
3. Select aspect ratios and style
4. Click "Render Videos"
5. Processing happens in browser (check console for "Using browser rendering")
6. Download completed videos

### Test Server-Side Rendering (Hostinger)

1. Upload a large video (>50MB or >75 seconds)
2. Processing automatically routes to Hostinger
3. Check console for "Using Hostinger for..."
4. Monitor progress
5. Download from server

### Test Auto-Deployment

```bash
# Make a change
echo "# Test" >> README.md

# Commit and push
git add README.md
git commit -m "Test deployment"
git push origin main

# Watch deployments
# - GitHub Actions: https://github.com/youruser/yourrepo/actions
# - Vercel: https://vercel.com/youruser/yourproject
```

Both should deploy within 1-2 minutes.

---

## Remote Debugging

### Setup Debugging on Hostinger

On the VPS:

```bash
cd /var/www/autoframe
./deploy/enable-debug.sh
```

This installs `debugpy` and creates a debug service.

### Start Debug Mode

```bash
# Stop production
sudo systemctl stop autoframe

# Start debug mode
sudo systemctl start autoframe-debug

# Check status
sudo systemctl status autoframe-debug
```

### Connect from VSCode

1. Open VSCode
2. Install "Remote - SSH" extension
3. Press `F1` → "Remote-SSH: Connect to Host"
4. Enter: `root@your-hostinger-ip`
5. Open folder: `/var/www/autoframe`
6. Press `F5` → Select "Remote Debug (Hostinger)"
7. Set breakpoints in `app.py`
8. Debug requests in real-time!

### Alternative: SSH Tunnel

On your local machine:

```bash
ssh -L 5678:localhost:5678 root@your-hostinger-ip
```

Then press `F5` in VSCode and select "Remote Flask Debug".

### Return to Production

```bash
sudo systemctl stop autoframe-debug
sudo systemctl start autoframe
```

---

## Maintenance

### View Logs

```bash
# Application logs
sudo tail -f /var/log/autoframe/error.log
sudo tail -f /var/log/autoframe/access.log

# Nginx logs
sudo tail -f /var/log/nginx/autoframe_error.log

# Systemd service logs
sudo journalctl -u autoframe -f
```

### Restart Application

```bash
sudo systemctl restart autoframe
```

### Manual Update

If auto-deploy fails, run manually:

```bash
cd /var/www/autoframe
./deploy/update-app.sh
```

This script:
- Creates backup
- Pulls latest code
- Updates dependencies
- Restarts service
- Performs health check
- Rolls back on failure

### SSL Certificate Renewal

Let's Encrypt certificates auto-renew. Test renewal:

```bash
sudo certbot renew --dry-run
```

### Cleanup Old Files

Automatic cleanup runs daily. Manual cleanup:

```bash
# Clean uploads/outputs older than 1 day
find /var/www/autoframe/uploads -type f -mtime +1 -delete
find /var/www/autoframe/outputs -type f -mtime +1 -delete

# Clean backups (keep last 5)
cd /var/www/autoframe/backups && ls -t | tail -n +6 | xargs rm -rf
```

---

## Troubleshooting

### Vercel Issues

**Problem: Build fails**
```
Solution:
- Check vercel.json syntax
- Verify Python version (3.9+ required)
- Check build logs in Vercel dashboard
```

**Problem: 504 Gateway Timeout**
```
Solution:
- Video is too long for serverless (>10s processing)
- Will automatically fallback to Hostinger
- Check browser console for routing decision
```

### Hostinger Issues

**Problem: Service won't start**
```bash
# Check logs
sudo journalctl -u autoframe -n 50

# Common fixes:
sudo systemctl daemon-reload
sudo systemctl restart autoframe

# Check port conflicts
sudo lsof -i :5000
```

**Problem: Nginx 502 Bad Gateway**
```bash
# Check if Flask is running
sudo systemctl status autoframe

# Check Nginx error log
sudo tail -f /var/log/nginx/autoframe_error.log

# Restart both
sudo systemctl restart autoframe nginx
```

**Problem: Out of disk space**
```bash
# Check disk usage
df -h

# Manual cleanup
./deploy/cleanup.sh

# Increase cleanup frequency
sudo crontab -e
# Change from daily to hourly: 0 * * * *
```

**Problem: High memory usage**
```bash
# Check memory
free -h

# Check processes
top

# Reduce parallel jobs
sudo nano /var/www/autoframe/.env
# Set: MAX_PARALLEL_JOBS=1

# Restart
sudo systemctl restart autoframe
```

### GitHub Actions Issues

**Problem: SSH connection failed**
```
Solution:
- Verify HOSTINGER_SSH_KEY secret is correct
- Check HOSTINGER_HOST and HOSTINGER_USER
- Test SSH manually: ssh -i ~/.ssh/key user@host
```

**Problem: Permission denied**
```bash
# On server, fix ownership
sudo chown -R www-data:www-data /var/www/autoframe
sudo chmod -R 755 /var/www/autoframe
```

### General Issues

**Problem: Videos not processing**
```
1. Check browser console for errors
2. Check network tab for failed requests
3. Test health endpoint: curl https://yourapp.com/health
4. Check server logs: sudo tail -f /var/log/autoframe/error.log
5. Verify FFmpeg: ffmpeg -version
```

**Problem: Slow processing**
```
For Hostinger:
1. Check CPU/RAM: htop
2. Reduce parallel jobs in .env
3. Upgrade VPS plan
4. Check ffmpeg encoding preset in app.py (currently: veryfast)
```

---

## Performance Tuning

### Hostinger VPS Recommendations

| VPS Plan | vCPU | RAM | Max Parallel Jobs | Batch Time (10×200MB) |
|----------|------|-----|-------------------|----------------------|
| VPS 2 | 2 | 8GB | 1 | 25-35 min |
| VPS 4 | 4 | 16GB | 2 | 12-18 min |
| VPS 8 | 8 | 32GB | 4 | 6-10 min |

Update `/var/www/autoframe/.env`:
```env
MAX_PARALLEL_JOBS=2  # Adjust based on vCPU count
```

### Gunicorn Workers

Edit `/var/www/autoframe/gunicorn.conf.py`:
```python
workers = multiprocessing.cpu_count()  # Use all cores
```

Restart:
```bash
sudo systemctl restart autoframe
```

---

## Security Best Practices

1. **Keep secrets secure**
   - Never commit `.env` files
   - Rotate SSH keys regularly
   - Use GitHub secrets for sensitive data

2. **Update dependencies**
   ```bash
   pip list --outdated
   pip install --upgrade package-name
   ```

3. **Monitor logs**
   - Check for suspicious activity
   - Set up log rotation
   - Use fail2ban for SSH protection

4. **Firewall rules**
   ```bash
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw allow 22/tcp
   sudo ufw enable
   ```

---

## Cost Breakdown

### Free Tier (Vercel Only)
- **Cost**: $0/month
- **Bandwidth**: 100GB/month
- **Limitations**: 10s timeout, client-side processing only
- **Best for**: Personal use, light traffic

### Hybrid Setup
- **Vercel**: $0/month
- **Hostinger VPS 4**: ~$20/month
- **Total**: ~$20/month
- **Bandwidth**: Unlimited (check Hostinger plan)
- **Best for**: Production use, batch processing

---

## Support

### Documentation
- Flask: https://flask.palletsprojects.com
- MoviePy: https://zulko.github.io/moviepy/
- FFmpeg: https://ffmpeg.org/documentation.html
- Vercel: https://vercel.com/docs
- GitHub Actions: https://docs.github.com/actions

### Useful Commands

```bash
# Hostinger quick reference
sudo systemctl status autoframe      # Check status
sudo systemctl restart autoframe     # Restart app
sudo systemctl stop autoframe        # Stop app
sudo systemctl start autoframe       # Start app
sudo tail -f /var/log/autoframe/error.log  # View logs
./deploy/update-app.sh               # Manual update
./deploy/enable-debug.sh             # Enable debugging

# Check resources
htop                                 # CPU/RAM usage
df -h                                # Disk usage
free -h                              # Memory usage
sudo lsof -i :5000                   # Check port 5000

# Nginx
sudo nginx -t                        # Test config
sudo systemctl restart nginx         # Restart nginx
sudo tail -f /var/log/nginx/autoframe_error.log  # Nginx logs
```

---

## Next Steps

1. ✅ Complete setup (you're done!)
2. Test with small videos (browser processing)
3. Test with large videos (server processing)
4. Monitor logs for errors
5. Adjust `MAX_PARALLEL_JOBS` based on performance
6. Setup monitoring (optional): Uptime Robot, Sentry
7. Add custom domain (optional)
8. Scale as needed

---

## Changelog

### Version 1.0 (2025-11-03)
- Initial hybrid deployment setup
- Vercel + Hostinger auto-deployment
- GitHub Actions CI/CD
- Remote debugging support
- Health check endpoints
- Smart backend routing

---

**Happy Deploying! 🚀**

For issues or questions, create a GitHub issue in your repository.
