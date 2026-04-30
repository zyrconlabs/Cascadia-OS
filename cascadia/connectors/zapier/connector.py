#!/usr/bin/env python3
"""
Zapier Inbound Connector — B4
Cascadia OS · Zyrcon Labs · v1.0.0

Receives webhook payloads FROM Zapier and publishes them as NATS events.
Also supports sending data TO external Zapier webhook URLs.

Port: 9030
NATS subjects:
  cascadia.connectors.zapier-connector.call      — inbound action calls
  cascadia.connectors.zapier-connector.response  — action results
  cascadia.connectors.zapier-connector.event     — inbound webhook events from Zapier
"""

import asyncio
import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "zapier-connector"
VERSION = "1.0.0"
PORT = 9030
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
EVENT_SUBJECT = f"cascadia.connectors.{NAME}.event"

ACTIONS_REQUIRING_APPROVAL = {"send_to_zapier", "register_hook", "delete_hook"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)

# ---------------------------------------------------------------------------
# In-memory hook registry
# hook_id -> {"name": str, "target_operator": str, "created_at": str}
# ---------------------------------------------------------------------------
_HOOKS: Dict[str, Dict[str, Any]] = {}

# Shared NATS connection — injected at startup so the HTTP thread can publish
_nc: Optional[Any] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


# ---------------------------------------------------------------------------
# Hook registry helpers
# ---------------------------------------------------------------------------

def register_hook(hook_id: str, name: str, target_operator: str) -> dict:
    """Add a hook to the in-memory registry and return its webhook URL."""
    _HOOKS[hook_id] = {
        "name": name,
        "target_operator": target_operator,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("register_hook hook_id=%s name=%s target=%s", hook_id, name, target_operator)
    return {
        "ok": True,
        "hook_id": hook_id,
        "webhook_url": f"http://127.0.0.1:{PORT}/webhook/{hook_id}",
    }


def delete_hook(hook_id: str) -> dict:
    """Remove a hook from the registry."""
    removed = _HOOKS.pop(hook_id, None)
    log.info("delete_hook hook_id=%s found=%s", hook_id, removed is not None)
    return {"ok": True, "hook_id": hook_id, "deleted": removed is not None}


def list_hooks() -> dict:
    """Return the list of registered hooks."""
    hooks = [
        {"hook_id": hid, **meta}
        for hid, meta in _HOOKS.items()
    ]
    log.info("list_hooks count=%d", len(hooks))
    return {"ok": True, "hooks": hooks}


# ---------------------------------------------------------------------------
# Outbound: send data to an external Zapier webhook URL
# ---------------------------------------------------------------------------

def send_to_zapier(webhook_url: str, payload: dict) -> dict:
    """POST JSON payload to an external Zapier webhook URL."""
    log.info("send_to_zapier url=%s", webhook_url)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
            try:
                result_body = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                result_body = body
            return {"ok": True, "status_code": status, "response": result_body}
    except urllib.request.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")

    if action == "send_to_zapier":
        return send_to_zapier(
            webhook_url=payload["webhook_url"],
            payload=payload.get("payload", {}),
        )
    elif action == "register_hook":
        return register_hook(
            hook_id=payload["hook_id"],
            name=payload.get("name", payload["hook_id"]),
            target_operator=payload.get("target_operator", ""),
        )
    elif action == "delete_hook":
        return delete_hook(hook_id=payload["hook_id"])
    elif action == "list_hooks":
        return list_hooks()
    elif action == "receive_webhook":
        # Informational — actual ingest happens via HTTP path; return registry state
        return list_hooks()
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the zapier-connector subject tree.

    Flow:
      1. Parse JSON from raw bytes.
      2. If the action requires approval, publish to cascadia.approvals.request
         and return — execution is deferred until approval is granted.
      3. Otherwise call execute_call and publish result to the response subject.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.error("Failed to parse inbound message on %s: %s", subject, exc)
        return

    action = payload.get("action", "")
    log.info("handle_event subject=%s action=%s", subject, action)

    if action in ACTIONS_REQUIRING_APPROVAL:
        approval_request = {
            "connector": NAME,
            "subject": subject,
            "action": action,
            "payload": payload,
            "reason": f"Action '{action}' requires human approval before execution.",
        }
        await nc.publish(
            APPROVAL_SUBJECT,
            json.dumps(approval_request).encode("utf-8"),
        )
        log.info("Published approval request for action=%s", action)
        # Return approval-pending status to any reply subject
        if hasattr(raw, "reply") and raw.reply:  # type: ignore[attr-defined]
            await nc.publish(
                raw.reply,  # type: ignore[attr-defined]
                json.dumps({"ok": True, "status": "pending_approval"}).encode("utf-8"),
            )
        return

    try:
        result = execute_call(payload)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)}

    response = {"connector": NAME, "action": action, "result": result}
    await nc.publish(
        RESPONSE_SUBJECT,
        json.dumps(response).encode("utf-8"),
    )
    log.info("Published response for action=%s ok=%s", action, result.get("ok"))


