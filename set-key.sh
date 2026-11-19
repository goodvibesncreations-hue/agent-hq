#!/usr/bin/env bash
# Add or change the Anthropic API key:  bash /opt/agent-hq/set-key.sh
set -euo pipefail
cd "$(dirname "$0")"
read -rp "Paste your Anthropic API key: " KEY < /dev/tty
sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${KEY}|" .env
docker compose up -d clipper
echo "Saved. The AI editor will use it for the next video."
