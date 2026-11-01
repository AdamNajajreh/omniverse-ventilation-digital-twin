#!/bin/bash
# Puts the stream on the internet. Run once as yourself, after `gcloud auth login`.
# It keeps the current address, opens the web and video ports, installs nginx with
# an HTTPS certificate, starts everything as system services, and sets a password.
set -euo pipefail

STREAM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE="$HOME/.local/opt/node-v22.11.0-linux-x64/bin/node"
METADATA=http://metadata.google.internal/computeMetadata/v1/instance
metadata() { curl -fs -H 'Metadata-Flavor: Google' "$METADATA/$1"; }

ADDRESS="$(metadata network-interfaces/0/access-configs/0/external-ip)"
INSTANCE="$(metadata name)"
ZONE="$(metadata zone | sed 's#.*/##')"
REGION="${ZONE%-*}"
HOST="${ADDRESS//./-}.sslip.io"
TAG=ventilation-stream

if gcloud config get-value account 2>/dev/null | grep -q gserviceaccount; then
    echo "Run this first, then start this script again:  gcloud auth login --no-launch-browser"
    exit 1
fi

echo "== Keeping $ADDRESS as a static address"
if ! gcloud compute addresses describe "$TAG" --region "$REGION" >/dev/null 2>&1; then
    gcloud compute addresses create "$TAG" --addresses "$ADDRESS" --region "$REGION"
fi

echo "== Opening HTTPS and the video port. Kit's signaling port stays closed."
gcloud compute instances add-tags "$INSTANCE" --zone "$ZONE" --tags "$TAG"
if ! gcloud compute firewall-rules describe "$TAG" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "$TAG" --direction INGRESS \
        --allow tcp:80,tcp:443,udp:47998 --source-ranges 0.0.0.0/0 --target-tags "$TAG"
fi

echo "== Installing nginx and certbot"
sudo apt-get update -qq
sudo apt-get install -y -qq nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/sites-available/ventilation-stream >/dev/null <<EOF
server {
    listen 80;
    server_name $HOST;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$http_connection;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 1h;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/ventilation-stream /etc/nginx/sites-enabled/ventilation-stream
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo "== Getting the HTTPS certificate for $HOST"
sudo certbot --nginx -d "$HOST" --non-interactive --agree-tos --register-unsafely-without-email --redirect

echo "== Installing the services"
sudo tee /etc/systemd/system/ventilation-kit.service >/dev/null <<EOF
[Unit]
Description=Ventilation Digital Twin stream
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
ExecStart=$STREAM_DIR/run-kit.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/ventilation-gateway.service >/dev/null <<EOF
[Unit]
Description=Ventilation Digital Twin password gateway
After=network-online.target

[Service]
User=$USER
WorkingDirectory=$STREAM_DIR
ExecStart=$NODE gateway.mjs
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

PASSWORD_LINE="$("$NODE" "$STREAM_DIR/gateway.mjs" --set-password)"
sudo systemctl daemon-reload
sudo systemctl enable --now ventilation-gateway ventilation-kit

echo
echo "Done. Other firewall rules on this project, check that none opens port 49100:"
gcloud compute firewall-rules list --format='table(name,sourceRanges.list(),allowed[].map().firewall_rule().list(),targetTags.list())'
echo
echo "Link:  https://$HOST"
echo "$PASSWORD_LINE"
echo "Keep the password safe. It is stored only as a hash and is not shown again."
