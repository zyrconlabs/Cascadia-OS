#!/usr/bin/env bash
set -euo pipefail
if [ -z "${GOOGLE_CLIENT_ID:-}" ] || [ -z "${GOOGLE_CLIENT_SECRET:-}" ]; then
    echo "[google-connector] WARNING: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set"
    echo "[google-connector] See cascadia/connectors/google/README.md for setup instructions"
fi
echo "[google-connector] install complete"
