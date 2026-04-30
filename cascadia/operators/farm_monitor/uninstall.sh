#!/usr/bin/env bash
# uninstall.sh — farm-monitor
set -euo pipefail
echo "[farm-monitor] Uninstalling..."
lsof -ti tcp:8302 | xargs kill -9 2>/dev/null || true
echo "[farm-monitor] Uninstall complete."
