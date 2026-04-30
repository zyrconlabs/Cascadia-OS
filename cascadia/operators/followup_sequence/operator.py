#!/usr/bin/env python3
"""
Follow-Up Sequence Operator — Cascadia OS (C3)
NATS: cascadia.operators.followup-sequence.call / .response
Approval-gated: send_step
Direct: create_sequence, enroll_contact, get_next_step, list_enrollments,
        pause_enrollment, resume_enrollment, unenroll_contact
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

NAME = "followup-sequence"
VERSION = "1.0.0"
PORT = 8103
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"
SUBJECT_APPROVALS = "cascadia.approvals.request"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [followup-sequence] %(message)s",
)
log = logging.getLogger(NAME)

# In-memory stores
_sequences: Dict[str, Dict[str, Any]] = {}   # sequence_id → {name, steps, created_at}
_enrollments: Dict[str, Dict[str, Any]] = {}  # enrollment_id → {contact, sequence_id, step, created_at, status}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Core logic ────────────────────────────────────────────────────────────────

def create_sequence(name: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    steps: [{"subject": str, "body": str, "delay_days": int}]
    """
    seq_id = f"SEQ-{_uid().upper()}"
    seq = {
        "sequence_id": seq_id,
        "name": name,
        "steps": [
            {
                "step_index": i,
                "subject": s.get("subject", ""),
                "body": s.get("body", ""),
                "delay_days": int(s.get("delay_days", 0)),
            }
            for i, s in enumerate(steps)
        ],
        "created_at": _now(),
    }
    _sequences[seq_id] = seq
    log.info("Created sequence %s name=%s steps=%d", seq_id, name, len(steps))
    return seq


def enroll_contact(
    contact_email: str,
    contact_name: str,
    sequence_id: str,
) -> Dict[str, Any]:
    if sequence_id not in _sequences:
        return {"ok": False, "error": f"sequence not found: {sequence_id}"}
    enrollment_id = f"ENR-{_uid().upper()}"
    enrollment = {
        "enrollment_id": enrollment_id,
        "contact_email": contact_email,
        "contact_name": contact_name,
        "sequence_id": sequence_id,
        "current_step": 0,
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _enrollments[enrollment_id] = enrollment
    log.info("Enrolled %s in sequence %s enrollment=%s", contact_email, sequence_id, enrollment_id)
    return {"ok": True, "enrollment": enrollment}


def get_next_step(enrollment_id: str) -> Dict[str, Any]:
    enr = _enrollments.get(enrollment_id)
    if not enr:
        return {"ok": False, "error": f"enrollment not found: {enrollment_id}"}
    seq = _sequences.get(enr["sequence_id"])
    if not seq:
        return {"ok": False, "error": "sequence not found for enrollment"}
    steps = seq.get("steps", [])
    current = enr.get("current_step", 0)
    if current >= len(steps):
        return {"ok": True, "enrollment_id": enrollment_id, "status": "completed", "step": None}
    return {
        "ok": True,
        "enrollment_id": enrollment_id,
        "step": steps[current],
        "contact_email": enr["contact_email"],
        "contact_name": enr["contact_name"],
        "status": enr["status"],
    }


def list_enrollments(
    sequence_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    results = list(_enrollments.values())
    if sequence_id:
        results = [e for e in results if e.get("sequence_id") == sequence_id]
    if status:
        results = [e for e in results if e.get("status") == status]
    results.sort(key=lambda e: e.get("created_at", ""))
    return results


def pause_enrollment(enrollment_id: str) -> Dict[str, Any]:
    enr = _enrollments.get(enrollment_id)
    if not enr:
        return {"ok": False, "error": f"enrollment not found: {enrollment_id}"}
    enr["status"] = "paused"
    enr["updated_at"] = _now()
    return {"ok": True, "enrollment": enr}


def resume_enrollment(enrollment_id: str) -> Dict[str, Any]:
    enr = _enrollments.get(enrollment_id)
    if not enr:
        return {"ok": False, "error": f"enrollment not found: {enrollment_id}"}
    enr["status"] = "active"
    enr["updated_at"] = _now()
    return {"ok": True, "enrollment": enr}


def unenroll_contact(enrollment_id: str) -> Dict[str, Any]:
    enr = _enrollments.get(enrollment_id)
    if not enr:
        return {"ok": False, "error": f"enrollment not found: {enrollment_id}"}
    enr["status"] = "unenrolled"
    enr["updated_at"] = _now()
    return {"ok": True, "enrollment": enr}


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "create_sequence":
        return {
            "ok": True,
            "action": action,
            **create_sequence(
                name=payload.get("name", ""),
                steps=payload.get("steps", []),
            ),
        }

    if action == "enroll_contact":
        return {
            "action": action,
            **enroll_contact(
                contact_email=payload.get("contact_email", ""),
                contact_name=payload.get("contact_name", ""),
                sequence_id=payload.get("sequence_id", ""),
            ),
        }

    if action == "get_next_step":
        return {"action": action, **get_next_step(payload.get("enrollment_id", ""))}

    if action == "list_enrollments":
        results = list_enrollments(
            sequence_id=payload.get("sequence_id"),
            status=payload.get("status"),
        )
        return {"ok": True, "action": action, "enrollments": results, "count": len(results)}

    if action == "pause_enrollment":
        return {"action": action, **pause_enrollment(payload.get("enrollment_id", ""))}

    if action == "resume_enrollment":
        return {"action": action, **resume_enrollment(payload.get("enrollment_id", ""))}

    if action == "unenroll_contact":
        return {"action": action, **unenroll_contact(payload.get("enrollment_id", ""))}

    if action == "send_step":
        return {
            "ok": True,
            "action": action,
            "status": "approval_required",
            "message": "send_step requires approval — publish to cascadia.approvals.request",
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

    if action == "send_step":
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
                "sequences": len(_sequences),
                "enrollments": len(_enrollments),
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
