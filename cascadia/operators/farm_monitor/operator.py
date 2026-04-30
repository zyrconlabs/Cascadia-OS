#!/usr/bin/env python3
"""
Farm Monitor Operator — Cascadia OS
NATS: cascadia.operators.farm-monitor.call / .response
      cascadia.iot.readings   (subscribe — live sensor data)
      cascadia.iot.alerts     (publish — threshold breaches)
      cascadia.approvals.request (publish — approval-gated notifications)
Approval-gated: send_alert_notification
Direct: configure_zone, get_zone_status, list_zones, check_thresholds
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

NAME = "farm-monitor"
VERSION = "1.0.0"
PORT = 8302
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"
SUBJECT_READINGS = "cascadia.iot.readings"
SUBJECT_ALERTS = "cascadia.iot.alerts"
SUBJECT_APPROVALS = "cascadia.approvals.request"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [farm-monitor] %(message)s",
)
log = logging.getLogger(NAME)

# Zone registry: zone_id → zone config + readings
_zones: Dict[str, Dict[str, Any]] = {}
_zones_lock = threading.Lock()

# Default thresholds (overridable per zone)
DEFAULT_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "soil_moisture": {
        "warn_below": 30.0,
        "alert_below": 20.0,
    },
    "temperature": {
        "warn_above": 35.0,
        "alert_above": 40.0,
    },
    "humidity": {
        "warn_below": 40.0,
        "alert_below": 30.0,
    },
    "ph": {
        "warn_below": 5.5,
        "warn_above": 7.5,
        "alert_below": 5.0,
        "alert_above": 8.0,
    },
}

# NATS connection and event loop (set in _nats_loop)
_nc: Any = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Zone management ───────────────────────────────────────────────────────────

def configure_zone(
    zone_id: str,
    name: str = "",
    devices: Optional[List[str]] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not zone_id:
        return {"ok": False, "error": "zone_id is required"}

    merged_thresholds = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}
    if thresholds:
        for sensor, overrides in thresholds.items():
            if sensor in merged_thresholds:
                merged_thresholds[sensor].update(overrides)
            else:
                merged_thresholds[sensor] = overrides

    with _zones_lock:
        existing = _zones.get(zone_id, {})
        zone = {
            "zone_id": zone_id,
            "name": name or zone_id,
            "devices": devices if devices is not None else existing.get("devices", []),
            "thresholds": merged_thresholds,
            "readings": existing.get("readings", deque(maxlen=200)),
            "active_alerts": existing.get("active_alerts", []),
            "configured_at": existing.get("configured_at", _now()),
            "updated_at": _now(),
        }
        _zones[zone_id] = zone

    log.info("Configured zone %s devices=%s", zone_id, zone["devices"])
    return {
        "ok": True,
        "zone_id": zone_id,
        "name": zone["name"],
        "devices": zone["devices"],
        "thresholds": zone["thresholds"],
    }


def get_zone_status(zone_id: str) -> Dict[str, Any]:
    with _zones_lock:
        zone = _zones.get(zone_id)
    if zone is None:
        return {"ok": False, "error": f"zone not found: {zone_id}"}

    readings_snapshot = list(zone["readings"])[-20:]
    return {
        "ok": True,
        "zone_id": zone_id,
        "name": zone["name"],
        "devices": zone["devices"],
        "last_readings": readings_snapshot,
        "active_alerts": zone["active_alerts"],
        "reading_count": len(zone["readings"]),
    }


def list_zones() -> Dict[str, Any]:
    with _zones_lock:
        zones_copy = list(_zones.values())

    summary = []
    for zone in zones_copy:
        readings = list(zone["readings"])
        summary.append({
            "zone_id": zone["zone_id"],
            "name": zone["name"],
            "devices": zone["devices"],
            "reading_count": len(readings),
            "active_alerts": len(zone["active_alerts"]),
            "last_reading_at": readings[-1]["ingested_at"] if readings else None,
        })
    summary.sort(key=lambda z: z["zone_id"])
    return {"ok": True, "zones": summary, "count": len(summary)}


# ── Threshold checking ────────────────────────────────────────────────────────

def _eval_threshold(sensor_type: str, value: float, thresholds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an alert dict if threshold is breached, else None."""
    cfg = thresholds.get(sensor_type)
    if cfg is None:
        return None

    level = None
    reason = ""

    if sensor_type == "soil_moisture":
        if value < cfg.get("alert_below", 20):
            level = "alert"
            reason = f"soil_moisture {value}% < alert threshold {cfg.get('alert_below', 20)}%"
        elif value < cfg.get("warn_below", 30):
            level = "warn"
            reason = f"soil_moisture {value}% < warn threshold {cfg.get('warn_below', 30)}%"

    elif sensor_type == "temperature":
        if value > cfg.get("alert_above", 40):
            level = "alert"
            reason = f"temperature {value}°C > alert threshold {cfg.get('alert_above', 40)}°C"
        elif value > cfg.get("warn_above", 35):
            level = "warn"
            reason = f"temperature {value}°C > warn threshold {cfg.get('warn_above', 35)}°C"

    elif sensor_type == "humidity":
        if value < cfg.get("alert_below", 30):
            level = "alert"
            reason = f"humidity {value}% < alert threshold {cfg.get('alert_below', 30)}%"
        elif value < cfg.get("warn_below", 40):
            level = "warn"
            reason = f"humidity {value}% < warn threshold {cfg.get('warn_below', 40)}%"

    elif sensor_type == "ph":
        if value < cfg.get("alert_below", 5.0) or value > cfg.get("alert_above", 8.0):
            level = "alert"
            reason = f"pH {value} outside alert range [{cfg.get('alert_below', 5.0)}, {cfg.get('alert_above', 8.0)}]"
        elif value < cfg.get("warn_below", 5.5) or value > cfg.get("warn_above", 7.5):
            level = "warn"
            reason = f"pH {value} outside warn range [{cfg.get('warn_below', 5.5)}, {cfg.get('warn_above', 7.5)}]"

    if level is None:
        return None

    return {"level": level, "sensor_type": sensor_type, "value": value, "reason": reason}


