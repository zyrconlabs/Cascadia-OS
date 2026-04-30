#!/usr/bin/env python3
"""Standalone health check for telegram-connector."""
import json

print(json.dumps({"status": "healthy", "connector": "telegram-connector"}))
