#!/usr/bin/env bash
# CON-108 REST Connector — uninstall hook
set -euo pipefail
echo "[rest-connector] Stopping..."
if [ -f /tmp/rest-connector.pid ]; then
    kill "$(cat /tmp/rest-connector.pid)" 2>/dev/null || true
    rm -f /tmp/rest-connector.pid
fi
echo "[rest-connector] Stopped."