def check_thresholds(zone_id: str, reading: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Check reading against zone thresholds; publish alert if breached. Returns alert or None."""
    with _zones_lock:
        zone = _zones.get(zone_id)
    if zone is None:
        return None

    sensor_type = reading.get("sensor_type", "")
    try:
        value = float(reading.get("value", 0))
    except (TypeError, ValueError):
        return None

    breach = _eval_threshold(sensor_type, value, zone["thresholds"])
    if breach is None:
        return None

    alert = {
        "alert_id": f"ALT-{_uid().upper()}",
        "zone_id": zone_id,
        "zone_name": zone["name"],
        "device_id": reading.get("device_id", ""),
        "sensor_type": sensor_type,
        "value": value,
        "unit": reading.get("unit", ""),
        "level": breach["level"],
        "reason": breach["reason"],
        "reading_timestamp": reading.get("timestamp", _now()),
        "alerted_at": _now(),
    }

    # Store in active_alerts (keep last 50)
    with _zones_lock:
        zone["active_alerts"].append(alert)
        if len(zone["active_alerts"]) > 50:
            zone["active_alerts"] = zone["active_alerts"][-50:]

    # Publish to NATS
    if _nc is not None and _loop is not None:
        payload = json.dumps(alert).encode()
        fut = asyncio.run_coroutine_threadsafe(
            _nc.publish(SUBJECT_ALERTS, payload), _loop
        )
        try:
            fut.result(timeout=2)
        except Exception as exc:
            log.warning("NATS alert publish failed: %s", exc)

    log.warning("ALERT zone=%s device=%s sensor=%s level=%s reason=%s",
                zone_id, alert["device_id"], sensor_type, breach["level"], breach["reason"])
    return alert


def _route_reading_to_zones(reading: Dict[str, Any]) -> None:
    """Find zones that own this device_id and run threshold checks."""
    device_id = reading.get("device_id", "")
    with _zones_lock:
        zone_ids = [
            zid for zid, z in _zones.items()
            if not z["devices"] or device_id in z["devices"]
        ]
    for zone_id in zone_ids:
        with _zones_lock:
            zone = _zones.get(zone_id)
            if zone is not None:
                zone["readings"].append(reading)
        check_thresholds(zone_id, reading)


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "configure_zone":
        return configure_zone(
            zone_id=payload.get("zone_id", ""),
            name=payload.get("name", ""),
            devices=payload.get("devices"),
            thresholds=payload.get("thresholds"),
        )

    if action == "get_zone_status":
        return get_zone_status(zone_id=payload.get("zone_id", ""))

    if action == "list_zones":
        return list_zones()

    if action == "check_thresholds":
        zone_id = payload.get("zone_id", "")
        reading = payload.get("reading", {})
        alert = check_thresholds(zone_id, reading)
        if alert:
            return {"ok": True, "breached": True, "alert": alert}
        return {"ok": True, "breached": False}

    if action == "send_alert_notification":
        return {
            "ok": True,
            "action": action,
            "status": "approval_required",
            "message": "send_alert_notification requires approval — publish to cascadia.approvals.request",
        }

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

    if action == "send_alert_notification":
        request_id = _uid()
        approval_msg = {
            "request_id": request_id,
            "operator": NAME,
            "action": action,
            "payload": payload,
            "requested_at": _now(),
        }
        await nc.publish(SUBJECT_APPROVALS, json.dumps(approval_msg).encode())
        response = {
            "ok": True,
            "status": "pending_approval",
            "request_id": request_id,
            "action": action,
        }
    else:
        response = execute_task(payload)

    reply = payload.get("_reply") or SUBJECT_RESPONSE
    await nc.publish(reply, json.dumps(response).encode())


async def _readings_handler(msg: Any) -> None:
    """Process live readings from cascadia.iot.readings."""
    try:
        reading = json.loads(msg.data)
    except Exception as exc:
        log.warning("Bad JSON on readings subject: %s", exc)
        return
    _route_reading_to_zones(reading)


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
    log.info("NATS connected")

    async def _call_cb(msg: Any) -> None:
        await handle_event(_nc, msg.subject, msg.data)

    await _nc.subscribe(SUBJECT_CALL, cb=_call_cb)
    log.info("Subscribed to %s", SUBJECT_CALL)

    await _nc.subscribe(SUBJECT_READINGS, cb=_readings_handler)
    log.info("Subscribed to %s", SUBJECT_READINGS)

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await _nc.drain()


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
            with _zones_lock:
                zone_count = len(_zones)
            self._json(200, {
                "status": "ok",
                "operator": NAME,
                "version": VERSION,
                "port": PORT,
                "zones_configured": zone_count,
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
