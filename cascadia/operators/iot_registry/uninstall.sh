#!/usr/bin/env bash
# uninstall.sh — iot-registry
set -euo pipefail
echo "[iot-registry] Uninstalling..."
lsof -ti tcp:8301 | xargs kill -9 2>/dev/null || true
echo "[iot-registry] Uninstall complete."
