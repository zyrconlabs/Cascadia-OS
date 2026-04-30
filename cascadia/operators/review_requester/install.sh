#!/usr/bin/env bash
# install.sh — review-requester
set -euo pipefail
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[review-requester] Installing..."
pip install --quiet nats-py
echo "[review-requester] Install complete. Run: python3 ${OPERATOR_DIR}/operator.py"
