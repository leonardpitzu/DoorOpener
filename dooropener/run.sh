#!/usr/bin/env bash
# Home Assistant app entry point for DoorOpener.
# The Supervisor writes the app configuration to /data/options.json; config.py
# reads it, and falls back to the Supervisor's own API token when ha_token is empty.
# Server settings (port, HTTPS, the single worker) are in gunicorn.conf.py, which
# the standalone container shares.
set -euo pipefail

export DOOROPENER_OPTIONS_PATH="/data/options.json"
export DOOROPENER_LOG_DIR="/data/logs"
export USERS_STORE_PATH="/data/users.json"
export DOOROPENER_SECRET_KEY_FILE="/data/secret_key"
export DOOROPENER_PIN_KEY_FILE="/data/pin_key"

echo "[dooropener] Starting DoorOpener..."
cd /app
exec gunicorn app:app
