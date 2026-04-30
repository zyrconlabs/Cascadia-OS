#!/usr/bin/env bash
# install.sh — farm-monitor
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[farm-monitor] Installing..."
pip install --quiet nats-py
echo "[farm-monitor] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
