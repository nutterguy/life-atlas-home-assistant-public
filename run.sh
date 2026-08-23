#!/bin/sh
set -e

export LIFE_ATLAS_DATA_DIR=/data
export LIFE_ATLAS_HOST=0.0.0.0
export LIFE_ATLAS_PORT=8099
export LIFE_ATLAS_SERVER_ONLY=true

exec python3 /app/app.py
