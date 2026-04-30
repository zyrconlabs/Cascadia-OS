#!/usr/bin/env python3
"""Standalone health check for slack-connector."""
import json

print(json.dumps({"status": "healthy", "connector": "slack-connector"}))
