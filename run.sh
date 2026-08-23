#!/bin/sh
set -u

export LIFE_ATLAS_DATA_DIR=/data
export LIFE_ATLAS_HOST=0.0.0.0
export LIFE_ATLAS_PORT=8099
export LIFE_ATLAS_SERVER_ONLY=true

export PORT="${GOOGLE_PHOTOS_MCP_PORT:-3000}"
export NODE_ENV="${GOOGLE_PHOTOS_MCP_NODE_ENV:-development}"

MCP_DIR=/opt/google-photos-mcp
MCP_ENTRY="$MCP_DIR/dist/index.js"

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
node "$MCP_ENTRY" &
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
