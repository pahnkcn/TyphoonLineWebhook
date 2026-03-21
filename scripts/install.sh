#!/bin/bash

# ใจดี Chatbot - Azure Ubuntu 24.04 LTS Installation Script
# This script installs and configures all necessary components for the chatbot application
# on an Azure Ubuntu Server 24.04 LTS (Gen2 x64)

set -e  # Exit immediately if a command exits with a non-zero status

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

# Set working directory
APP_DIR="/opt/chatbot"
ENV_FILE="$APP_DIR/.env"

print_section "System Update & Basic Packages"
# Update and upgrade system
apt-get update && apt-get upgrade -y

# Install basic utilities
apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    nano \
    unzip \
    supervisor

print_section "Installing Docker"
# Remove older versions if they exist
apt-get remove -y docker docker-engine docker.io containerd runc || true

# Install Docker repository
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io

# Install Docker Compose
DOCKER_COMPOSE_VERSION=v2.24.0
curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Add current user to docker group
usermod -aG docker $CURRENT_USER
echo "Docker installed successfully. Added $CURRENT_USER to docker group."

print_section "Setting Up Application Directory"
# Create application directory
mkdir -p $APP_DIR
cd $APP_DIR

# Clone or create application files
git clone https://github.com/yourusername/chatbot.git $APP_DIR || {
    echo "Failed to clone repository. Creating directory structure manually."
    mkdir -p $APP_DIR/{logs,data}
}

print_section "Setting Up Environment Variables"
# Create .env file from example if it doesn't exist
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$ENV_FILE"
        echo "Created .env file from example. Please update with your actual values."
    else
        cat > "$ENV_FILE" << EOF
# LINE API Credentials
LINE_CHANNEL_ACCESS_TOKEN=your_token_here
LINE_CHANNEL_SECRET=your_secret_here

# xAI Grok API Configuration
XAI_API_KEY=your_api_key_here

# Redis Configuration
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# MySQL Configuration
MYSQL_HOST=db
MYSQL_PORT=3306
MYSQL_USER=chatbot
MYSQL_PASSWORD=$(openssl rand -hex 12)
MYSQL_DB=chatbot
MYSQL_ROOT_PASSWORD=$(openssl rand -hex 16)

# Application Settings
ENVIRONMENT=production
LOG_LEVEL=INFO
EOF
        echo "Created default .env file with auto-generated passwords. Please update with your actual values."
    fi
    
    # Set appropriate permissions
    chmod 600 "$ENV_FILE"
    chown $CURRENT_USER:$CURRENT_USER "$ENV_FILE"
else
    echo ".env file already exists. Skipping creation."
fi

print_section "Setting Up Docker Services"
# Create or update docker-compose.yml
cat > "$APP_DIR/docker-compose.yml" << 'EOF'
version: '3'
services:
  web:
    build: .
    ports:
      - "5000:5000"
    restart: always
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - MYSQL_HOST=db
      - MYSQL_PORT=3306
      - TZ=Asia/Bangkok
    depends_on:
      - redis
      - db

  redis:
    image: redis:6-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  db:
    image: mysql:8.0
    command: --default-authentication-plugin=mysql_native_password
    restart: always
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: ${MYSQL_DB}
      MYSQL_USER: ${MYSQL_USER}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - db_data:/var/lib/mysql
    ports:
      - "3306:3306"

volumes:
  redis_data:
  db_data:
EOF

print_section "Setting Up Nginx Reverse Proxy"
# Install Nginx
apt-get install -y nginx certbot python3-certbot-nginx

# Configure Nginx for the chatbot
cat > /etc/nginx/sites-available/chatbot << 'EOF'
server {
    listen 80;
    server_name _;  # Default server for any hostname or IP

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# Enable the site
ln -sf /etc/nginx/sites-available/chatbot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
nginx -t && systemctl reload nginx

print_section "Setting Up Supervisor"
# Create supervisor configuration for manual Python deployment (alternative to Docker)
cat > /etc/supervisor/conf.d/chatbot.conf << EOF
[program:chatbot]
command=/usr/bin/python3 $APP_DIR/app_main.py
directory=$APP_DIR
autostart=false
autorestart=true
startretries=5
stderr_logfile=$APP_DIR/logs/supervisor.err.log
stdout_logfile=$APP_DIR/logs/supervisor.out.log
user=$CURRENT_USER
environment=
    PATH="/usr/local/bin:/usr/bin:/bin",
    PYTHONUNBUFFERED="1"
EOF

supervisorctl reread
supervisorctl update

# Set proper ownership of app directory
chown -R $CURRENT_USER:$CURRENT_USER $APP_DIR

print_section "Setting Up Security"
# Basic firewall setup
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable

print_section "Installation Complete"
echo "Chatbot installation completed!"
echo "Next steps:"
echo "1. Edit your .env file: nano $ENV_FILE"
echo "2. Start the application using Docker: cd $APP_DIR && docker-compose up -d"
echo
echo "Your webhook URL for LINE configuration will be: http://YOUR_PUBLIC_IP"
echo
echo "NOTE: For secure HTTPS connections with just an IP address:"
echo "- You can use a self-signed certificate: 'sudo certbot --nginx' and follow prompts"
echo "- But LINE webhook requires valid SSL certificates, so consider using a service like ngrok"
echo "  for development or obtaining a proper domain for production use."

# Apply ownership again to be extra sure
chown -R $CURRENT_USER:$CURRENT_USER $APP_DIR
chmod +x "$APP_DIR/app_main.py" 2>/dev/null || echo "No app_main.py file found yet."

echo "Done!"
