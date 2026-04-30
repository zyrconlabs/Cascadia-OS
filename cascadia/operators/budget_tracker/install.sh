#!/usr/bin/env bash
set -euo pipefail
echo "[budget-tracker] Installing dependencies..."
pip install --quiet nats-py
echo "[budget-tracker] Install complete."
