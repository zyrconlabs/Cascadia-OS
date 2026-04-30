#!/usr/bin/env python3
"""
Review Requester Operator — Cascadia OS (C4)
NATS: cascadia.operators.review-requester.call / .response
Approval-gated: send_requests, send_single
Direct: create_campaign, list_campaigns, queue_request, list_pending
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

NAME = "review-requester"
VERSION = "1.0.0"
PORT = 8104
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"
SUBJECT_APPROVALS = "cascadia.approvals.request"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [review-requester] %(message)s",
)
log = logging.getLogger(NAME)

# In-memory stores
_campaigns: Dict[str, Dict[str, Any]] = {}   # campaign_id → campaign
_pending: Dict[str, Dict[str, Any]] = {}      # request_id → request


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Core logic ────────────────────────────────────────────────────────────────

def create_campaign(
    name: str,
    platform: str,
    review_url: str,
    message_template: str,
) -> Dict[str, Any]:
    campaign_id = f"CAM-{_uid().upper()}"
    campaign = {
        "campaign_id": campaign_id,
        "name": name,
        "platform": platform,
        "review_url": review_url,
        "message_template": message_template,
        "created_at": _now(),
    }
    _campaigns[campaign_id] = campaign
    log.info("Created campaign %s name=%s platform=%s", campaign_id, name, platform)
    return campaign


def list_campaigns() -> List[Dict[str, Any]]:
    return sorted(_campaigns.values(), key=lambda c: c.get("created_at", ""))


def queue_request(
    campaign_id: str,
    customer_email: str,
    customer_name: str,
    order_ref: str = "",
) -> Dict[str, Any]:
    if campaign_id not in _campaigns:
        return {"ok": False, "error": f"campaign not found: {campaign_id}"}
    request_id = f"RRQ-{_uid().upper()}"
    req = {
        "request_id": request_id,
        "campaign_id": campaign_id,
        "customer_email": customer_email,
        "customer_name": customer_name,
        "order_ref": order_ref,
        "status": "pending",
        "queued_at": _now(),
    }
    _pending[request_id] = req
    log.info("Queued review request %s for %s campaign=%s", request_id, customer_email, campaign_id)
    return {"ok": True, "request": req}


def list_pending(campaign_id: Optional[str] = None) -> List[Dict[str, Any]]:
    results = [r for r in _pending.values() if r.get("status") == "pending"]
    if campaign_id:
        results = [r for r in results if r.get("campaign_id") == campaign_id]
    results.sort(key=lambda r: r.get("queued_at", ""))
    return results


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "create_campaign":
        campaign = create_campaign(
            name=payload.get("name", ""),
            platform=payload.get("platform", ""),
            review_url=payload.get("review_url", ""),
            message_template=payload.get("message_template", ""),
        )
        return {"ok": True, "action": action, "campaign": campaign}

    if action == "list_campaigns":
        campaigns = list_campaigns()
        return {"ok": True, "action": action, "campaigns": campaigns, "count": len(campaigns)}

    if action == "queue_request":
        return {
            "action": action,
            **queue_request(
                campaign_id=payload.get("campaign_id", ""),
                customer_email=payload.get("customer_email", ""),
                customer_name=payload.get("customer_name", ""),
                order_ref=payload.get("order_ref", ""),
            ),
        }

    if action == "list_pending":
        results = list_pending(campaign_id=payload.get("campaign_id"))
        return {"ok": True, "action": action, "pending": results, "count": len(results)}

    if action in ("send_requests", "send_single"):
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

    if action in ("send_requests", "send_single"):
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
                "campaigns": len(_campaigns),
                "pending_requests": len([r for r in _pending.values() if r.get("status") == "pending"]),
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
