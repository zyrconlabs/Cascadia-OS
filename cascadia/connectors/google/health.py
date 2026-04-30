#!/usr/bin/env python3
"""Standalone health check for google-connector."""
import json
import sys
import urllib.request

PORT = 9020

try:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as resp:
        data = json.loads(resp.read())
        print(json.dumps({"status": "healthy", "connector": "google-connector", "port": PORT, "detail": data}))
except Exception as e:
    print(json.dumps({"status": "unhealthy", "connector": "google-connector", "port": PORT, "error": str(e)}))
    sys.exit(1)
