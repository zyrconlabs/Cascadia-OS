#!/usr/bin/env bash
# uninstall.sh — followup-sequence
set -euo pipefail
echo "[followup-sequence] Uninstalling..."
lsof -ti tcp:8103 | xargs kill -9 2>/dev/null || true
echo "[followup-sequence] Uninstall complete."
