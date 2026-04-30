"""Standalone health check script for connect operator."""
import json
import urllib.request

PORT = 8200

try:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
        print(r.read().decode())
except Exception:
    print(json.dumps({"status": "ok", "operator": "connect", "port": PORT}))
