#!/usr/bin/env bash
# Agent HQ server setup. Run on a fresh Ubuntu server as root:
#   bash <(curl -fsSL https://raw.githubusercontent.com/goodvibesncreations-hue/agent-hq/main/setup.sh)
# Running it again later updates everything and keeps your settings.
set -euo pipefail

REPO_URL="https://github.com/goodvibesncreations-hue/agent-hq.git"
DIR=/opt/agent-hq

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then echo "Please run as root."; exit 1; fi

say "Installing Docker and Git (takes 1-2 minutes)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ufw >/dev/null
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh >/dev/null; fi

if [ ! -f /swapfile ]; then
  say "Adding 4 GB of swap so transcription doesn't run out of memory"
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "Getting the Agent HQ code"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone -q "$REPO_URL" "$DIR"; fi
cd "$DIR"
mkdir -p data

if [ ! -f .env ]; then
  say "A few questions (only asked once)"
  IP=$(curl -fsS http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || curl -fsS https://api.ipify.org)
  DASHED=${IP//./-}
  read -rp "Paste your Anthropic API key (starts with sk-ant-), or press Enter to skip for now: " AKEY < /dev/tty
  while true; do
    read -rsp "Choose a password for Clip Studio (8+ characters): " PW < /dev/tty; echo
    [ ${#PW} -ge 8 ] && break
    echo "Too short, try again."
  done
  cat > .env <<EOF
ANTHROPIC_API_KEY=${AKEY}
N8N_DOMAIN=n8n.${DASHED}.sslip.io
CLIPS_DOMAIN=clips.${DASHED}.sslip.io
N8N_ENCRYPTION_KEY=$(openssl rand -hex 24)
TIMEZONE=America/New_York
WHISPER_MODEL=base
DEFAULT_CLIPS=5
EOF
  chmod 600 .env
  HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$PW")
  cat > Caddyfile <<EOF
n8n.${DASHED}.sslip.io {
  reverse_proxy n8n:5678
}

clips.${DASHED}.sslip.io {
  basic_auth {
    admin ${HASH}
  }
  reverse_proxy clipper:8000
}
EOF
fi

say "Turning on the firewall (web + SSH only)"
ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

say "Building and starting the agents (first time takes about 5 minutes)"
docker compose build --pull clipper
docker compose up -d

set -a; . ./.env; set +a
cat <<EOF

=====================================================
  Agent HQ is running.

  Clip Studio:  https://${CLIPS_DOMAIN}
                user: admin   password: the one you chose

  n8n:          https://${N8N_DOMAIN}
                (create your n8n owner account on first visit)

  The secure (https) certificate can take a minute to
  appear. If a page doesn't load, wait 60s and refresh.

  To update later, run the same setup command again.
=====================================================
EOF
