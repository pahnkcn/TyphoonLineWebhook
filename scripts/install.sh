#!/bin/bash

# ใจดี Chatbot - Ubuntu Server Installation Script
# Supports: DigitalOcean Droplet, Azure VM, AWS EC2, or any Ubuntu 22.04+ server
# This script installs Docker, Nginx, and sets up the production environment.

set -euo pipefail

# ===================================================
# Configuration
# ===================================================
REPO_URL="${REPO_URL:-https://github.com/yourusername/TyphoonLineWebhook.git}"
APP_DIR="${APP_DIR:-/home/deploy/typhoon-webhook}"
DOCR_REGISTRY="${DOCR_REGISTRY:-}"  # e.g. "typhoon-registry"

# Print section headers for better readability
print_section() {
    echo
    echo "===== $1 ====="
    echo
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run this script as root (use sudo)"
    exit 1
fi

# Get current username for ownership settings
CURRENT_USER=$(logname || echo $SUDO_USER)
if [ -z "$CURRENT_USER" ]; then
    echo "Unable to determine current user. Please run with sudo."
    exit 1
fi

ENV_FILE="$APP_DIR/.env"

print_section "System Update & Basic Packages"
apt-get update && apt-get upgrade -y

apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    nano \
    unzip

print_section "Installing Docker"
# Remove older versions if they exist
apt-get remove -y docker docker-engine docker.io containerd runc || true

# Install Docker via official script
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
fi

# Add current user to docker group
usermod -aG docker "$CURRENT_USER"
echo "Docker installed successfully. Added $CURRENT_USER to docker group."

print_section "Installing doctl (DigitalOcean CLI)"
if ! command -v doctl &> /dev/null; then
    DOCTL_VERSION="1.104.0"
    curl -sL "https://github.com/digitalocean/doctl/releases/download/v${DOCTL_VERSION}/doctl-${DOCTL_VERSION}-linux-amd64.tar.gz" | \
        tar -xzv -C /usr/local/bin
    echo "doctl installed. Run 'doctl auth init' to authenticate."
else
    echo "doctl already installed."
fi

print_section "Setting Up Application Directory"
mkdir -p "$APP_DIR/logs"

if [ ! -f "$APP_DIR/docker-compose.prod.yml" ]; then
    git clone "$REPO_URL" "$APP_DIR" || {
        echo "Failed to clone repository. Please clone manually into $APP_DIR"
        echo "  git clone $REPO_URL $APP_DIR"
    }
fi

print_section "Setting Up Environment Variables"
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$ENV_FILE"
        # Auto-generate secure passwords
        sed -i "s/change_this_password/$(openssl rand -hex 16)/g" "$ENV_FILE"
        sed -i "s/ENVIRONMENT=development/ENVIRONMENT=production/" "$ENV_FILE"
        echo "Created .env from .env.example with auto-generated passwords."
        echo ">>> IMPORTANT: Edit $ENV_FILE with your actual API keys! <<<"
    else
        echo "WARNING: No .env.example found. Create .env manually."
    fi
    chmod 600 "$ENV_FILE"
    chown "$CURRENT_USER:$CURRENT_USER" "$ENV_FILE"
else
    echo ".env file already exists. Skipping creation."
fi

print_section "Setting Up Nginx Reverse Proxy"
apt-get install -y nginx certbot python3-certbot-nginx

cat > /etc/nginx/sites-available/chatbot << 'NGINX_CONF'
server {
    listen 80;
    server_name _;

    # LINE webhook endpoint
    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
    }

    # Health check (no proxy headers needed)
    location /health {
        proxy_pass http://localhost:5000/health;
    }
}
NGINX_CONF

ln -sf /etc/nginx/sites-available/chatbot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

print_section "Setting Up Firewall"
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable

# Set proper ownership of app directory
chown -R "$CURRENT_USER:$CURRENT_USER" "$APP_DIR"

print_section "DOCR Login (Optional)"
if [ -n "$DOCR_REGISTRY" ]; then
    echo "Logging into DigitalOcean Container Registry..."
    su - "$CURRENT_USER" -c "doctl registry login" || {
        echo "WARNING: DOCR login failed. Run 'doctl auth init' first, then 'doctl registry login'."
    }
else
    echo "Skipping DOCR login (set DOCR_REGISTRY env var to enable)."
    echo "  Example: DOCR_REGISTRY=typhoon-registry sudo bash install.sh"
fi

print_section "Installation Complete"
echo "Server setup finished!"
echo ""
echo "Next steps:"
echo "  1. Edit your .env file:"
echo "     nano $ENV_FILE"
echo ""
echo "  2. Login to DigitalOcean Container Registry:"
echo "     doctl auth init"
echo "     doctl registry login"
echo ""
echo "  3. Start services (first time):"
echo "     cd $APP_DIR"
echo "     docker compose -f docker-compose.prod.yml up -d"
echo ""
echo "  4. (Optional) Set up SSL with a domain:"
echo "     sudo certbot --nginx -d yourdomain.com"
echo ""
echo "  5. Push to main branch — GitHub Actions will auto-deploy!"
echo ""
echo "Webhook URL: http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_PUBLIC_IP')"
echo "Done!"
