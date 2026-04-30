#!/usr/bin/env bash
# CON-108 REST Connector — install hook
set -euo pipefail
echo "[rest-connector] Installing..."
pip install nats-py --quiet
echo "[rest-connector] Starting on port 9980..."
nohup python3 "$(dirname "$0")/connector.py" > /tmp/rest-connector.log 2>&1 &
echo $! > /tmp/rest-connector.pid
echo "[rest-connector] Started (PID $(cat /tmp/rest-connector.pid))"
