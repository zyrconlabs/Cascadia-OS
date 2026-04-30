#!/usr/bin/env python3
"""
Slack Connector — CON-017
Cascadia OS DEPOT packaging

Sends messages and receives events from Slack channels and DMs
via bot token or OAuth2.

Port: 9003
NATS subject: cascadia.connectors.slack-connector.>
Auth: Bearer bot token
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
NAME = "slack-connector"
VERSION = "1.0.0"
PORT = 9003
SLACK_API_BASE = "https://slack.com/api"
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
ACTIONS_REQUIRING_APPROVAL = {"send_message"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


# ---------------------------------------------------------------------------
# Slack API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _slack_post(endpoint: str, token: str, body: dict) -> dict:
    """POST to a Slack Web API endpoint and return the parsed JSON response."""
    url = f"{SLACK_API_BASE}/{endpoint}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _slack_get(endpoint: str, token: str, params: dict | None = None) -> dict:
    """GET from a Slack Web API endpoint and return the parsed JSON response."""
    url = f"{SLACK_API_BASE}/{endpoint}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def send_message(channel: str, text: str, token: str) -> dict:
    """Send a message to a Slack channel or DM.

    Returns:
        dict with keys: ok, ts, channel (mirrors Slack API response)
    """
    log.info("send_message channel=%s", channel)
    result = _slack_post(
        "chat.postMessage",
        token,
        {"channel": channel, "text": text},
    )
    return {
        "ok": result.get("ok", False),
        "ts": result.get("ts"),
        "channel": result.get("channel"),
        "error": result.get("error"),
    }


def list_channels(token: str, limit: int = 200) -> dict:
    """List public channels in the workspace."""
    log.info("list_channels")
    result = _slack_get(
        "conversations.list",
        token,
        {"limit": limit, "exclude_archived": "true"},
    )
    channels = [
        {"id": c.get("id"), "name": c.get("name")}
        for c in result.get("channels", [])
    ]
    return {"ok": result.get("ok", False), "channels": channels}


def get_user(user_id: str, token: str) -> dict:
    """Fetch profile information for a Slack user."""
    log.info("get_user user_id=%s", user_id)
    result = _slack_get("users.info", token, {"user": user_id})
    user = result.get("user", {})
    profile = user.get("profile", {})
    return {
        "ok": result.get("ok", False),
        "id": user.get("id"),
        "name": user.get("name"),
        "real_name": profile.get("real_name"),
        "email": profile.get("email"),
    }


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")
    token = payload.get("token", "")

    if action == "send_message":
        return send_message(
            channel=payload["channel"],
            text=payload["text"],
            token=token,
        )
    elif action == "list_channels":
        return list_channels(token=token, limit=payload.get("limit", 200))
    elif action == "get_user":
        return get_user(user_id=payload["user_id"], token=token)
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the slack-connector subject tree.

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
