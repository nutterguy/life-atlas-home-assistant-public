#!/usr/bin/env bash
set -euo pipefail
umask 077

python3 /app/life_atlas/secrets_init.py

WAHA_INTERNAL_API_KEY="$(tr -d '\r\n' < /data/secrets/waha-api-key-hash)"
export WHATSAPP_API_KEY="$WAHA_INTERNAL_API_KEY"
export WHATSAPP_DEFAULT_ENGINE=NOWEB
export WAHA_LOCAL_STORE_BASE_DIR=/data/waha
export WAHA_DASHBOARD_ENABLED=false
export WHATSAPP_SWAGGER_ENABLED=false
export WAHA_APPS_ENABLED=false
export WAHA_RUN_XVFB=false
export WAHA_LOG_LEVEL=warn
export WHATSAPP_API_PORT=3000
export WHATSAPP_API_HOSTNAME=127.0.0.1
export PYTHONDONTWRITEBYTECODE=1

/entrypoint.sh &
WAHA_PID=$!
unset WAHA_INTERNAL_API_KEY WHATSAPP_API_KEY
python3 /app/life_atlas/adapter.py &
ADAPTER_PID=$!

shutdown() {
  kill "$ADAPTER_PID" "$WAHA_PID" 2>/dev/null || true
  wait "$ADAPTER_PID" "$WAHA_PID" 2>/dev/null || true
}
trap shutdown EXIT INT TERM

wait -n "$WAHA_PID" "$ADAPTER_PID"
EXIT_CODE=$?
exit "$EXIT_CODE"
