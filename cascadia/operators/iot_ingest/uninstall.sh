#!/usr/bin/env bash
# uninstall.sh — iot-ingest
set -euo pipefail
echo "[iot-ingest] Uninstalling..."
lsof -ti tcp:8300 | xargs kill -9 2>/dev/null || true
echo "[iot-ingest] Uninstall complete."
