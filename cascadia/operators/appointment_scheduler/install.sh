#!/usr/bin/env bash
# install.sh — appointment-scheduler
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[appointment-scheduler] Installing..."
pip install --quiet nats-py
echo "[appointment-scheduler] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
