#!/bin/bash
# Send a one-line alert to the admin Telegram chat. Standalone (only needs
# curl + .env), so it works as a systemd OnFailure handler even when the bot
# and watchdog are down. Usage: notify.sh "message"
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
[ -f "$ENV_FILE" ] && export $(grep -v '^#' "$ENV_FILE" | xargs) || true

MSG="${1:-d-brain alert}"
CHAT_ID="${ALLOWED_USER_IDS//[\[\]]/}"
CHAT_ID="${CHAT_ID%%,*}"  # first id only

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "$CHAT_ID" ]; then
    echo "notify: missing token or chat id" >&2
    exit 0
fi

curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
    -d "chat_id=$CHAT_ID" -d "text=$MSG" >/dev/null || true
