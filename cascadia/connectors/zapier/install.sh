#!/usr/bin/env bash
set -euo pipefail
echo "[zapier-connector] Installing dependencies..."
pip install nats-py --quiet
echo "[zapier-connector] install complete"
