"""
cascadia/operators/generic_alert/health.py
Health check for the Generic Alert operator.
"""


def check() -> dict:
    return {"status": "healthy", "component": "generic_alert", "port": 8910}