# ---------------------------------------------------------------------------
# Combined HTTP server: /health (GET) + /webhook/{hook_id} (POST)
# ---------------------------------------------------------------------------

def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Return (or lazily create) the shared asyncio event loop for thread-safe NATS ops."""
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, daemon=True).start()
    return _loop


class _ZapierHandler(BaseHTTPRequestHandler):
    """Single HTTP handler for both health checks and inbound Zapier webhooks."""

    def _json_response(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    # -- GET ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/health":
            self._json_response(200, {"status": "ok", "connector": NAME})
        else:
            self._json_response(404, {"error": "not found"})

    # -- POST -----------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        parts = [p for p in path.split("/") if p]

        # Expect /webhook/{hook_id}
        if len(parts) != 2 or parts[0] != "webhook":
            self._json_response(
                400, {"error": "path must be POST /webhook/{hook_id}"}
            )
            return

        hook_id = parts[1]
        body = self._read_body()

        try:
            payload = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            payload = {"raw": body.decode("utf-8", errors="replace")}

        event = {
            "connector": NAME,
            "hook_id": hook_id,
            "registered": hook_id in _HOOKS,
            "target_operator": _HOOKS.get(hook_id, {}).get("target_operator", ""),
            "data": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info("Received webhook hook_id=%s registered=%s", hook_id, event["registered"])

        if _nc is not None:
            asyncio.run_coroutine_threadsafe(
                _nc.publish(EVENT_SUBJECT, json.dumps(event).encode("utf-8")),
                _get_event_loop(),
            )
            self._json_response(200, {"ok": True, "hook_id": hook_id, "subject": EVENT_SUBJECT})
        else:
            log.warning("NATS unavailable — event from hook_id=%s not published", hook_id)
            self._json_response(200, {"ok": True, "hook_id": hook_id, "queued": False})

    def log_message(self, fmt, *args) -> None:  # suppress default access log noise
        pass


def _start_http_server() -> threading.Thread:
    """Start the combined webhook + health HTTP server in a background thread."""
    server = HTTPServer(("0.0.0.0", PORT), _ZapierHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("%s v%s HTTP server listening on port %d", NAME, VERSION, PORT)
    return thread


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_main() -> None:
    global _nc, _loop

    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed — NATS subscribe disabled (HTTP-only mode)")
        await asyncio.sleep(float("inf"))
        return

    # Capture the running loop so the HTTP thread can schedule coroutines
    _loop = asyncio.get_running_loop()

    nc = await nats.connect(NATS_URL)
    _nc = nc
    log.info("Connected to NATS at %s", NATS_URL)

    subject = f"cascadia.connectors.{NAME}.>"

    async def _cb(msg):
        await handle_event(nc, msg.subject, msg.data)

    await nc.subscribe(subject, cb=_cb)
    log.info("Subscribed to %s", subject)

    try:
        await asyncio.sleep(float("inf"))
    finally:
        await nc.drain()


def main() -> None:
    _start_http_server()
    asyncio.run(_nats_main())


if __name__ == "__main__":
    main()
