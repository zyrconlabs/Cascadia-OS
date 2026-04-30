#!/usr/bin/env bash
# install.sh — iot-ingest
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[iot-ingest] Installing..."
pip install --quiet nats-py
echo "[iot-ingest] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
