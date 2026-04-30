#!/usr/bin/env python3
"""
Gmail Connector — CON-013
Cascadia OS DEPOT packaging

Send and receive Gmail messages via the Gmail REST API v1.
Supports OAuth2 and service account auth.

Port: 9500
NATS subject: cascadia.connectors.gmail-connector.>
Auth: OAuth2 Bearer access_token
"""

import asyncio
import base64
import json
import logging
import threading
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "gmail-connector"
VERSION = "1.0.0"
PORT = 9500
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users"
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
ACTIONS_REQUIRING_APPROVAL = {"send_email"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


# ---------------------------------------------------------------------------
# Gmail API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _gmail_get(path: str, access_token: str, params: dict | None = None) -> dict:
    """GET from the Gmail API and return parsed JSON."""
    url = f"{GMAIL_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gmail_post(path: str, access_token: str, body: dict) -> dict:
    """POST to the Gmail API and return parsed JSON."""
    url = f"{GMAIL_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {access_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def send_email(
    to: str,
    subject: str,
    body: str,
    access_token: str,
    sender: str = "me",
) -> dict:
    """Send a Gmail message.

    Encodes an RFC 2822 message as base64url and POSTs it to
    /users/me/messages/send.

    Returns:
        dict with keys: ok, message_id
    """
    log.info("send_email to=%s subject=%s", to, subject)
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = to
    mime["From"] = sender
    mime["Subject"] = subject
    raw_bytes = mime.as_bytes()
    raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

    try:
        result = _gmail_post("/me/messages/send", access_token, {"raw": raw_b64})
        return {"ok": True, "message_id": result.get("id")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def list_messages(
    access_token: str,
    query: str = "",
    max_results: int = 10,
) -> dict:
    """List Gmail messages matching an optional search query.

    Returns:
        dict with keys: ok, messages ([{id, threadId}]), result_size_estimate
    """
    log.info("list_messages query=%r max_results=%d", query, max_results)
    params: dict = {"maxResults": max_results}
    if query:
        params["q"] = query
    try:
        result = _gmail_get("/me/messages", access_token, params)
        return {
            "ok": True,
            "messages": result.get("messages", []),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def get_message(message_id: str, access_token: str) -> dict:
    """Fetch metadata for a single Gmail message.

    Returns:
        dict with keys: ok, id, subject, from, to, date, snippet
    """
    log.info("get_message message_id=%s", message_id)
    try:
        result = _gmail_get(
            f"/me/messages/{message_id}",
            access_token,
            {"format": "metadata"},
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in result.get("payload", {}).get("headers", [])
        }
        return {
            "ok": True,
            "id": result.get("id"),
            "subject": headers.get("subject"),
            "from": headers.get("from"),
            "to": headers.get("to"),
            "date": headers.get("date"),
            "snippet": result.get("snippet"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")
    access_token = payload.get("access_token", "")

    if action == "send_email":
        return send_email(
            to=payload["to"],
            subject=payload["subject"],
            body=payload["body"],
            access_token=access_token,
            sender=payload.get("sender", "me"),
        )
    elif action == "list_messages":
        return list_messages(
            access_token=access_token,
            query=payload.get("query", ""),
            max_results=payload.get("max_results", 10),
        )
    elif action == "get_message":
        return get_message(
            message_id=payload["message_id"],
            access_token=access_token,
        )
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the gmail-connector subject tree.

    Flow:
      1. Parse JSON from raw bytes.
      2. If the action requires approval, publish to cascadia.approvals.request
         and return — do NOT execute yet.
      3. Otherwise call execute_call, publish result to the response subject.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.error("Failed to parse inbound message: %s", exc)
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
# Health HTTP server
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(
            {
                "status": "healthy",
                "connector": NAME,
                "version": VERSION,
                "port": PORT,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # suppress default access log noise
        pass


def _start_health_server() -> threading.Thread:
    server = HTTPServer(("0.0.0.0", PORT), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on port %d", PORT)
    return thread


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_main() -> None:
    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed — NATS subscribe disabled")
        await asyncio.sleep(float("inf"))
        return

    nc = await nats.connect(NATS_URL)
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
    _start_health_server()
    asyncio.run(_nats_main())


if __name__ == "__main__":
    main()
