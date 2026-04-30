#!/usr/bin/env bash
set -euo pipefail
TOKEN_FILE="${GOOGLE_TOKEN_FILE:-$HOME/.cascadia/google_tokens.json}"
if [ -f "$TOKEN_FILE" ]; then
    echo "[google-connector] WARNING: stored tokens remain at $TOKEN_FILE"
    echo "[google-connector] To fully revoke access, run the revoke_token action before uninstalling"
fi
echo "[google-connector] uninstall complete"
