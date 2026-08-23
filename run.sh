#!/usr/bin/env bash
set -u

source /usr/lib/bashio/bashio.sh

export LIFE_ATLAS_DATA_DIR=/data
export LIFE_ATLAS_HOST=0.0.0.0
export LIFE_ATLAS_PORT=8099
export LIFE_ATLAS_SERVER_ONLY=true

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
python3 /app/app.py &
APP_PID=$!

status=0
while true; do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    wait "$APP_PID"
    status=$?
    break
  fi

  if ! kill -0 "$MCP_PID" 2>/dev/null; then
    wait "$MCP_PID"
    status=$?
    if [ "$status" -eq 0 ]; then
      status=1
    fi
    echo "Google Photos MCP stopped unexpectedly; stopping Life Atlas." >&2
    break
  fi

  sleep 2
done

terminate_children
trap - INT TERM EXIT
wait "$APP_PID" 2>/dev/null || true
wait "$MCP_PID" 2>/dev/null || true
exit "$status"
