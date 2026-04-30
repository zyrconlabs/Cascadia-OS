"""
IoT Device Registry Operator — Cascadia OS
Owns: port 8301, HTTP lifecycle, DEPOT registration.
Imports device_registry library for all device CRUD logic.
Maturity: IoT sensor primitives in development (beta).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import HTTPServer
from pathlib import Path

from cascadia.iot.device_registry import _Handler as _BaseHandler

OPERATOR_PORT = int(os.environ.get("IOT_REGISTRY_PORT", 8301))

log = logging.getLogger("iot_registry.operator")


class _RegistryHandler(_BaseHandler):
    """Extends device registry handler with port-aware health."""

    def do_GET(self) -> None:
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path == "/iot/health":
            self._send(200, {
                "status": "healthy",
                "component": "iot_registry",
                "port": OPERATOR_PORT,
                "maturity": "beta",
                "note": "IoT sensor primitives in development",
            })
            return
        super().do_GET()


def _register_with_crew() -> None:
    import urllib.request
    manifest_path = Path(__file__).parent / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception:
        return
    try:
        payload = json.dumps({"operator_id": manifest["operator_id"], "manifest": manifest}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:5100/api/crew/register",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def start() -> None:
    threading.Thread(target=_register_with_crew, daemon=True).start()
    server = HTTPServer(("0.0.0.0", OPERATOR_PORT), _RegistryHandler)
    log.info("iot_registry operator listening on port %d (Sensors beta)", OPERATOR_PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [iot_registry] %(message)s",
    )
    start()
