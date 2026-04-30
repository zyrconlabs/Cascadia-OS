"""
test_settings_op/operator.py — Settings engine test operator.
For testing only. Does not perform real work. Not in DEPOT catalog.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = int(os.environ.get("TEST_SETTINGS_OP_PORT", 8999))
SETTINGS_DB = os.environ.get("SETTINGS_DB_PATH", "data/settings.db")
VAULT_DB    = os.environ.get("VAULT_DB_PATH", "data/runtime/cascadia_vault.db")
MANIFEST_PATH = Path(__file__).parent / "manifest.json"


def _get_engine():
    from cascadia.settings.engine import SettingsEngine
    return SettingsEngine(settings_db=SETTINGS_DB, vault_db=VAULT_DB)


def _load_manifest():
    from cascadia.shared.manifest_schema import load_manifest
    return load_manifest(MANIFEST_PATH)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default access log

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/health":
            self._send(200, {
                "status": "healthy",
                "component": "test-settings-op",
                "for_testing_only": True,
            })
        elif path == "/api/settings/echo":
            try:
                m = _load_manifest()
                settings = _get_engine().get_settings("operator", "test-settings-op", m)
                self._send(200, {"settings": settings, "manifest_id": m.id})
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if path == "/api/run":
            try:
                m = _load_manifest()
                settings = _get_engine().get_settings("operator", "test-settings-op", m)
                self._send(200, {
                    "ran": True,
                    "received": body,
                    "active_settings": settings,
                    "for_testing_only": True,
                })
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"Test Settings Operator listening on port {PORT} (testing only)")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
