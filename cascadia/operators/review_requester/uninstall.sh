#!/usr/bin/env bash
# uninstall.sh — review-requester
set -euo pipefail
echo "[review-requester] Uninstalling...]"
lsof -ti tcp:8104 | xargs kill -9 2>/dev/null || true
echo "[review-requester] Uninstall complete."
