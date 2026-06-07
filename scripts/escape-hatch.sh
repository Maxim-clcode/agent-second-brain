#!/bin/bash
# Escape hatch: switch d-brain between the interactive session (subscription)
# and router mode (claude -p against an alternate provider) if Anthropic ever
# closes the interactive path. Reversible.
#
#   scripts/escape-hatch.sh on      # → router mode (needs a SEPARATE API key)
#   scripts/escape-hatch.sh off     # → interactive (subscription)
#   scripts/escape-hatch.sh status
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENVF="$PROJECT_DIR/.env"
[ -f "$ENVF" ] || { echo "No .env at $ENVF"; exit 1; }

_set_mode() {
    if grep -q '^DBRAIN_MODE=' "$ENVF"; then
        sed -i.bak "s/^DBRAIN_MODE=.*/DBRAIN_MODE=$1/" "$ENVF" && rm -f "$ENVF.bak"
    else
        echo "DBRAIN_MODE=$1" >> "$ENVF"
    fi
}

_restart() {
    systemctl --user restart dbrain-bot.service 2>/dev/null || true
}

case "${1:-status}" in
  on)
    if ! grep -qE '^ANTHROPIC_BASE_URL=.+' "$ENVF" || ! grep -qE '^ANTHROPIC_AUTH_TOKEN=.+' "$ENVF"; then
        echo "❌ Router mode needs ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN in .env."
        echo "   This must be a SEPARATE billed API key (NOT your subscription)."
        echo "   Get one at: https://console.anthropic.com/  (or your provider)."
        exit 1
    fi
    _set_mode router
    echo "🔀 Router mode ON (claude -p via alternate provider). Restarting bot…"
    _restart
    ;;
  off)
    _set_mode interactive
    echo "🟢 Interactive mode (subscription). Restarting bot…"
    _restart
    ;;
  status)
    grep '^DBRAIN_MODE=' "$ENVF" || echo "DBRAIN_MODE=interactive (default)"
    ;;
  *)
    echo "Usage: escape-hatch.sh <on|off|status>"
    exit 1
    ;;
esac
