#!/usr/bin/env python3
"""
Appointment Scheduler Operator — Cascadia OS (C2)
NATS: cascadia.operators.appointment-scheduler.call / .response
Approval-gated: send_confirmation, send_reminder
Direct: create_appointment, list_appointments, cancel_appointment, reschedule_appointment
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

NAME = "appointment-scheduler"
VERSION = "1.0.0"
PORT = 8102
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"
SUBJECT_APPROVALS = "cascadia.approvals.request"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [appointment-scheduler] %(message)s",
)
log = logging.getLogger(NAME)

# In-memory store: appointment_id → appointment dict
_appointments: Dict[str, Dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Core appointment logic ────────────────────────────────────────────────────

def create_appointment(
    client_name: str,
    client_email: str,
    date: str,
    time: str,
    duration_minutes: int,
    notes: str = "",
) -> Dict[str, Any]:
    appointment_id = f"APT-{_uid().upper()}"
    appt = {
        "appointment_id": appointment_id,
        "client_name": client_name,
        "client_email": client_email,
        "date": date,
        "time": time,
        "duration_minutes": int(duration_minutes),
        "notes": notes,
        "status": "scheduled",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _appointments[appointment_id] = appt
    log.info("Created appointment %s for %s on %s %s", appointment_id, client_name, date, time)
    return appt


def list_appointments(
    date: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    results = list(_appointments.values())
    if date:
        results = [a for a in results if a.get("date") == date]
    if status:
        results = [a for a in results if a.get("status") == status]
    results.sort(key=lambda a: (a.get("date", ""), a.get("time", "")))
    return results


def cancel_appointment(appointment_id: str, reason: str = "") -> Dict[str, Any]:
    appt = _appointments.get(appointment_id)
    if not appt:
        return {"ok": False, "error": f"appointment not found: {appointment_id}"}
    appt["status"] = "cancelled"
    appt["cancel_reason"] = reason
    appt["updated_at"] = _now()
    log.info("Cancelled appointment %s reason=%s", appointment_id, reason)
    return {"ok": True, "appointment": appt}


def reschedule_appointment(
    appointment_id: str,
    new_date: str,
    new_time: str,
) -> Dict[str, Any]:
    appt = _appointments.get(appointment_id)
    if not appt:
        return {"ok": False, "error": f"appointment not found: {appointment_id}"}
    appt["date"] = new_date
    appt["time"] = new_time
    appt["status"] = "rescheduled"
    appt["updated_at"] = _now()
    log.info("Rescheduled appointment %s to %s %s", appointment_id, new_date, new_time)
    return {"ok": True, "appointment": appt}


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "create_appointment":
        appt = create_appointment(
            client_name=payload.get("client_name", ""),
            client_email=payload.get("client_email", ""),
            date=payload.get("date", ""),
            time=payload.get("time", ""),
            duration_minutes=int(payload.get("duration_minutes", 60)),
            notes=payload.get("notes", ""),
        )
        return {"ok": True, "action": action, "appointment": appt}

    if action == "list_appointments":
        results = list_appointments(
            date=payload.get("date"),
            status=payload.get("status"),
        )
        return {"ok": True, "action": action, "appointments": results, "count": len(results)}

    if action == "cancel_appointment":
        return cancel_appointment(
            appointment_id=payload.get("appointment_id", ""),
            reason=payload.get("reason", ""),
        )

    if action == "reschedule_appointment":
        return reschedule_appointment(
            appointment_id=payload.get("appointment_id", ""),
            new_date=payload.get("new_date", ""),
            new_time=payload.get("new_time", ""),
        )

    if action in ("send_confirmation", "send_reminder"):
        return {
            "ok": True,
            "action": action,
            "status": "approval_required",
            "message": f"{action} requires approval — publish to cascadia.approvals.request",
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

    if action in ("send_confirmation", "send_reminder"):
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

    def _json(self, code: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/health":
            self._json(200, {
                "status": "ok",
                "operator": NAME,
                "version": VERSION,
                "port": PORT,
                "appointments_in_memory": len(_appointments),
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
