#!/usr/bin/env bash
# Home Assistant add-on entry point for DoorOpener.
# The Supervisor writes the add-on configuration to /data/options.json; config.py
# reads it, and falls back to the Supervisor's own API token when ha_token is empty.
set -euo pipefail

export DOOROPENER_OPTIONS_PATH="/data/options.json"
export DOOROPENER_LOG_DIR="/data/logs"
export USERS_STORE_PATH="/data/users.json"

DOOROPENER_PORT="$(python3 -c "import json; print(json.load(open('/data/options.json')).get('port', 6532))")"
export DOOROPENER_PORT

echo "[dooropener] Starting DoorOpener on port ${DOOROPENER_PORT}..."
cd /app
# One worker: the brute-force counters live in memory and must see every request.
exec gunicorn \
  --bind "0.0.0.0:${DOOROPENER_PORT}" \
  --workers 1 \
  --threads 2 \
  --timeout 60 \
  app:app
