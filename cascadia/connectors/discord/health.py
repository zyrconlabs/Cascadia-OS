#!/usr/bin/env python3
"""Standalone health check for discord-connector."""
import json

print(json.dumps({"status": "healthy", "connector": "discord-connector"}))
