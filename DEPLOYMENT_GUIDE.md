# Free AutoFrame - Hostinger Deployment Guide

Complete step-by-step guide to deploy your video processing app on Hostinger VPS.

---

## Prerequisites

1. **Hostinger VPS Account** (VPS 4 recommended: 4 vCPU, 16GB RAM)
2. **Domain name** (optional but recommended)
3. **SSH access** to your Hostinger VPS

---

## Step 1: Access Your Hostinger VPS

### Option A: Using Hostinger's Web Terminal
1. Log into your Hostinger account at https://hpanel.hostinger.com
2. Go to **VPS** section
3. Click on your VPS
4. Click **"Browser Terminal"** or **"SSH Access"**
5. You'll get a terminal in your browser

### Option B: Using SSH from your computer
1. Get your VPS IP address from Hostinger panel
2. Get your SSH credentials (username and password)
3. Open terminal/command prompt and run:
   ```bash
   ssh root@YOUR_VPS_IP
   ```
4. Enter your password when prompted

---

## Step 2: Initial Server Setup (First Time Only)

Once you're logged into your VPS:

### 2.1 Update System
```bash
sudo apt update && sudo apt upgrade -y
```

### 2.2 Install Basic Tools
```bash
sudo apt install -y git curl wget
```

---

## Step 3: Clone Your Repository

```bash
cd /tmp
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME
```

**Replace** `YOUR_USERNAME/YOUR_REPO_NAME` with your actual GitHub repository.

---

## Step 4: Run the Deployment Script

The deployment script will install everything automatically.

### 4.1 Make the script executable
```bash
chmod +x deploy/setup-server.sh
```

### 4.2 Run the script
```bash
sudo ./deploy/setup-server.sh
```

### 4.3 Answer the prompts

The script will ask you:

**Prompt 1: Domain name**
```
Enter your domain name (e.g., api.yourdomain.com):
```
- If you have a domain: Enter it (e.g., `autoframe.yourdomain.com`)
- If you DON'T have a domain: Enter your VPS IP address (e.g., `123.45.67.89`)

