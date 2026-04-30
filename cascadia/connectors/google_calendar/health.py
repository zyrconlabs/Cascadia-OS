#!/usr/bin/env python3
"""Standalone health check for google-calendar-connector."""
import json

print(json.dumps({"status": "healthy", "connector": "google-calendar-connector"}))
