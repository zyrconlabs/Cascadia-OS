#!/usr/bin/env python3
"""Standalone health check for zapier-connector."""
import json

print(json.dumps({"status": "ok", "connector": "zapier-connector"}))
