#!/usr/bin/env python3
"""Standalone health check for appointment-scheduler (port 8102)."""
import json
import sys
import urllib.request

PORT = 8102
NAME = "appointment-scheduler"

try:
    with urllib.request.urlopen(f"http://localhost:{PORT}/health", timeout=3) as resp:
        data = json.loads(resp.read())
        if data.get("status") == "ok":
            print(json.dumps(data, indent=2))
            sys.exit(0)
        else:
            print(f"Unhealthy: {data}", file=sys.stderr)
            sys.exit(1)
except Exception as exc:
    print(f"Health check failed for {NAME}:{PORT} — {exc}", file=sys.stderr)
    sys.exit(1)
