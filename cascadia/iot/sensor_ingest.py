"""
sensor_ingest.py — Cascadia OS IoT
Receives sensor readings and publishes to NATS.
Owns: HTTP ingest, payload validation, NATS publish.
Does not own: threshold evaluation, alerting, device registry.
Port: 8300
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

NAME = "sensor_ingest"
VERSION = "1.0.0"
PORT = 8300
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

VALID_SENSOR_TYPES = frozenset([
    "temperature", "humidity", "gps", "motion", "door", "pressure", "light",
])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [sensor_ingest] %(message)s",
)
log = logging.getLogger(NAME)

_nc: Any = None
_loop: Any = None
_nats_lock = threading.Lock()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_timestamp(ts: Optional[str]) -> str:
    if not ts:
        return _now_utc()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return _now_utc()


def _validate_reading(body: Dict[str, Any]) -> Optional[str]:
    required = ("device_id", "sensor_type", "value", "unit")
    for field in required:
        if field not in body or body[field] is None or body[field] == "":
            return f"missing required field: {field}"
    if body["sensor_type"] not in VALID_SENSOR_TYPES:
        return (
            f"invalid sensor_type '{body['sensor_type']}'; "
            f"valid types: {sorted(VALID_SENSOR_TYPES)}"
        )
    try:
        float(body["value"])
    except (TypeError, ValueError):
        return "value must be numeric"
    return None


def _nats_subject(sensor_type: str, device_id: str) -> str:
    return f"cascadia.iot.sensor.{sensor_type}.{device_id}"


def _publish_to_nats(subject: str, payload: Dict[str, Any]) -> None:
    def _run() -> None:
        try:
            import asyncio
            import nats  # type: ignore

            async def _pub() -> None:
                nc = await nats.connect(NATS_URL)
                await nc.publish(subject, json.dumps(payload).encode())
                await nc.drain()

            loop = asyncio.new_event_loop()
            loop.run_until_complete(_pub())
            loop.close()
        except Exception as exc:
            log.warning("NATS publish failed (non-fatal): %s", exc)

    threading.Thread(target=_run, daemon=True).start()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)

    def _send(self, status: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/iot/health":
            self._send(200, {"status": "healthy", "component": "sensor_ingest", "port": PORT})
        elif path == "/iot/sensor/types":
            self._send(200, {"sensor_types": sorted(VALID_SENSOR_TYPES)})
        else:
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

        err = _validate_reading(body)
        if err:
            self._send(400, {"error": err})
            return

        body["timestamp"] = _normalize_timestamp(body.get("timestamp"))
        subject = _nats_subject(body["sensor_type"], body["device_id"])

        _publish_to_nats(subject, body)
        log.info("accepted reading device=%s type=%s subject=%s",
                 body["device_id"], body["sensor_type"], subject)
        self._send(200, {"accepted": True, "subject": subject})


def start(port: int = PORT) -> None:
    server = HTTPServer(("0.0.0.0", port), _Handler)
    log.info("sensor_ingest listening on port %d", port)
    server.serve_forever()


if __name__ == "__main__":
    start()
