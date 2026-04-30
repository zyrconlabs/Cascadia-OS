#!/usr/bin/env python3
"""
IoT Sensor Ingest Operator — Cascadia OS
NATS: cascadia.operators.iot-ingest.call / .response
      cascadia.iot.readings  (publish inbound readings)
Approval-gated: none — ingest only
Direct: get_readings, clear_readings
HTTP: POST /ingest, POST /ingest/batch, GET /readings, GET /health
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

NAME = "iot-ingest"
VERSION = "1.0.0"
PORT = 8300
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"
SUBJECT_READINGS = "cascadia.iot.readings"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [iot-ingest] %(message)s",
)
log = logging.getLogger(NAME)

# In-memory reading store
_readings: deque = deque(maxlen=1000)
_readings_lock = threading.Lock()

# NATS connection and event-loop references (set in _nats_loop)
_nc: Any = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Reading validation & ingestion ───────────────────────────────────────────

REQUIRED_FIELDS = {"device_id", "sensor_type", "value"}


def _validate_reading(raw: Dict[str, Any]) -> tuple[bool, str]:
    for f in REQUIRED_FIELDS:
        if f not in raw:
            return False, f"missing required field: {f}"
    return True, ""


def _normalise_reading(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "device_id": str(raw["device_id"]),
        "sensor_type": str(raw["sensor_type"]),
        "value": raw["value"],
        "unit": str(raw.get("unit", "")),
        "timestamp": raw.get("timestamp") or _now(),
        "ingested_at": _now(),
    }


def _store_and_publish(reading: Dict[str, Any]) -> None:
    """Store reading and publish to NATS (thread-safe)."""
    with _readings_lock:
        _readings.append(reading)

    if _nc is not None and _loop is not None:
        payload = json.dumps(reading).encode()
        future = asyncio.run_coroutine_threadsafe(
            _nc.publish(SUBJECT_READINGS, payload), _loop
        )
        try:
            future.result(timeout=2)
        except Exception as exc:
            log.warning("NATS publish failed: %s", exc)


def ingest_reading(raw: Dict[str, Any]) -> Dict[str, Any]:
    ok, err = _validate_reading(raw)
    if not ok:
        return {"ok": False, "error": err}
    reading = _normalise_reading(raw)
    _store_and_publish(reading)
    log.info("Ingested reading device=%s sensor=%s value=%s",
             reading["device_id"], reading["sensor_type"], reading["value"])
    return {"ok": True, "reading": reading}


def ingest_batch(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    results = []
    ok_count = 0
    for item in items:
        result = ingest_reading(item)
        results.append(result)
        if result.get("ok"):
            ok_count += 1
    return {"ok": True, "accepted": ok_count, "total": len(items), "results": results}


def get_readings(limit: int = 50) -> List[Dict[str, Any]]:
    with _readings_lock:
        all_readings = list(_readings)
    return all_readings[-limit:]


def clear_readings() -> Dict[str, Any]:
    with _readings_lock:
        count = len(_readings)
        _readings.clear()
    log.info("Cleared %d readings from memory", count)
    return {"ok": True, "cleared": count}


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "get_readings":
        limit = int(payload.get("limit", 50))
        readings = get_readings(limit)
        return {"ok": True, "action": action, "readings": readings, "count": len(readings)}

    if action == "clear_readings":
        return clear_readings()

    return {"ok": False, "error": f"unknown action: {action}"}


# ── HTTP server ───────────────────────────────────────────────────────────────

class IngestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> Optional[Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/ingest":
            body = self._read_body()
            if body is None:
                self._json(400, {"ok": False, "error": "invalid or missing JSON body"})
                return
            if not isinstance(body, dict):
                self._json(400, {"ok": False, "error": "body must be a JSON object"})
                return
            result = ingest_reading(body)
            self._json(200 if result["ok"] else 400, result)

        elif path == "/ingest/batch":
            body = self._read_body()
            if body is None or not isinstance(body, list):
                self._json(400, {"ok": False, "error": "body must be a JSON array"})
                return
            result = ingest_batch(body)
            self._json(200, result)

        else:
            self._json(404, {"error": "not_found"})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        if path == "/health":
            with _readings_lock:
                count = len(_readings)
            self._json(200, {
                "status": "ok",
                "operator": NAME,
                "version": VERSION,
                "port": PORT,
                "readings_count": count,
            })

        elif path == "/readings":
            try:
                limit = int(qs.get("limit", ["50"])[0])
            except (ValueError, IndexError):
                limit = 50
            limit = max(1, min(limit, 1000))
            readings = get_readings(limit)
            self._json(200, {"ok": True, "readings": readings, "count": len(readings)})

        else:
            self._json(404, {"error": "not_found"})


def _start_http_server() -> None:
    server = HTTPServer(("0.0.0.0", PORT), IngestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("HTTP server listening on port %d", PORT)


# ── NATS handler ──────────────────────────────────────────────────────────────

async def handle_event(nc: Any, subject: str, raw: bytes) -> None:
    try:
        payload = json.loads(raw)
    except Exception as exc:
        log.warning("Bad JSON on %s: %s", subject, exc)
        return

    action = payload.get("action", "")
    log.info("NATS %s action=%s", subject, action)

    response = execute_task(payload)
    reply = payload.get("_reply") or SUBJECT_RESPONSE
    await nc.publish(reply, json.dumps(response).encode())


async def _nats_loop() -> None:
    global _nc, _loop
    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed — NATS loop disabled")
        return

    _loop = asyncio.get_running_loop()
    log.info("Connecting to NATS at %s", NATS_URL)
    _nc = await nats.connect(NATS_URL)
    log.info("NATS connected, subscribing to %s", SUBJECT_CALL)

    async def _cb(msg: Any) -> None:
        await handle_event(_nc, msg.subject, msg.data)

    await _nc.subscribe(SUBJECT_CALL, cb=_cb)
    log.info("Subscribed to %s", SUBJECT_CALL)

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await _nc.drain()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _start_http_server()
    asyncio.run(_nats_loop())


if __name__ == "__main__":
    main()
