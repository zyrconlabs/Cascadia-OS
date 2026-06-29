#!/usr/bin/env python3
"""Apple Local Connector Phase 1 skeleton.

The connector is local-only and intentionally performs no real Apple app
access in this phase.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

try:
    from .apple_bridge import AppleBridge
    from .schemas import (
        approval_required_response,
        phase_1_not_implemented_response,
        unknown_action_response,
    )
except ImportError:  # pragma: no cover - supports direct script execution
    from apple_bridge import AppleBridge
    from schemas import (
        approval_required_response,
        phase_1_not_implemented_response,
        unknown_action_response,
    )

NAME = "apple-local-connector"
VERSION = "1.0.0"
PORT = 9601
HOST = "127.0.0.1"
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"

READ_ONLY_ACTIONS = {
    "calendar.list_calendars",
    "calendar.list_events",
    "calendar.get_event",
    "reminders.list_lists",
    "reminders.list_items",
    "reminders.get_item",
    "notes.list_folders",
    "notes.search",
    "notes.get_note",
}

MUTATING_ACTIONS = {
    "calendar.create_event",
    "calendar.update_event",
    "calendar.delete_event",
    "reminders.create_item",
    "reminders.update_item",
    "reminders.complete_item",
    "reminders.delete_item",
    "notes.create_note",
    "notes.update_note",
    "notes.archive_note",
    "notes.delete_note",
}

ACTIONS_REQUIRING_APPROVAL = MUTATING_ACTIONS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


def health_payload(bridge: AppleBridge | None = None) -> dict[str, Any]:
    bridge = bridge or AppleBridge()
    readiness = bridge.readiness()
    ready = all(
        bool(readiness[domain].get("available"))
        for domain in ("calendar", "reminders", "notes")
    )
    return {
        "status": "healthy" if ready else "degraded",
        "ok": True,
        "connector": NAME,
        "version": VERSION,
        "port": PORT,
        "host": HOST,
        "phase": 1,
        "readiness": readiness,
    }


def execute_call(payload: dict[str, Any], bridge: AppleBridge | None = None) -> dict[str, Any]:
    """Dispatch Phase 1 read-only actions to mockable adapters."""
    action = payload.get("action")
    bridge = bridge or AppleBridge()

    if action in MUTATING_ACTIONS:
        if payload.get("approved") is True:
            return phase_1_not_implemented_response(action)
        return approval_required_response(action)

    if action == "calendar.list_calendars":
        return bridge.calendar.list_calendars()
    if action == "calendar.list_events":
        return bridge.calendar.list_events(**payload)
    if action == "calendar.get_event":
        return bridge.calendar.get_event(**payload)
    if action == "reminders.list_lists":
        return bridge.reminders.list_lists()
    if action == "reminders.list_items":
        return bridge.reminders.list_items(**payload)
    if action == "reminders.get_item":
        return bridge.reminders.get_item(**payload)
    if action == "notes.list_folders":
        return bridge.notes.list_folders()
    if action == "notes.search":
        return bridge.notes.search(**payload)
    if action == "notes.get_note":
        return bridge.notes.get_note(**payload)

    return unknown_action_response(action)


async def handle_event(nc, subject: str, raw: bytes) -> None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.error("Failed to parse inbound message: %s", exc)
        return

    action = payload.get("action", "")
    log.info("handle_event subject=%s action=%s", subject, action)

    if action in ACTIONS_REQUIRING_APPROVAL and payload.get("approved") is not True:
        approval_request = {
            "connector": NAME,
            "subject": subject,
            "action": action,
            "payload": payload,
            "reason": f"Action '{action}' requires human approval before execution.",
        }
        await nc.publish(APPROVAL_SUBJECT, json.dumps(approval_request).encode("utf-8"))
        return

    try:
        result = execute_call(payload)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)}

    response = {"connector": NAME, "action": action, "result": result}
    await nc.publish(RESPONSE_SUBJECT, json.dumps(response).encode("utf-8"))


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path in ("/health", "/api/health"):
            self._json(200, health_payload())
            return
        self._json(404, {"ok": False, "error": "not found"})

    def _json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # suppress default access log noise
        pass


def _start_health_server() -> threading.Thread:
    server = HTTPServer((HOST, PORT), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on %s:%d", HOST, PORT)
    return thread


async def _nats_main() -> None:
    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed; NATS subscribe disabled")
        await asyncio.sleep(float("inf"))
        return

    nc = await nats.connect(NATS_URL)

    async def _cb(msg):
        await handle_event(nc, msg.subject, msg.data)

    await nc.subscribe(f"cascadia.connectors.{NAME}.>", cb=_cb)
    try:
        await asyncio.sleep(float("inf"))
    finally:
        await nc.drain()


def main() -> None:
    _start_health_server()
    asyncio.run(_nats_main())


if __name__ == "__main__":
    main()
