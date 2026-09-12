#!/usr/bin/env bash
set -u

export LIFE_ATLAS_DATA_DIR=/data
export LIFE_ATLAS_HOST=0.0.0.0
export LIFE_ATLAS_PORT=8099
export LIFE_ATLAS_BACKEND_PORT=8100
export LIFE_ATLAS_SERVER_ONLY=true
export LIFE_ATLAS_SEED_SAMPLE=true
export LIFE_ATLAS_VERSION=0.14.4
export LIFE_ATLAS_REFERENCE_CONNECTOR_URL="${LIFE_ATLAS_REFERENCE_CONNECTOR_URL:-http://local-life-atlas-reference-connector:8098}"
export LIFE_ATLAS_WHATSAPP_DATA=/data/whatsapp
export LIFE_ATLAS_OPTIONS_FILE=/data/options.json
export LIFE_ATLAS_WHATSAPP_CONNECTOR_URL=http://127.0.0.1:8097
export LIFE_ATLAS_WHATSAPP_CONNECTOR_KEY_FILE=/data/whatsapp/secrets/connector-api-key
export LIFE_ATLAS_WHATSAPP_CONNECTOR_HOST=127.0.0.1
export LIFE_ATLAS_WHATSAPP_CONNECTOR_PORT=8097
export LIFE_ATLAS_WHATSAPP_MANAGEMENT_HOST=127.0.0.1
export LIFE_ATLAS_WHATSAPP_MANAGEMENT_PORT=8110
export PYTHONDONTWRITEBYTECODE=1

read_option() {
  python3 - "$1" <<'PYTHON'
import json
import sys

try:
    with open("/data/options.json", encoding="utf-8") as handle:
        options = json.load(handle)
except (FileNotFoundError, json.JSONDecodeError, OSError):
    options = {}

value = options.get(sys.argv[1], "")
if isinstance(value, str):
    print(value, end="")
PYTHON
}

agent_api_key="$(read_option agent_api_key)"
if [ -n "$agent_api_key" ]; then
  export LIFE_ATLAS_AGENT_API_KEY="$agent_api_key"
fi
unset agent_api_key

MCP_DIR=/opt/google-photos-mcp
MCP_ENTRY="$MCP_DIR/dist/index.js"
MCP_DATA_DIR=/data/google-photos-mcp
MCP_RUNTIME_DATA="$MCP_DIR/runtime-data"

mkdir -p "$MCP_DATA_DIR"
chmod 700 "$MCP_DATA_DIR"
ln -sfn "$MCP_DATA_DIR" "$MCP_RUNTIME_DATA"
export TOKEN_STORAGE_PATH="runtime-data/tokens.db"

google_client_id="$(read_option google_photos_mcp_client_id)"
google_client_secret="$(read_option google_photos_mcp_client_secret)"
google_redirect_uri="$(read_option google_photos_mcp_redirect_uri)"
if [ -n "$google_client_id" ]; then
  export GOOGLE_CLIENT_ID="$google_client_id"
fi
if [ -n "$google_client_secret" ]; then
  export GOOGLE_CLIENT_SECRET="$google_client_secret"
fi
if [ -n "$google_redirect_uri" ]; then
  export GOOGLE_REDIRECT_URI="$google_redirect_uri"
fi
unset google_client_id google_client_secret google_redirect_uri

: "${GOOGLE_REDIRECT_URI:=http://localhost:3000/auth/callback}"
export GOOGLE_REDIRECT_URI

python3 /opt/life-atlas/whatsapp_archive/secrets_init.py
WAHA_INTERNAL_API_KEY="$(tr -d '\r\n' < /data/whatsapp/secrets/waha-api-key-hash)"
export WHATSAPP_API_KEY="$WAHA_INTERNAL_API_KEY"
export WHATSAPP_DEFAULT_ENGINE=NOWEB
export WAHA_LOCAL_STORE_BASE_DIR=/data/whatsapp/waha
export WAHA_DASHBOARD_ENABLED=false
export WHATSAPP_SWAGGER_ENABLED=false
export WAHA_APPS_ENABLED=false
export WAHA_RUN_XVFB=false
export WAHA_LOG_LEVEL=warn
export WHATSAPP_API_PORT=3001
export WHATSAPP_API_HOSTNAME=127.0.0.1

wait_for_backend() {
  python3 - "$LIFE_ATLAS_BACKEND_PORT" <<'PYTHON'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 60
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            sys.exit(0)
    except OSError:
        time.sleep(0.25)
sys.exit(1)
PYTHON
}

