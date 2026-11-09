#!/usr/bin/env bash
# Point Telegram at the Worker. Reads TELEGRAM_BOT_TOKEN and WEBHOOK_SECRET from the
# environment (never pass them as arguments). Pending updates are KEPT so nothing is lost.
# Usage: ./scripts/set-webhook.sh https://kakireportbot.<subdomain>.workers.dev
set -euo pipefail
URL="${1:?worker url}"
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" -H 'content-type: application/json' \
  -d "{\"url\":\"${URL%/}/telegram\",\"secret_token\":\"${WEBHOOK_SECRET}\",\"allowed_updates\":[\"message\",\"edited_message\",\"my_chat_member\",\"chat_member\"],\"max_connections\":1}"
echo
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" | sed -E 's/"url":"[^"]*"/"url":"(set)"/'
echo
