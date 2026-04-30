#!/usr/bin/env bash
# install.sh — invoice-generator
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[invoice-generator] Installing..."
pip install --quiet nats-py
mkdir -p ~/invoices
echo "[invoice-generator] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
