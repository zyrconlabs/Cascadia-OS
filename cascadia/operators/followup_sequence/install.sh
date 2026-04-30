#!/usr/bin/env bash
# install.sh — followup-sequence
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[followup-sequence] Installing..."
pip install --quiet nats-py
echo "[followup-sequence] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
