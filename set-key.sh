#!/usr/bin/env bash
# Add, change or check AI keys:  bash /opt/agent-hq/set-key.sh
set -euo pipefail
cd "$(dirname "$0")"

clean() {  # strip invisible paste characters, quotes and spaces
  printf '%s' "$1" | sed -e 's/\x1b\[20[01]~//g' -e 's/\^\[\[20[01]~//g' -e 's/\[20[01]~//g' \
    | tr -d '\r\n\t "'"'"'' | tr -cd '[:print:]'
}
mask() { local k="$1"; [ -z "$k" ] && echo "(empty)" || echo "${k:0:6}...${k: -4}  (${#k} characters)"; }
show() {
  echo; echo "Saved keys:"
  for V in GEMINI_API_KEY GROQ_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY; do
    K=$(grep "^${V}=" .env | cut -d= -f2- || true); printf '  %-18s %s\n' "$V" "$(mask "$K")"
  done; echo
}

show
echo "Which key do you want to set?"
echo "  1) Gemini  (starts with AIza)"
echo "  2) Groq    (starts with gsk_)"
echo "  3) Claude  (starts with sk-ant-)"
echo "  4) OpenAI  (starts with sk-)"
echo "  5) Nothing, just show me what's saved"
read -rp "Number: " N < /dev/tty
case "$N" in
  1) VAR=GEMINI_API_KEY; PRE=AIza ;; 2) VAR=GROQ_API_KEY; PRE=gsk_ ;;
  3) VAR=ANTHROPIC_API_KEY; PRE=sk-ant- ;; 4) VAR=OPENAI_API_KEY; PRE=sk- ;;
  5) exit 0 ;; *) echo "Please run again and type a number from 1 to 5."; exit 1 ;;
esac
read -rp "Paste the key (or press Enter to remove it): " RAW < /dev/tty
KEY=$(clean "$RAW")
if [ -n "$KEY" ] && [[ "$KEY" != "$PRE"* ]]; then
  echo
  echo "That doesn't look like this kind of key: it should start with $PRE"
  echo "but it starts with ${KEY:0:6}. Nothing was saved."
  case "$KEY" in AIza*) echo "(It looks like a Gemini key. Choose option 1.)";; gsk_*) echo "(It looks like a Groq key. Choose option 2.)";;
    sk-ant-*) echo "(It looks like a Claude key. Choose option 3.)";; esac
  exit 1
fi
if grep -q "^${VAR}=" .env; then sed -i "s|^${VAR}=.*|${VAR}=${KEY}|" .env; else echo "${VAR}=${KEY}" >> .env; fi
docker compose up -d clipper >/dev/null 2>&1
echo; echo "Saved: $(mask "$KEY")"
echo "Now open Clip Studio and tap Test AI keys, then Test again."
