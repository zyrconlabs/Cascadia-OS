#!/usr/bin/env python3
"""
Outlook Connector — CON-014
Cascadia OS DEPOT packaging

Send and receive email via the Microsoft Graph API
(Outlook, Microsoft 365).

Port: 9501
NATS subject: cascadia.connectors.outlook-connector.>
Auth: OAuth2 Bearer access_token
"""

import asyncio
import json
import logging
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "outlook-connector"
VERSION = "1.0.0"
PORT = 9501
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0/me"
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
# Graph API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _graph_get(path: str, access_token: str, params: dict | None = None) -> dict:
    """GET from the Microsoft Graph API and return parsed JSON."""
    url = f"{GRAPH_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _graph_post(path: str, access_token: str, body: dict) -> tuple[int, dict]:
    """POST to the Microsoft Graph API and return (status_code, parsed JSON).

    Graph's sendMail endpoint returns 202 Accepted with an empty body on
    success, so callers should check the status code rather than the body.
    """
    url = f"{GRAPH_API_BASE}{path}"
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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            return status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"raw": raw.decode("utf-8", errors="replace")}
        return exc.code, payload


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def send_email(
    to: str,
    subject: str,
    body: str,
    access_token: str,
) -> dict:
    """Send an email via Microsoft Graph API.

    POSTs to /me/sendMail with a JSON message envelope.

    Returns:
        dict with keys: ok, status
    """
    log.info("send_email to=%s subject=%s", to, subject)
    envelope = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body,
            },
            "toRecipients": [
                {"emailAddress": {"address": to}}
            ],
        },
        "saveToSentItems": True,
    }
    try:
        status, _ = _graph_post("/sendMail", access_token, envelope)
        ok = status in (200, 202)
        return {"ok": ok, "status": status}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def list_messages(
    access_token: str,
    filter_query: str = "",
    top: int = 10,
) -> dict:
    """List messages from the inbox via Microsoft Graph API.

    Returns:
        dict with keys: ok, messages ([{id, subject, from, receivedDateTime, bodyPreview}])
    """
    log.info("list_messages filter=%r top=%d", filter_query, top)
    params: dict = {"$top": top}
    if filter_query:
        params["$filter"] = filter_query
    try:
        result = _graph_get("/messages", access_token, params)
        messages = [
            {
                "id": m.get("id"),
                "subject": m.get("subject"),
                "from": m.get("from", {}).get("emailAddress", {}).get("address"),
                "receivedDateTime": m.get("receivedDateTime"),
                "bodyPreview": m.get("bodyPreview"),
            }
            for m in result.get("value", [])
        ]
        return {"ok": True, "messages": messages}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def get_message(message_id: str, access_token: str) -> dict:
    """Fetch a single message by ID via Microsoft Graph API.

    Returns:
        dict with keys: ok, id, subject, from, receivedDateTime, body
    """
    log.info("get_message message_id=%s", message_id)
    try:
        result = _graph_get(f"/messages/{message_id}", access_token)
        return {
            "ok": True,
            "id": result.get("id"),
            "subject": result.get("subject"),
            "from": result.get("from", {}).get("emailAddress", {}).get("address"),
            "receivedDateTime": result.get("receivedDateTime"),
            "body": result.get("body", {}).get("content"),
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
        )
    elif action == "list_messages":
        return list_messages(
            access_token=access_token,
            filter_query=payload.get("filter_query", ""),
            top=payload.get("top", 10),
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
    """Handle an inbound NATS message on the outlook-connector subject tree.

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
