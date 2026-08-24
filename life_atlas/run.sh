#!/usr/bin/env bash
set -u

source /usr/lib/bashio/bashio.sh

export LIFE_ATLAS_DATA_DIR=/data
export LIFE_ATLAS_HOST=0.0.0.0
export LIFE_ATLAS_PORT=8099
export LIFE_ATLAS_BACKEND_PORT=8100
export LIFE_ATLAS_SERVER_ONLY=true
export LIFE_ATLAS_SEED_SAMPLE=true
export LIFE_ATLAS_VERSION=0.8.1
export LIFE_ATLAS_REFERENCE_CONNECTOR_URL="${LIFE_ATLAS_REFERENCE_CONNECTOR_URL:-http://local-life-atlas-reference-connector:8098}"

export PORT="${GOOGLE_PHOTOS_MCP_PORT:-3000}"
export NODE_ENV="${GOOGLE_PHOTOS_MCP_NODE_ENV:-development}"

MCP_DIR=/opt/google-photos-mcp
MCP_ENTRY="$MCP_DIR/dist/index.js"
MCP_DATA_DIR=/data/google-photos-mcp
MCP_RUNTIME_DATA="$MCP_DIR/runtime-data"

mkdir -p "$MCP_DATA_DIR"
chmod 700 "$MCP_DATA_DIR"
ln -sfn "$MCP_DATA_DIR" "$MCP_RUNTIME_DATA"
export TOKEN_STORAGE_PATH="runtime-data/tokens.db"

if bashio::config.has_value 'google_photos_mcp_client_id'; then
  export GOOGLE_CLIENT_ID="$(bashio::config 'google_photos_mcp_client_id')"
fi
if bashio::config.has_value 'google_photos_mcp_client_secret'; then
  export GOOGLE_CLIENT_SECRET="$(bashio::config 'google_photos_mcp_client_secret')"
fi
if bashio::config.has_value 'google_photos_mcp_redirect_uri'; then
  export GOOGLE_REDIRECT_URI="$(bashio::config 'google_photos_mcp_redirect_uri')"
fi

: "${GOOGLE_REDIRECT_URI:=http://localhost:3000/auth/callback}"
export GOOGLE_REDIRECT_URI

terminate_children() {
  if [ -n "${PROXY_PID:-}" ]; then
    kill "$PROXY_PID" 2>/dev/null || true
  fi
  if [ -n "${APP_PID:-}" ]; then
    kill "$APP_PID" 2>/dev/null || true
  fi
  if [ -n "${MCP_PID:-}" ]; then
    kill "$MCP_PID" 2>/dev/null || true
  fi
}

trap terminate_children INT TERM EXIT

cd "$MCP_DIR"
(
  umask 077
  exec node "$MCP_ENTRY"
) &
MCP_PID=$!

cd /app
LIFE_ATLAS_HOST=127.0.0.1 LIFE_ATLAS_PORT="$LIFE_ATLAS_BACKEND_PORT" python3 /app/runtime_entry.py &
APP_PID=$!

python3 /app/mcp_ingress_proxy.py &
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

  if ! kill -0 "$MCP_PID" 2>/dev/null; then
    wait "$MCP_PID"
    status=$?
    [ "$status" -eq 0 ] && status=1
    echo "Google Photos MCP stopped unexpectedly; stopping Life Atlas." >&2
    break
  fi

  sleep 2
done

terminate_children
trap - INT TERM EXIT
wait "$PROXY_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
wait "$MCP_PID" 2>/dev/null || true
exit "$status"
