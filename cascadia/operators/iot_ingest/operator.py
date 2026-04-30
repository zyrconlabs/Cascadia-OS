"""
IoT Sensor Ingest Operator — Cascadia OS
Owns: port 8300, HTTP lifecycle, DEPOT registration, readings feed endpoint.
Imports sensor_ingest library for validation, normalization, NATS publish.
Maturity: IoT sensor primitives in development (beta).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs

from cascadia.iot.sensor_ingest import (
    _Handler as _BaseHandler,
    validate_reading,
    normalize_timestamp,
    build_subject,
    publish_reading,
    VALID_SENSOR_TYPES,
)
from cascadia.iot.sensor_store import SensorStore

OPERATOR_PORT = int(os.environ.get("IOT_INGEST_PORT", 8300))
_STORE_PATH = os.environ.get("IOT_STORE_PATH", "data/iot/readings.db")

log = logging.getLogger("iot_ingest.operator")

_store: Optional[SensorStore] = None
_store_lock = threading.Lock()


def _get_store() -> SensorStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SensorStore(_STORE_PATH)
    return _store


class _IngestHandler(_BaseHandler):
    """Extends the base sensor ingest handler with readings feed and port-aware health."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/iot/health":
            self._send(200, {
                "status": "healthy",
                "component": "iot_ingest",
                "port": OPERATOR_PORT,
                "maturity": "beta",
                "note": "IoT sensor primitives in development",
            })
            return

        if path == "/iot/sensor/types":
            self._send(200, {"sensor_types": sorted(VALID_SENSOR_TYPES)})
            return

        if path == "/iot/readings":
            qs = parse_qs(parsed.query)
            try:
                limit = min(int(qs.get("limit", ["20"])[0]), 100)
            except (ValueError, IndexError):
                limit = 20
            device_id = qs.get("device_id", [None])[0]
            store = _get_store()
            if device_id:
                readings = store.query(device_id, hours=24)[:limit]
            else:
                # All devices, last N readings across the store
                devices = store.list_devices()
                all_readings: list = []
                for did in devices:
                    all_readings.extend(store.query(did, hours=24))
                all_readings.sort(key=lambda r: r.get("recorded_at", ""), reverse=True)
                readings = all_readings[:limit]
            self._send(200, {"readings": readings, "count": len(readings)})
            return

        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/iot/sensor/reading":
            self._send(404, {"error": "not found"})
            return

        body = self._read_json()
        if body is None:
            self._send(400, {"error": "invalid or missing JSON body"})
            return

        err = validate_reading(body)
        if err:
            self._send(400, {"error": err})
            return

        body["timestamp"] = normalize_timestamp(body.get("timestamp"))
        subject = build_subject(body["sensor_type"], body["device_id"])

        try:
            val = float(body["value"])
            _get_store().record(body["device_id"], subject, body, value=val)
        except Exception:
            pass

        publish_reading(subject, body)
        log.info("accepted reading device=%s type=%s", body["device_id"], body["sensor_type"])
        self._send(200, {"accepted": True, "subject": subject})


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
    server = HTTPServer(("0.0.0.0", OPERATOR_PORT), _IngestHandler)
    log.info("iot_ingest operator listening on port %d (Sensors beta)", OPERATOR_PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [iot_ingest] %(message)s",
    )
    start()
