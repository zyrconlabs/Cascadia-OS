#!/usr/bin/env bash
set -euo pipefail
echo "[connect] Installing dependencies..."
pip install --quiet nats-py
echo "[connect] Install complete."
