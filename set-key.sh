#!/usr/bin/env bash
# Add or change an AI key:  bash /opt/agent-hq/set-key.sh
set -euo pipefail
cd "$(dirname "$0")"
echo "Which key?  1) Gemini (free)  2) Groq (free)  3) Anthropic/Claude  4) OpenAI"
read -rp "Number: " N < /dev/tty
case "$N" in
  1) VAR=GEMINI_API_KEY ;; 2) VAR=GROQ_API_KEY ;; 3) VAR=ANTHROPIC_API_KEY ;; 4) VAR=OPENAI_API_KEY ;;
  *) echo "Please run again and type 1, 2, 3 or 4."; exit 1 ;;
esac
read -rp "Paste the key (or press Enter to remove it): " KEY < /dev/tty
if grep -q "^${VAR}=" .env; then sed -i "s|^${VAR}=.*|${VAR}=${KEY}|" .env; else echo "${VAR}=${KEY}" >> .env; fi
docker compose up -d clipper
echo "Saved. The next video will use it."
