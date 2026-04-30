#!/usr/bin/env python3
"""
SMS / Twilio Connector — CON-021
Cascadia OS DEPOT packaging

Sends SMS messages via the Twilio Programmable Messaging API.

Port: 9002
NATS subject: cascadia.connectors.sms-connector.>
Auth: Basic (account_sid + auth_token)

NOTE: SMS dispatch ALWAYS requires human approval before execution.
"""

import asyncio
import base64
import json
import logging
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "sms-connector"
VERSION = "1.0.0"
PORT = 9002
TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
# SMS always requires approval — all send actions gate on this
ACTIONS_REQUIRING_APPROVAL = {"send_sms"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


# ---------------------------------------------------------------------------
# Twilio REST API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _twilio_post(path: str, account_sid: str, auth_token: str, body: dict) -> dict:
    """POST to the Twilio REST API using HTTP Basic auth.

    Args:
        path: URL path relative to TWILIO_API_BASE (e.g.
              'Accounts/{sid}/Messages.json').
        account_sid: Twilio account SID (used as Basic auth username).
        auth_token: Twilio auth token (used as Basic auth password).
        body: Form-encoded body fields as a dict.

    Returns:
        Parsed JSON response dict.
    """
    url = f"{TWILIO_API_BASE}/{path}"
    # Twilio REST API uses application/x-www-form-urlencoded
    data = urllib.parse.urlencode(body).encode("utf-8")
    credentials = base64.b64encode(
        f"{account_sid}:{auth_token}".encode("utf-8")
    ).decode("ascii")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def send_sms(
    from_number: str,
    to_number: str,
    body: str,
    account_sid: str,
    auth_token: str,
) -> dict:
    """Send an SMS via Twilio.

    Args:
        from_number: Twilio phone number in E.164 format (e.g. '+15551234567').
        to_number:   Recipient phone number in E.164 format.
        body:        Message text.
        account_sid: Twilio account SID.
        auth_token:  Twilio auth token.

    Returns:
        dict with keys: ok, sid, status, to, from_number
    """
    log.info("send_sms from=%s to=%s", from_number, to_number)
    result = _twilio_post(
        f"Accounts/{account_sid}/Messages.json",
        account_sid,
        auth_token,
        {"From": from_number, "To": to_number, "Body": body},
    )

    # Twilio returns 'sid' and 'status' on success; 'message' + 'code' on error
    if "sid" in result:
        return {
            "ok": True,
            "sid": result.get("sid"),
            "status": result.get("status"),
            "to": result.get("to"),
            "from_number": result.get("from"),
        }
    return {
        "ok": False,
        "error": result.get("message", str(result)),
        "code": result.get("code"),
    }


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")

    if action == "send_sms":
        return send_sms(
            from_number=payload["from_number"],
            to_number=payload["to_number"],
            body=payload["body"],
            account_sid=payload["account_sid"],
            auth_token=payload["auth_token"],
        )
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the sms-connector subject tree.

    Flow:
      1. Parse JSON from raw bytes.
      2. SMS send_sms ALWAYS requires approval — publish to
         cascadia.approvals.request and return without executing.
      3. For any other action call execute_call, publish result to the
         response subject.
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
            "reason": (
                "SMS dispatch always requires human approval before execution "
                "to prevent accidental or unauthorised messages."
            ),
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

    def log_message(self, fmt, *args):
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
