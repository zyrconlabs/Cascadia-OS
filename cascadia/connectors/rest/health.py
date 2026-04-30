"""Health check for CON-108 REST Connector."""
import json
import urllib.request

try:
    with urllib.request.urlopen('http://127.0.0.1:9980/health', timeout=3) as r:
        data = json.loads(r.read())
        print(json.dumps({"status": "healthy", "connector": "rest-connector", "detail": data}))
except Exception as exc:
    print(json.dumps({"status": "error", "reason": str(exc)}))
