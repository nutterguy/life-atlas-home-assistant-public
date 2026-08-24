#!/bin/sh
set -e

export LIFE_ATLAS_DATA_DIR=/data
export LIFE_ATLAS_HOST=0.0.0.0
export LIFE_ATLAS_PORT=8099
export LIFE_ATLAS_SERVER_ONLY=true
export LIFE_ATLAS_SEED_SAMPLE=true
export LIFE_ATLAS_VERSION=0.6.3
export LIFE_ATLAS_REFERENCE_CONNECTOR_URL="${LIFE_ATLAS_REFERENCE_CONNECTOR_URL:-http://local-life-atlas-reference-connector:8098}"

exec python3 /app/runtime_entry.py
