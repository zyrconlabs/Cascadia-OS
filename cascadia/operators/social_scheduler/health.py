"""Shared health endpoint factory for Cascadia OS operators."""

import json
from http.server import BaseHTTPRequestHandler


def make_health_handler(operator_name: str, version: str, port: int):
    """Return a BaseHTTPRequestHandler class for GET /health."""

    class HealthHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # suppress default access log

        def do_GET(self):
            if self.path == "/health":
                body = json.dumps({
                    "status": "ok",
                    "operator": operator_name,
                    "version": version,
                    "port": port,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    return HealthHandler
