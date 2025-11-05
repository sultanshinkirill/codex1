# Quick Start - Deploy in 5 Minutes

## The Super Simple Version

### 1. Log into Hostinger
- Go to https://hpanel.hostinger.com
- Click your VPS
- Click "Browser Terminal"

### 2. Run These Commands (Copy & Paste)

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install git
sudo apt install -y git

# Clone your repo (REPLACE with your GitHub URL!)
cd /tmp
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

# Run deployment script
chmod +x deploy/setup-server.sh
sudo ./deploy/setup-server.sh
```

### 3. Answer 3 Questions

1. **Domain name?**
   - Have domain: `autoframe.yourdomain.com`
   - No domain: Use your VPS IP (found in Hostinger panel)

2. **Email?**
   - Your email address

3. **GitHub repo?**
   - Same URL you cloned above

### 4. Wait 5-10 Minutes ☕

The script installs everything automatically.

### 5. Test It!

Open in browser:
- With domain: `https://yourdomain.com`
- Without domain: `http://YOUR_VPS_IP`

You should see your video app with FREE/PAID buttons!

---

## About the SECRET_KEY

**You asked:** "Where to get SECRET_KEY? Where to put it?"

**Answer:** You don't need to do anything!

The deployment script automatically:
1. Generates a secure random SECRET_KEY
2. Saves it to `/var/www/autoframe/.env`
3. Uses it when the app starts

It's already done for you! 🎉

---

## If Something Goes Wrong

```bash
# Check if app is running
sudo systemctl status autoframe

# View logs
sudo tail -50 /var/log/autoframe/error.log

# Restart app
sudo systemctl restart autoframe
```

---

That's it! Your app is deployed! 🚀

For detailed troubleshooting, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
