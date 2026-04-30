#!/usr/bin/env python3
"""Standalone health check for whatsapp-connector."""
import json

print(json.dumps({"status": "healthy", "connector": "whatsapp-connector"}))
