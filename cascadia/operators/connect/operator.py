"""
C9: CONNECT Integration Operator — Cascadia OS Operator
Port: 8200  Subject prefix: cascadia.operators.connect
"""

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError

import nats

NAME = "connect"
VERSION = "1.0.0"
PORT = 8200
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

CALL_SUBJECT = f"cascadia.operators.{NAME}.call"
RESPONSE_SUBJECT = f"cascadia.operators.{NAME}.response"
APPROVALS_SUBJECT = "cascadia.approvals.request"
EVENT_SUBJECT = f"cascadia.operators.{NAME}.event"
CRM_SUBJECT = f"cascadia.operators.{NAME}.crm"

VALID_CRM_TYPES = {"salesforce", "hubspot", "generic"}

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

webhooks: dict = {}   # webhook_id → {name, target_subject, registered_at}

# ---------------------------------------------------------------------------
# Shared NATS connection (set during startup)
# ---------------------------------------------------------------------------

_nc = None  # type: nats.aio.client.Client | None
_loop = None  # type: asyncio.AbstractEventLoop | None


# ---------------------------------------------------------------------------
# Webhook HTTP ingest server (runs on a sub-path of PORT)
# ---------------------------------------------------------------------------

class _IngestHandler(BaseHTTPRequestHandler):
    """Handles POST /ingest → publishes parsed body to cascadia.operators.connect.event."""

    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def do_POST(self):
        if self.path != "/ingest":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
        except Exception as exc:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode())
            return

        envelope = {
            "ingest_id": str(uuid.uuid4()),
            "received_at": datetime.utcnow().isoformat(),
            "source_ip": self.client_address[0],
            "payload": body,
        }
        # Publish asynchronously onto the event loop
        if _nc is not None and _loop is not None and not _loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                _nc.publish(EVENT_SUBJECT, json.dumps(envelope).encode()),
                _loop,
            )
        result = {"status": "accepted", "ingest_id": envelope["ingest_id"]}
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok", "operator": NAME}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


# ---------------------------------------------------------------------------
# Core logic — direct actions
# ---------------------------------------------------------------------------

def register_webhook(webhook_id: str, name: str, target_subject: str) -> dict:
    if not webhook_id:
        webhook_id = str(uuid.uuid4())
    webhooks[webhook_id] = {
        "webhook_id": webhook_id,
        "name": name,
        "target_subject": target_subject,
        "registered_at": datetime.utcnow().isoformat(),
    }
    return dict(webhooks[webhook_id])


def list_webhooks() -> dict:
    return {"webhooks": list(webhooks.values()), "count": len(webhooks)}


def _do_ingest(payload: dict) -> dict:
    """Internal action triggered by HTTP ingest — returns envelope info."""
    return {
        "action": "ingest",
        "note": "Ingest is HTTP-triggered; use POST /ingest",
        "event_subject": EVENT_SUBJECT,
    }


# ---------------------------------------------------------------------------
# Approval-gated actions (logic only — actual I/O happens post-approval)
# ---------------------------------------------------------------------------

def _build_outbound_payload(url: str, method: str,
                             headers: dict, body: dict) -> dict:
    return {
        "url": url,
        "method": method.upper(),
        "headers": headers or {},
        "body": body or {},
    }


def _build_crm_payload(crm_type: str, record_type: str, data: dict) -> dict:
    crm_type = crm_type.lower()
    if crm_type not in VALID_CRM_TYPES:
        return {"error": f"Invalid crm_type '{crm_type}'. Valid: {sorted(VALID_CRM_TYPES)}"}
    return {
        "crm_type": crm_type,
        "record_type": record_type,
        "data": data or {},
        "formatted_at": datetime.utcnow().isoformat(),
    }


def _execute_http_outbound(url: str, method: str,
                            headers: dict, body: dict) -> dict:
    """Perform the actual HTTP request (called post-approval)."""
    method = method.upper()
    data = json.dumps(body).encode() if body else None
    req = urllib_request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            try:
                resp_json = json.loads(resp_body)
            except Exception:
                resp_json = resp_body
            return {
                "status_code": resp.status,
                "headers": dict(resp.headers),
                "body": resp_json,
            }
    except HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}", "status_code": exc.code}
    except URLError as exc:
        return {"error": f"URL error: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Approval request helper
# ---------------------------------------------------------------------------

async def _request_approval(nc, action: str, payload: dict) -> dict:
    request_id = str(uuid.uuid4())
    envelope = {
        "request_id": request_id,
        "operator": NAME,
        "action": action,
        "payload": payload,
        "requested_at": datetime.utcnow().isoformat(),
    }
    await nc.publish(APPROVALS_SUBJECT, json.dumps(envelope).encode())
    return {"status": "pending_approval", "request_id": request_id, "action": action}


# ---------------------------------------------------------------------------
# Task dispatcher
# ---------------------------------------------------------------------------

def execute_task(payload: dict) -> dict:
    action = payload.get("action", "")
    params = payload.get("params", {})

    if action == "register_webhook":
        return register_webhook(
            webhook_id=params.get("webhook_id", ""),
            name=params.get("name", ""),
            target_subject=params.get("target_subject", EVENT_SUBJECT),
        )
    elif action == "list_webhooks":
        return list_webhooks()

    elif action == "ingest":
        return _do_ingest(params)

    elif action in ("http_outbound", "crm_write"):
        return {"error": f"{action} requires async context; use handle_event"}

    else:
        return {"error": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes):
    try:
        payload = json.loads(raw.decode())
    except Exception as exc:
        result = {"error": f"Invalid JSON: {exc}"}
        await nc.publish(RESPONSE_SUBJECT, json.dumps(result).encode())
        return

    action = payload.get("action", "")
    params = payload.get("params", {})

    if action == "http_outbound":
        approval_payload = _build_outbound_payload(
            url=params.get("url", ""),
            method=params.get("method", "GET"),
            headers=params.get("headers", {}),
            body=params.get("body", {}),
        )
        result = await _request_approval(nc, action, approval_payload)

    elif action == "crm_write":
        crm_payload = _build_crm_payload(
            crm_type=params.get("crm_type", "generic"),
            record_type=params.get("record_type", ""),
            data=params.get("data", {}),
        )
        if "error" in crm_payload:
            result = crm_payload
        else:
            await nc.publish(CRM_SUBJECT, json.dumps(crm_payload).encode())
            result = await _request_approval(nc, action, crm_payload)

    else:
        result = execute_task(payload)

    result["operator"] = NAME
    result["version"] = VERSION
    await nc.publish(RESPONSE_SUBJECT, json.dumps(result).encode())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_loop():
    global _nc, _loop
    _loop = asyncio.get_event_loop()
    _nc = await nats.connect(NATS_URL)
    print(f"[{NAME}] Connected to NATS at {NATS_URL}")

    async def _cb(msg):
        await handle_event(_nc, msg.subject, msg.data)

    await _nc.subscribe(CALL_SUBJECT, cb=_cb)
    print(f"[{NAME}] Subscribed to {CALL_SUBJECT}")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await _nc.drain()


def main():
    server = HTTPServer(("0.0.0.0", PORT), _IngestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[{NAME}] HTTP ingest + health endpoint running on port {PORT}")

    asyncio.run(_nats_loop())


if __name__ == "__main__":
    main()
