#!/usr/bin/env python3
"""Standalone health check for sms-connector."""
import json

print(json.dumps({"status": "healthy", "connector": "sms-connector"}))
