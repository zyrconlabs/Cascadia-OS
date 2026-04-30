#!/usr/bin/env python3
"""
Intake Form Processor Operator — Cascadia OS (C5)
NATS: cascadia.operators.intake-form.call / .response
Approval-gated: route_submission
Direct: define_form, list_forms, submit_form, list_submissions, get_submission
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

NAME = "intake-form"
VERSION = "1.0.0"
PORT = 8105
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"
SUBJECT_APPROVALS = "cascadia.approvals.request"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [intake-form] %(message)s",
)
log = logging.getLogger(NAME)

# In-memory stores
_forms: Dict[str, Dict[str, Any]] = {}          # form_id → form definition
_submissions: Dict[str, Dict[str, Any]] = {}    # submission_id → submission

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_PHONE_RE = re.compile(r'^[\d\s\-\+\(\)\.]{7,20}$')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Core logic ────────────────────────────────────────────────────────────────

def define_form(
    form_id: str,
    name: str,
    fields: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    fields: [{"name": str, "required": bool, "type": "text"|"email"|"phone"|"number"}]
    """
    form = {
        "form_id": form_id,
        "name": name,
        "fields": [
            {
                "name": f.get("name", ""),
                "required": bool(f.get("required", False)),
                "type": f.get("type", "text"),
            }
            for f in fields
        ],
        "created_at": _now(),
    }
    _forms[form_id] = form
    log.info("Defined form %s name=%s fields=%d", form_id, name, len(fields))
    return form


def list_forms() -> List[Dict[str, Any]]:
    return sorted(_forms.values(), key=lambda f: f.get("created_at", ""))


def _validate_field(field_def: Dict[str, Any], value: Any) -> Optional[str]:
    """Return an error string if invalid, else None."""
    fname = field_def["name"]
    ftype = field_def.get("type", "text")
    required = field_def.get("required", False)

    if value is None or (isinstance(value, str) and value.strip() == ""):
        if required:
            return f"Field '{fname}' is required"
        return None  # optional and empty — ok

    val_str = str(value).strip()

    if ftype == "email":
        if not _EMAIL_RE.match(val_str):
            return f"Field '{fname}' must be a valid email address"
    elif ftype == "phone":
        if not _PHONE_RE.match(val_str):
            return f"Field '{fname}' must be a valid phone number"
    elif ftype == "number":
        try:
            float(val_str)
        except ValueError:
            return f"Field '{fname}' must be a number"

    return None


def submit_form(form_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    form = _forms.get(form_id)
    if not form:
        return {"ok": False, "errors": [f"Form not found: {form_id}"], "submission_id": None}

    errors = []
    for field in form.get("fields", []):
        fname = field["name"]
        value = data.get(fname)
        err = _validate_field(field, value)
        if err:
            errors.append(err)

    if errors:
        return {"ok": False, "errors": errors, "submission_id": None}

    submission_id = f"SUB-{_uid().upper()}"
    submission = {
        "submission_id": submission_id,
        "form_id": form_id,
        "form_name": form.get("name", ""),
        "data": data,
        "status": "received",
        "submitted_at": _now(),
    }
    _submissions[submission_id] = submission
    log.info("Received submission %s form=%s", submission_id, form_id)
    return {"ok": True, "submission_id": submission_id, "errors": []}


def list_submissions(form_id: Optional[str] = None) -> List[Dict[str, Any]]:
    results = list(_submissions.values())
    if form_id:
        results = [s for s in results if s.get("form_id") == form_id]
    results.sort(key=lambda s: s.get("submitted_at", ""))
    return results


def get_submission(submission_id: str) -> Dict[str, Any]:
    sub = _submissions.get(submission_id)
    if not sub:
        return {"ok": False, "error": f"submission not found: {submission_id}"}
    return {"ok": True, "submission": sub}


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "define_form":
        form = define_form(
            form_id=payload.get("form_id", f"form-{_uid()}"),
            name=payload.get("name", ""),
            fields=payload.get("fields", []),
        )
        return {"ok": True, "action": action, "form": form}

    if action == "list_forms":
        forms = list_forms()
        return {"ok": True, "action": action, "forms": forms, "count": len(forms)}

    if action == "submit_form":
        result = submit_form(
            form_id=payload.get("form_id", ""),
            data=payload.get("data", {}),
        )
        return {"action": action, **result}

    if action == "list_submissions":
        results = list_submissions(form_id=payload.get("form_id"))
        return {"ok": True, "action": action, "submissions": results, "count": len(results)}

    if action == "get_submission":
        return {"action": action, **get_submission(payload.get("submission_id", ""))}

    if action == "route_submission":
        return {
            "ok": True,
            "action": action,
            "status": "approval_required",
            "message": "route_submission requires approval — publish to cascadia.approvals.request",
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

    if action == "route_submission":
        submission_id = payload.get("submission_id", "")
        target_operator = payload.get("target_operator", "")
        target_action = payload.get("target_action", "")

        sub = _submissions.get(submission_id)
        if not sub:
            response = {"ok": False, "error": f"submission not found: {submission_id}"}
        else:
            request_id = _uid()
            # Approval gate: publish to cascadia.approvals.request
            approval_msg = {
                "request_id": request_id,
                "operator": NAME,
                "action": "route_submission",
                "submission_id": submission_id,
                "target_operator": target_operator,
                "target_action": target_action,
                "target_subject": f"cascadia.operators.{target_operator}.call",
                "payload": payload,
                "requested_at": _now(),
            }
            await nc.publish(SUBJECT_APPROVALS, json.dumps(approval_msg).encode())
            response = {
                "ok": True,
                "status": "pending_approval",
                "request_id": request_id,
                "action": action,
                "target_operator": target_operator,
                "target_action": target_action,
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
                "forms_defined": len(_forms),
                "submissions_total": len(_submissions),
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
