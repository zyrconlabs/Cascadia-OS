#!/usr/bin/env python3
"""
Invoice Generator Operator — Cascadia OS (C1)
NATS: cascadia.operators.invoice-generator.call / .response
Approval-gated: send_invoice, save_invoice
Direct: generate_invoice
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

NAME = "invoice-generator"
VERSION = "1.0.0"
PORT = 8101
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT_CALL = f"cascadia.operators.{NAME}.call"
SUBJECT_RESPONSE = f"cascadia.operators.{NAME}.response"
SUBJECT_APPROVALS = "cascadia.approvals.request"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [invoice-generator] %(message)s",
)
log = logging.getLogger(NAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Core invoice logic ────────────────────────────────────────────────────────

def generate_invoice(
    client_name: str,
    client_email: str,
    items: List[Dict[str, Any]],
    currency: str = "USD",
    due_days: int = 30,
    company_name: str = "",
) -> Dict[str, Any]:
    """
    Build a plain-text invoice with ASCII table, totals, and due date.
    items: [{"description": str, "quantity": float, "unit_price": float}]
    Returns {"invoice_id": str, "text": str, "total": float, "due_date": str, ...}
    """
    invoice_id = f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{_uid().upper()[:6]}"
    issue_date = datetime.now(timezone.utc).date()
    due_date = issue_date + timedelta(days=due_days)

    # Compute line totals
    enriched = []
    for it in items:
        qty = float(it.get("quantity", 1))
        price = float(it.get("unit_price", 0))
        total = round(qty * price, 2)
        enriched.append({
            "description": str(it.get("description", "")),
            "quantity": qty,
            "unit_price": price,
            "line_total": total,
        })

    subtotal = round(sum(i["line_total"] for i in enriched), 2)
    tax_rate = 0.0
    tax_amount = round(subtotal * tax_rate, 2)
    grand_total = round(subtotal + tax_amount, 2)

    sym = currency

    # Build ASCII table
    col_w = [42, 8, 12, 12]
    sep = "+" + "+".join("-" * w for w in col_w) + "+"
    header_row = "| {:<40} | {:>6} | {:>10} | {:>10} |".format(
        "Description", "Qty", f"Unit ({sym})", f"Total ({sym})"
    )

    rows = []
    for it in enriched:
        rows.append(
            "| {:<40} | {:>6.2f} | {:>10.2f} | {:>10.2f} |".format(
                it["description"][:40], it["quantity"], it["unit_price"], it["line_total"]
            )
        )

    company_header = company_name if company_name else "Your Company"

    lines = [
        "=" * 68,
        f"  INVOICE — {company_header}",
        "=" * 68,
        f"  Invoice #:  {invoice_id}",
        f"  Issue Date: {issue_date}",
        f"  Due Date:   {due_date}",
        f"  Bill To:    {client_name} <{client_email}>",
        "",
        sep,
        header_row,
        sep,
    ]
    lines.extend(rows)
    lines.append(sep)
    lines.append(f"  {'Subtotal:':>50}  {sym} {subtotal:>10.2f}")
    if tax_amount:
        lines.append(f"  {'Tax:':>50}  {sym} {tax_amount:>10.2f}")
    lines.append(f"  {'TOTAL DUE:':>50}  {sym} {grand_total:>10.2f}")
    lines.append("")
    lines.append(f"  Payment due by {due_date}. Thank you for your business.")
    lines.append("=" * 68)

    text = "\n".join(lines)

    return {
        "invoice_id": invoice_id,
        "text": text,
        "client_name": client_name,
        "client_email": client_email,
        "currency": currency,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total": grand_total,
        "issue_date": str(issue_date),
        "due_date": str(due_date),
        "items": enriched,
        "generated_at": _now(),
    }


# ── execute_task dispatcher ───────────────────────────────────────────────────

def execute_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action", "")

    if action == "generate_invoice":
        items = payload.get("items", [])
        if not items:
            return {"ok": False, "error": "items list is required"}
        result = generate_invoice(
            client_name=payload.get("client_name", ""),
            client_email=payload.get("client_email", ""),
            items=items,
            currency=payload.get("currency", "USD"),
            due_days=int(payload.get("due_days", 30)),
            company_name=payload.get("company_name", ""),
        )
        return {"ok": True, "action": action, "result": result}

    if action == "send_invoice":
        return {
            "ok": True,
            "action": action,
            "status": "approval_required",
            "message": "send_invoice requires approval — publish to cascadia.approvals.request",
        }

    if action == "save_invoice":
        return {
            "ok": True,
            "action": action,
            "status": "approval_required",
            "message": "save_invoice requires approval — publish to cascadia.approvals.request",
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

    if action in ("send_invoice", "save_invoice"):
        # Gate: publish approval request, do not act directly
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
    elif action == "generate_invoice":
        response = execute_task(payload)
    else:
        response = {"ok": False, "error": f"unknown action: {action}"}

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
    def log_message(self, fmt, *args):  # silence default access log
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
            self._json(200, {"status": "ok", "operator": NAME, "version": VERSION, "port": PORT})
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
