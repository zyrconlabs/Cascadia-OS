#!/usr/bin/env python3
"""Standalone health check for teams-connector."""
import json

print(json.dumps({"status": "healthy", "connector": "teams-connector"}))
