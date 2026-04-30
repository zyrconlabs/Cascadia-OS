#!/usr/bin/env bash
set -euo pipefail
echo "[social-scheduler] Installing dependencies..."
pip install --quiet nats-py
echo "[social-scheduler] Install complete."
