#!/usr/bin/env python3
"""
Microsoft Teams Connector — CON-016
Cascadia OS DEPOT packaging

Send messages to Microsoft Teams channels and chats via the Microsoft Graph API.
Approval is required before any message is sent.

Port: 9503
NATS subject: cascadia.connectors.teams-connector.>
Auth: OAuth2 Bearer access token
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
NAME = "teams-connector"
VERSION = "1.0.0"
PORT = 9503
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
ACTIONS_REQUIRING_APPROVAL = {"send_channel_message", "send_chat_message"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


# ---------------------------------------------------------------------------
# Microsoft Graph API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _graph_post(path: str, access_token: str, body: dict) -> dict:
    """POST to the Graph API and return parsed JSON."""
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _graph_get(path: str, access_token: str, params: dict | None = None) -> dict:
    """GET from the Graph API and return parsed JSON."""
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


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def send_channel_message(
    team_id: str,
    channel_id: str,
    content: str,
    access_token: str,
) -> dict:
    """Send a message to a Teams channel.

    Args:
        team_id: Microsoft Teams team identifier.
        channel_id: Channel identifier within the team.
        content: Plain-text message body.
        access_token: OAuth2 Bearer token.

    Returns:
        dict with keys: ok, message_id
    """
    log.info("send_channel_message team_id=%s channel_id=%s", team_id, channel_id)
    path = (
        f"/teams/{urllib.parse.quote(team_id, safe='')}"
        f"/channels/{urllib.parse.quote(channel_id, safe='')}"
        f"/messages"
    )
    body = {"body": {"content": content, "contentType": "text"}}
    result = _graph_post(path, access_token, body)
    return {
        "ok": True,
        "message_id": result.get("id"),
    }


def send_chat_message(chat_id: str, content: str, access_token: str) -> dict:
    """Send a message to a Teams chat (1:1 or group).

    Args:
        chat_id: Chat identifier.
        content: Plain-text message body.
        access_token: OAuth2 Bearer token.

    Returns:
        dict with keys: ok, message_id
    """
    log.info("send_chat_message chat_id=%s", chat_id)
    path = f"/chats/{urllib.parse.quote(chat_id, safe='')}/messages"
    body = {"body": {"content": content, "contentType": "text"}}
    result = _graph_post(path, access_token, body)
    return {
        "ok": True,
        "message_id": result.get("id"),
    }


def list_channels(team_id: str, access_token: str) -> dict:
    """List all channels in a Teams team.

    Args:
        team_id: Microsoft Teams team identifier.
        access_token: OAuth2 Bearer token.

    Returns:
        dict with keys: ok, channels (list of {id, displayName})
    """
    log.info("list_channels team_id=%s", team_id)
    path = f"/teams/{urllib.parse.quote(team_id, safe='')}/channels"
    result = _graph_get(path, access_token)
    channels = [
        {"id": ch.get("id"), "displayName": ch.get("displayName", "")}
        for ch in result.get("value", [])
    ]
    return {"ok": True, "channels": channels}


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")
    token = payload.get("access_token", "")

    if action == "send_channel_message":
        return send_channel_message(
            team_id=payload["team_id"],
            channel_id=payload["channel_id"],
            content=payload["content"],
            access_token=token,
        )
    elif action == "send_chat_message":
        return send_chat_message(
            chat_id=payload["chat_id"],
            content=payload["content"],
            access_token=token,
        )
    elif action == "list_channels":
        return list_channels(
            team_id=payload["team_id"],
            access_token=token,
        )
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the teams-connector subject tree.

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
        # Keep process alive so health endpoint stays up
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
