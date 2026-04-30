#!/usr/bin/env bash
# uninstall.sh — appointment-scheduler
set -euo pipefail
echo "[appointment-scheduler] Uninstalling..."
lsof -ti tcp:8102 | xargs kill -9 2>/dev/null || true
echo "[appointment-scheduler] Uninstall complete."