**Prompt 2: Email for SSL**
```
Enter your email for SSL certificate:
```
- Enter your email address (needed for Let's Encrypt SSL)

**Prompt 3: GitHub Repository URL**
```
Enter GitHub repository URL:
```
- Enter the full GitHub URL (e.g., `https://github.com/username/repo.git`)

### 4.4 Wait for installation

The script will:
- ✅ Install Python, Nginx, FFmpeg
- ✅ Clone your repository
- ✅ Install Python dependencies
- ✅ Generate SECRET_KEY automatically
- ✅ Configure Nginx with COOP/COEP headers
- ✅ Set up SSL certificate (if domain is configured)
- ✅ Start the application

This takes about **5-10 minutes**.

---

## Step 5: Verify Installation

After the script completes, test your deployment:

### 5.1 Check application status
```bash
sudo systemctl status autoframe
```

You should see **"active (running)"** in green.

### 5.2 Test the health endpoint

If using a domain:
```bash
curl https://yourdomain.com/health
```

If using IP address:
```bash
curl http://YOUR_VPS_IP/health
```

You should see:
```json
{
  "status": "healthy",
  "deployment_mode": "hostinger",
  "timestamp": "2025-..."
}
```

---

## Step 6: Check Your SECRET_KEY (Important!)

The deployment script automatically generates a SECRET_KEY. To verify:

```bash
cat /var/www/autoframe/.env
```

You should see a line like:
```
SECRET_KEY=a1b2c3d4e5f6...long random string...
```

**This key is automatically generated and saved**. You don't need to do anything!

---

## Step 7: Configure DNS (If Using Domain)

If you're using a domain name:

1. Go to your domain registrar (where you bought the domain)
2. Find **DNS settings** or **DNS management**
3. Add an **A record**:
   - **Type**: A
   - **Name**: @ (or your subdomain like "autoframe")
   - **Value**: Your VPS IP address
   - **TTL**: 3600 (or default)
4. Save changes
5. Wait 5-60 minutes for DNS propagation

---

## Step 8: Test Both Tiers

### 8.1 Test FREE Tier (Browser Rendering)

1. Open your website in a browser: `https://yourdomain.com` (or `http://YOUR_VPS_IP`)
2. You should see **"FREE"** badge at the top
3. Upload 1-3 videos (max 50MB each, max 75 seconds)
4. Select 1-2 aspect ratios
5. Click **"Render"**
6. Videos process in browser (might take a few minutes)

### 8.2 Test PAID Tier (Server Rendering)

1. Click **"Upgrade to PAID"** button
2. Badge changes to **"PAID"** (gold color)
3. Now you can:
   - Upload up to 20 videos
   - Files up to 300MB each
   - Videos up to 3 minutes (180s)
   - Select up to 4 aspect ratios
4. Rendering happens on server (faster than browser)

---

## Step 9: View Logs (Troubleshooting)

If something goes wrong:

### Application logs
```bash
sudo tail -f /var/log/autoframe/error.log
```

### Nginx logs
```bash
sudo tail -f /var/log/nginx/autoframe_error.log
```

### System service status
```bash
sudo systemctl status autoframe
```

---

## Step 10: Useful Commands

### Restart application
```bash
sudo systemctl restart autoframe
```

### Check application status
```bash
sudo systemctl status autoframe
```

### View real-time logs
```bash
sudo tail -f /var/log/autoframe/error.log
```

### Update application code
```bash
cd /var/www/autoframe
sudo git pull
sudo systemctl restart autoframe
```

### Check disk space
```bash
df -h
```

### Check memory usage
```bash
free -h
```

---

## Important File Locations

| Item | Location |
|------|----------|
| Application code | `/var/www/autoframe/` |
| Environment config | `/var/www/autoframe/.env` |
| Application logs | `/var/log/autoframe/` |
| Nginx config | `/etc/nginx/sites-available/autoframe` |
| Systemd service | `/etc/systemd/system/autoframe.service` |
| Uploads (temp) | `/var/www/autoframe/uploads/` |
| Outputs (temp) | `/var/www/autoframe/outputs/` |

---

## Common Issues & Solutions

### Issue: "Connection refused" or site not loading
**Solution:**
```bash
# Check if app is running
sudo systemctl status autoframe

# If not running, start it
sudo systemctl start autoframe

# Check for errors
sudo tail -50 /var/log/autoframe/error.log
```

### Issue: "502 Bad Gateway"
**Solution:**
```bash
# App crashed, check logs
sudo tail -50 /var/log/autoframe/error.log

# Restart app
sudo systemctl restart autoframe
```

### Issue: SSL certificate not working
**Solution:**
```bash
# Make sure DNS is pointing to your server
# Then run certbot manually
sudo certbot --nginx -d yourdomain.com
```

### Issue: Videos not processing
**Solution:**
```bash
# Check if FFmpeg is installed
ffmpeg -version

# Check disk space
df -h

# Check logs for specific error
sudo tail -50 /var/log/autoframe/error.log
```

---

## Security Checklist

After deployment:

- ✅ SECRET_KEY is set (automatically generated by script)
- ✅ Firewall is enabled (Hostinger usually has this)
- ✅ SSL certificate is installed (if using domain)
- ✅ COOP/COEP headers are configured (for browser rendering)
- ✅ Rate limiting is active
- ✅ File cleanup runs every hour

---

## What Happens After Deployment?

1. **FREE tier users** can:
   - Process 3 videos at a time (50MB max each)
   - Up to 75 seconds duration
   - 2 aspect ratios per batch
   - 3 batches per day
   - Browser rendering (no server cost)

2. **PAID tier users** can:
   - Process 20 videos at a time (300MB max each)
   - Up to 3 minutes (180s) duration
   - 4 aspect ratios per batch
   - Unlimited batches
   - Server rendering (faster)

3. **Automatic cleanup**:
   - Old files deleted every hour
   - Disk space monitored
   - Low space warnings logged

4. **Security**:
   - Session + IP tracking prevents abuse
   - Rate limiting prevents spam
   - Content-Length checks prevent bandwidth waste

---

## Next Steps (After MVP Testing)

Once you've tested with 1-5 users:

1. **Add payment system** (Stripe/PayPal)
2. **Add user accounts** (optional - currently anonymous)
3. **Add email notifications** (when rendering complete)
4. **Scale up** if needed (upgrade VPS)
5. **Add monitoring** (UptimeRobot, etc.)

---

## Support

If you run into issues:

1. Check the logs (see Step 9)
2. Check common issues (above)
3. Verify all services are running:
   ```bash
   sudo systemctl status autoframe
   sudo systemctl status nginx
   ```

---

## Summary

**That's it!** Your app is now deployed with:
- ✅ Two-tier system (FREE browser + PAID server)
- ✅ Security & rate limiting
- ✅ Automatic cleanup
- ✅ COOP/COEP headers for fast browser rendering
- ✅ Easy Coolify-ready setup

The entire deployment should take **10-15 minutes** from start to finish!
