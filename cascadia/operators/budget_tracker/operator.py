"""
C7: Budget Tracker — Cascadia OS Operator
Port: 8107  Subject prefix: cascadia.operators.budget-tracker
"""

import asyncio
import csv
import io
import json
import os
import uuid
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import nats

NAME = "budget-tracker"
VERSION = "1.0.0"
PORT = 8107
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

CALL_SUBJECT = f"cascadia.operators.{NAME}.call"
RESPONSE_SUBJECT = f"cascadia.operators.{NAME}.response"
APPROVALS_SUBJECT = "cascadia.approvals.request"

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

budgets: dict = {}       # budget_id → {name, total, currency, spent, category}
transactions: list = []  # [{budget_id, amount, description, vendor, timestamp}]

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def create_budget(name: str, total: float, currency: str = "USD",
                  category: str = "general") -> dict:
    budget_id = str(uuid.uuid4())
    budgets[budget_id] = {
        "id": budget_id,
        "name": name,
        "total": float(total),
        "currency": currency,
        "spent": 0.0,
        "category": category,
        "created_at": datetime.utcnow().isoformat(),
    }
    return {"budget_id": budget_id, **budgets[budget_id]}


def log_expense(budget_id: str, amount: float, description: str,
                vendor: str = "") -> dict:
    if budget_id not in budgets:
        return {"error": f"Budget not found: {budget_id}"}
    amount = float(amount)
    budgets[budget_id]["spent"] += amount
    tx = {
        "tx_id": str(uuid.uuid4()),
        "budget_id": budget_id,
        "amount": amount,
        "description": description,
        "vendor": vendor,
        "timestamp": datetime.utcnow().isoformat(),
    }
    transactions.append(tx)
    remaining = budgets[budget_id]["total"] - budgets[budget_id]["spent"]
    return {"transaction": tx, "remaining": remaining}


def get_budget(budget_id: str) -> dict:
    if budget_id not in budgets:
        return {"error": f"Budget not found: {budget_id}"}
    b = dict(budgets[budget_id])
    b["remaining"] = b["total"] - b["spent"]
    b["pct_used"] = round((b["spent"] / b["total"] * 100) if b["total"] else 0, 2)
    return b


def list_budgets(category: str = None) -> dict:
    result = []
    for b in budgets.values():
        if category and b["category"] != category:
            continue
        entry = dict(b)
        entry["remaining"] = entry["total"] - entry["spent"]
        entry["pct_used"] = round(
            (entry["spent"] / entry["total"] * 100) if entry["total"] else 0, 2
        )
        result.append(entry)
    return {"budgets": result, "count": len(result)}


def generate_report(budget_id: str = None) -> dict:
    scope_budgets = (
        [budgets[budget_id]] if budget_id and budget_id in budgets else list(budgets.values())
    )
    if budget_id and budget_id not in budgets:
        return {"error": f"Budget not found: {budget_id}"}

    scope_ids = {b["id"] for b in scope_budgets}
    scope_txs = [t for t in transactions if t["budget_id"] in scope_ids]

    vendor_totals: dict = {}
    for tx in scope_txs:
        v = tx.get("vendor") or "unknown"
        vendor_totals[v] = vendor_totals.get(v, 0) + tx["amount"]

    top_vendors = sorted(vendor_totals.items(), key=lambda x: -x[1])[:10]

    total_budget = sum(b["total"] for b in scope_budgets)
    total_spent = sum(b["spent"] for b in scope_budgets)

    return {
        "report_generated_at": datetime.utcnow().isoformat(),
        "budget_count": len(scope_budgets),
        "total_budget": total_budget,
        "total_spent": total_spent,
        "total_remaining": total_budget - total_spent,
        "pct_used": round((total_spent / total_budget * 100) if total_budget else 0, 2),
        "top_vendors": [{"vendor": v, "total": t} for v, t in top_vendors],
        "budgets": [get_budget(b["id"]) for b in scope_budgets],
        "transaction_count": len(scope_txs),
    }


def _build_csv_report(budget_id: str = None) -> str:
    report = generate_report(budget_id)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Budget Report", report["report_generated_at"]])
    writer.writerow([])
    writer.writerow(["budget_id", "name", "category", "currency",
                     "total", "spent", "remaining", "pct_used"])
    for b in report["budgets"]:
        writer.writerow([
            b["id"], b["name"], b["category"], b["currency"],
            b["total"], b["spent"], b["remaining"], b["pct_used"],
        ])
    writer.writerow([])
    writer.writerow(["Top Vendors"])
    writer.writerow(["vendor", "total_spend"])
    for vd in report["top_vendors"]:
        writer.writerow([vd["vendor"], vd["total"]])
    return output.getvalue()


# ---------------------------------------------------------------------------
# Approval-gated actions
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

    if action == "create_budget":
        return create_budget(
            name=params.get("name", "Unnamed"),
            total=float(params.get("total", 0)),
            currency=params.get("currency", "USD"),
            category=params.get("category", "general"),
        )
    elif action == "log_expense":
        return log_expense(
            budget_id=params.get("budget_id", ""),
            amount=float(params.get("amount", 0)),
            description=params.get("description", ""),
            vendor=params.get("vendor", ""),
        )
    elif action == "get_budget":
        return get_budget(params.get("budget_id", ""))

    elif action == "list_budgets":
        return list_budgets(category=params.get("category"))

    elif action == "generate_report":
        return generate_report(budget_id=params.get("budget_id"))

    elif action == "export_report":
        return {"error": "export_report requires async context; use handle_event"}

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

    if action == "export_report":
        params = payload.get("params", {})
        # Include CSV preview in approval payload
        csv_data = _build_csv_report(params.get("budget_id"))
        approval_payload = {**params, "csv_preview_chars": len(csv_data),
                            "output_path": params.get("output_path", "report.csv")}
        result = await _request_approval(nc, action, approval_payload)
    else:
        result = execute_task(payload)

    result["operator"] = NAME
    result["version"] = VERSION
    await nc.publish(RESPONSE_SUBJECT, json.dumps(result).encode())


# ---------------------------------------------------------------------------
# Health HTTP server
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            body = json.dumps({"status": "ok", "operator": NAME, "version": VERSION, "port": PORT}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_loop():
    nc = await nats.connect(NATS_URL)
    print(f"[{NAME}] Connected to NATS at {NATS_URL}")

    async def _cb(msg):
        await handle_event(nc, msg.subject, msg.data)

    await nc.subscribe(CALL_SUBJECT, cb=_cb)
    print(f"[{NAME}] Subscribed to {CALL_SUBJECT}")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await nc.drain()


def main():
    health_server = HTTPServer(("0.0.0.0", PORT), _HealthHandler)
    t = threading.Thread(target=health_server.serve_forever, daemon=True)
    t.start()
    print(f"[{NAME}] Health endpoint running on port {PORT}")

    asyncio.run(_nats_loop())


if __name__ == "__main__":
    main()
