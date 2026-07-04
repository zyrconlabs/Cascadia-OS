#!/usr/bin/env python3
"""Apple Local Connector Phase 1 skeleton.

The connector is local-only and intentionally performs no real Apple app
access in this phase.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

try:
    from . import state
    from .apple_bridge import AppleBridge, build_live_bridge
    from .schemas import (
        approval_required_response,
        error_response,
        unknown_action_response,
    )
except ImportError:  # pragma: no cover - supports direct script execution
    import state
    from apple_bridge import AppleBridge, build_live_bridge
    from schemas import (
        approval_required_response,
        error_response,
        unknown_action_response,
    )

NAME = "apple-local-connector"
VERSION = "1.0.0"
PORT = 9601
HOST = "127.0.0.1"
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"

# Owner-facing Telegram approval relay (Stage 3A). The connector notifies the
# owner directly via the telegram operator (:9000), mirroring how Mentor sends
# its own inline-keyboard prompts rather than routing through the dormant
# generic approval-gate bus.
TELEGRAM_URL = os.environ.get("TELEGRAM_URL", "http://127.0.0.1:9000")
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "1535010257"))

# Approved mutating action → (bridge domain, adapter method). Only actions with
# a real adapter method are listed; update/complete/archive have no adapter yet
# and fall through to a structured "not implemented" error when approved.
_MUTATION_DISPATCH = {
    "calendar.create_event": ("calendar", "create_event"),
    "calendar.delete_event": ("calendar", "delete_event"),
    "reminders.create_item": ("reminders", "create_item"),
    "reminders.delete_item": ("reminders", "delete_item"),
    "notes.create_note": ("notes", "create_note"),
    "notes.delete_note": ("notes", "delete_note"),
}

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


def _execute_mutation(
    action: str, payload: dict[str, Any], bridge: AppleBridge
) -> dict[str, Any]:
    """Route an APPROVED mutating action to its real bridge adapter method."""
    target = _MUTATION_DISPATCH.get(action)
    if target is None:
        return error_response(f"mutating action not implemented: {action}")
    domain, method = target
    adapter = getattr(bridge, domain)
    try:
        return getattr(adapter, method)(**payload)
    except Exception as exc:  # noqa: BLE001
        return error_response(f"{action} failed: {exc}")


def execute_call(payload: dict[str, Any], bridge: AppleBridge | None = None) -> dict[str, Any]:
    """Dispatch read-only actions and APPROVED mutating actions to the bridge."""
    action = payload.get("action")
    bridge = bridge or AppleBridge()

    if action in MUTATING_ACTIONS:
        if payload.get("approved") is True:
            return _execute_mutation(action, payload, bridge)
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


def _human_summary(action: str, payload: dict[str, Any]) -> str:
    """Short owner-readable description of a pending mutation."""
    label = (
        payload.get("title")
        or payload.get("note_id")
        or payload.get("event_id")
        or payload.get("item_id")
        or ""
    )
    parts = [action.replace(".", " ").replace("_", " ")]
    if label:
        parts.append(f"'{label}'")
    if payload.get("start"):
        parts.append(f"at {payload['start']}")
    return " ".join(parts)


def _send_telegram_approval(request_id: str, action: str, payload: dict[str, Any]) -> None:
    """POST an inline Approve/Deny prompt to the telegram operator (:9000).

    Blocking urllib call — run off the event loop via run_in_executor so a slow
    telegram operator can never stall the NATS handler.
    """
    body = {
        "chat_id": OWNER_CHAT_ID,
        "text": f"🍎 Apple Local wants to: {_human_summary(action, payload)}",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"apple:approve:{request_id}"},
                {"text": "❌ Deny", "callback_data": f"apple:deny:{request_id}"},
            ]]
        },
    }
    try:
        req = urllib.request.Request(
            f"{TELEGRAM_URL}/send",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram approval notify failed (request_id=%s): %s", request_id, exc)


def handle_callback(data: str, bridge: AppleBridge | None = None) -> dict[str, Any]:
    """Resolve an ``apple:approve:<id>`` / ``apple:deny:<id>`` owner decision.

    Returns {"ok": True, "text": <edit-text>} — the same {"text": ...} contract
    Mentor's /api/callback uses, so CHIEF can edit the message with resp["text"].
    Unknown/expired ids are a safe no-op, never a crash.
    """
    bridge = bridge or AppleBridge()
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "apple":
        return {"ok": True, "text": "⚠️ Unrecognized Apple approval action."}
    _, verb, request_id = parts

    entry = state.pop_pending_approval(request_id)
    if entry is None:
        return {"ok": True, "text": "⌛ That approval request was not found or already handled."}

    action = entry["action"]
    if verb == "deny":
        return {"ok": True, "text": f"❌ Denied: {action}"}
    if verb == "approve":
        exec_payload = dict(entry["payload"])
        exec_payload["approved"] = True
        result = execute_call(exec_payload, bridge=bridge)
        if result.get("ok"):
            return {"ok": True, "text": f"✅ Approved &amp; executed: {action}"}
        reason = result.get("error") or result.get("reason") or result.get("status") or "unknown error"
        return {"ok": True, "text": f"⚠️ Approved but failed: {action} — {reason}"}
    return {"ok": True, "text": "⚠️ Unknown Apple approval verb."}


async def handle_event(nc, subject: str, raw: bytes, bridge: AppleBridge | None = None) -> None:
    # Wildcard subscribe (cascadia.connectors.<NAME>.>) also matches our own
    # published response subject — drop those so we never re-ingest our output.
    if subject == RESPONSE_SUBJECT:
        return

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.error("Failed to parse inbound message: %s", exc)
        return

    action = payload.get("action", "")
    log.info("handle_event subject=%s action=%s", subject, action)

    if action in ACTIONS_REQUIRING_APPROVAL and payload.get("approved") is not True:
        request_id = state.add_pending_approval(action, payload)
        log.info("Parked '%s' for approval as request_id=%s", action, request_id)
        await asyncio.get_running_loop().run_in_executor(
            None, _send_telegram_approval, request_id, action, payload
        )
        return

    try:
        result = execute_call(payload, bridge=bridge)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)}

    response = {"connector": NAME, "action": action, "result": result}
    await nc.publish(RESPONSE_SUBJECT, json.dumps(response).encode("utf-8"))


class _HealthHandler(BaseHTTPRequestHandler):
    # Set to the live bridge by _start_health_server so /health reflects real
    # TCC grant state; stays None under tests, which fall back to a stub bridge.
    bridge: AppleBridge | None = None

    def do_GET(self):  # noqa: N802
        if self.path in ("/health", "/api/health"):
            self._json(200, health_payload(self.bridge))
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        # Owner approve/deny taps arrive here via CHIEF (mirrors mentor:).
        if self.path in ("/api/callback", "/callback"):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"ok": False, "error": "invalid JSON"})
                return
            self._json(200, handle_callback(str(body.get("data", "")), self.bridge))
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


def _start_health_server(bridge: AppleBridge | None = None) -> threading.Thread:
    _HealthHandler.bridge = bridge
    server = HTTPServer((HOST, PORT), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on %s:%d", HOST, PORT)
    return thread


async def _nats_main(bridge: AppleBridge | None = None) -> None:
    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed; NATS subscribe disabled")
        await asyncio.sleep(float("inf"))
        return

    nc = await nats.connect(NATS_URL)

    async def _cb(msg):
        await handle_event(nc, msg.subject, msg.data, bridge=bridge)

    await nc.subscribe(f"cascadia.connectors.{NAME}.>", cb=_cb)
    try:
        await asyncio.sleep(float("inf"))
    finally:
        await nc.drain()


def main() -> None:
    bridge = build_live_bridge()
    _start_health_server(bridge)
    asyncio.run(_nats_main(bridge))


if __name__ == "__main__":
    main()
