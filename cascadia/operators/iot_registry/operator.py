#!/usr/bin/env python3
"""
IoT Device Registry Operator — Cascadia OS
NATS: cascadia.operators.iot-registry.call / .response
Approval-gated: none — registry operations are direct
Direct: register_device, get_device, list_devices, update_status, deregister_device
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

NAME = "iot-registry"
VERSION = "1.0.0"
PORT = 8301
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [iot-registry] %(message)s",
)
log = logging.getLogger(NAME)

# In-memory device registry: device_id → device dict
_devices: Dict[str, Dict[str, Any]] = {}
_devices_lock = threading.Lock()

VALID_STATUSES = {"online", "offline", "unknown"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Registry operations ───────────────────────────────────────────────────────

def register_device(
    device_id: str,
    name: str,
    device_type: str,
    location: str,
    sensor_types: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not device_id:
        return {"ok": False, "error": "device_id is required"}

    now = _now()
    existing = _devices.get(device_id)
    device = {
        "device_id": device_id,
        "name": name or device_id,
        "type": device_type or "generic",
        "location": location or "",
        "sensor_types": sensor_types or [],
        "status": existing.get("status", "unknown") if existing else "unknown",
        "last_seen": existing.get("last_seen") if existing else None,
        "metadata": metadata or {},
        "registered_at": existing.get("registered_at", now) if existing else now,
        "updated_at": now,
    }
    with _devices_lock:
        _devices[device_id] = device
    action = "updated" if existing else "registered"
    log.info("%s device %s type=%s location=%s", action, device_id, device_type, location)
    return {"ok": True, "action": action, "device": device}


def get_device(device_id: str) -> Dict[str, Any]:
    with _devices_lock:
        device = _devices.get(device_id)
    if device is None:
        return {"ok": False, "error": f"device not found: {device_id}"}
    return {"ok": True, "device": device}


def list_devices(
    device_type: Optional[str] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict[str, Any]:
    with _devices_lock:
        results = list(_devices.values())
    if device_type:
        results = [d for d in results if d.get("type") == device_type]
    if status:
        results = [d for d in results if d.get("status") == status]
    if location:
        results = [d for d in results if location.lower() in d.get("location", "").lower()]
    results.sort(key=lambda d: d.get("device_id", ""))
    return {"ok": True, "devices": results, "count": len(results)}


def update_status(device_id: str, status: str) -> Dict[str, Any]:
    if status not in VALID_STATUSES:
        return {"ok": False, "error": f"invalid status '{status}'; must be one of {sorted(VALID_STATUSES)}"}
    with _devices_lock:
        device = _devices.get(device_id)
        if device is None:
            return {"ok": False, "error": f"device not found: {device_id}"}
        device["status"] = status
        device["last_seen"] = _now()
        device["updated_at"] = _now()
    log.info("Updated status device=%s status=%s", device_id, status)
    return {"ok": True, "device": device}


def deregister_device(device_id: str) -> Dict[str, Any]:
    with _devices_lock:
        device = _devices.pop(device_id, None)
    if device is None:
        return {"ok": False, "error": f"device not found: {device_id}"}
    log.info("Deregistered device %s", device_id)
    return {"ok": True, "deregistered": device_id}


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "register_device":
        return register_device(
            device_id=payload.get("device_id", ""),
            name=payload.get("name", ""),
            device_type=payload.get("type", "generic"),
            location=payload.get("location", ""),
            sensor_types=payload.get("sensor_types", []),
            metadata=payload.get("metadata", {}),
        )

    if action == "get_device":
        return get_device(device_id=payload.get("device_id", ""))

    if action == "list_devices":
        return list_devices(
            device_type=payload.get("type"),
            status=payload.get("status"),
            location=payload.get("location"),
        )

    if action == "update_status":
        return update_status(
            device_id=payload.get("device_id", ""),
            status=payload.get("status", "unknown"),
        )

    if action == "deregister_device":
        return deregister_device(device_id=payload.get("device_id", ""))

    return {"ok": False, "error": f"unknown action: {action}"}


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
    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed — NATS loop disabled")
        return

    log.info("Connecting to NATS at %s", NATS_URL)
    nc = await nats.connect(NATS_URL)
    log.info("NATS connected, subscribing to %s", SUBJECT_CALL)

    async def _cb(msg: Any) -> None:
        await handle_event(nc, msg.subject, msg.data)

    await nc.subscribe(SUBJECT_CALL, cb=_cb)
    log.info("Subscribed to %s", SUBJECT_CALL)

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await nc.drain()


# ── Health HTTP ───────────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/health":
            with _devices_lock:
                count = len(_devices)
            self._json(200, {
                "status": "ok",
                "operator": NAME,
                "version": VERSION,
                "port": PORT,
                "devices_registered": count,
            })
        else:
            self._json(404, {"error": "not_found"})


def _start_health_server() -> None:
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health endpoint listening on port %d", PORT)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _start_health_server()
    asyncio.run(_nats_loop())


if __name__ == "__main__":
    main()
