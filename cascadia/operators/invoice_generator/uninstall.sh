#!/usr/bin/env bash
# uninstall.sh — invoice-generator
set -euo pipefail
echo "[invoice-generator] Uninstalling..."
# Kill any running instance on port 8101
lsof -ti tcp:8101 | xargs kill -9 2>/dev/null || true
echo "[invoice-generator] Uninstall complete."
