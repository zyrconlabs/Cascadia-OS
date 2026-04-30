#!/usr/bin/env bash
# install.sh — iot-registry
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[iot-registry] Installing..."
pip install --quiet nats-py
echo "[iot-registry] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
