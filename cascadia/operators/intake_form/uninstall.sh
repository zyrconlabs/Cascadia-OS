#!/usr/bin/env bash
# uninstall.sh — intake-form
set -euo pipefail
echo "[intake-form] Uninstalling..."
lsof -ti tcp:8105 | xargs kill -9 2>/dev/null || true
echo "[intake-form] Uninstall complete."