terminate_children() {
  if [ -n "${PROXY_PID:-}" ]; then
    kill "$PROXY_PID" 2>/dev/null || true
  fi
  if [ -n "${APP_PID:-}" ]; then
    kill "$APP_PID" 2>/dev/null || true
  fi
  if [ -n "${AGENT_PID:-}" ]; then
    kill "$AGENT_PID" 2>/dev/null || true
  fi
  if [ -n "${MCP_PID:-}" ]; then
    kill "$MCP_PID" 2>/dev/null || true
  fi
  if [ -n "${WHATSAPP_PID:-}" ]; then
    kill "$WHATSAPP_PID" 2>/dev/null || true
  fi
  if [ -n "${WAHA_PID:-}" ]; then
    kill "$WAHA_PID" 2>/dev/null || true
  fi
}

trap terminate_children INT TERM EXIT

cd /app
/entrypoint.sh &
WAHA_PID=$!
unset WAHA_INTERNAL_API_KEY WHATSAPP_API_KEY

cd /opt/life-atlas/whatsapp_archive
python3 adapter.py &
WHATSAPP_PID=$!

cd "$MCP_DIR"
(
  umask 077
  PORT="${GOOGLE_PHOTOS_MCP_PORT:-3000}" \
    NODE_ENV="${GOOGLE_PHOTOS_MCP_NODE_ENV:-development}" \
    exec /usr/local/bin/node22 "$MCP_ENTRY"
) &
MCP_PID=$!

cd /opt/life-atlas
LIFE_ATLAS_HOST=127.0.0.1 LIFE_ATLAS_PORT="$LIFE_ATLAS_BACKEND_PORT" python3 /opt/life-atlas/runtime_entry.py &
APP_PID=$!

# The backend binds its port only after app.initialise() has created and migrated
# the database. Waiting for that port keeps the agent API from running a second,
# concurrent initialise() against an empty /data, which can leave one process
# dead with "database is locked" on a first start.
if ! wait_for_backend; then
  echo "Life Atlas backend did not become ready in time; stopping add-on." >&2
  terminate_children
  trap - INT TERM EXIT
  exit 1
fi

python3 /opt/life-atlas/agent_api.py &
AGENT_PID=$!

python3 /opt/life-atlas/mcp_ingress_proxy.py &
PROXY_PID=$!

status=0
while true; do
  if ! kill -0 "$PROXY_PID" 2>/dev/null; then
    wait "$PROXY_PID"
    status=$?
    [ "$status" -eq 0 ] && status=1
    echo "Life Atlas ingress proxy stopped unexpectedly; stopping add-on." >&2
    break
  fi

  if ! kill -0 "$APP_PID" 2>/dev/null; then
    wait "$APP_PID"
    status=$?
    [ "$status" -eq 0 ] && status=1
    echo "Life Atlas backend stopped unexpectedly; stopping add-on." >&2
    break
  fi

  if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    wait "$AGENT_PID"
    status=$?
    [ "$status" -eq 0 ] && status=1
    echo "Life Atlas agent API stopped unexpectedly; stopping add-on." >&2
    break
  fi

  if ! kill -0 "$MCP_PID" 2>/dev/null; then
    wait "$MCP_PID"
    status=$?
    [ "$status" -eq 0 ] && status=1
    echo "Google Photos MCP stopped unexpectedly; stopping Life Atlas." >&2
    break
  fi

  if ! kill -0 "$WHATSAPP_PID" 2>/dev/null; then
    wait "$WHATSAPP_PID"
    status=$?
    [ "$status" -eq 0 ] && status=1
    echo "WhatsApp archive adapter stopped unexpectedly; stopping Life Atlas." >&2
    break
  fi

  if ! kill -0 "$WAHA_PID" 2>/dev/null; then
    wait "$WAHA_PID"
    status=$?
    [ "$status" -eq 0 ] && status=1
    echo "WAHA stopped unexpectedly; stopping Life Atlas." >&2
    break
  fi

  sleep 2
done

terminate_children
trap - INT TERM EXIT
wait "$PROXY_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
wait "$AGENT_PID" 2>/dev/null || true
wait "$MCP_PID" 2>/dev/null || true
wait "$WHATSAPP_PID" 2>/dev/null || true
wait "$WAHA_PID" 2>/dev/null || true
exit "$status"
