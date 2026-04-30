#!/usr/bin/env bash
# install.sh — intake-form
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[intake-form] Installing..."
pip install --quiet nats-py
echo "[intake-form] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
