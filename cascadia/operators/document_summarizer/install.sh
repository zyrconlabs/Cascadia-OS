#!/usr/bin/env bash
set -euo pipefail
echo "[document-summarizer] Installing dependencies..."
pip install --quiet nats-py
echo "[document-summarizer] Install complete."
