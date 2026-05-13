#!/usr/bin/env python3
"""
Telegram Connector — CON-018
Cascadia OS DEPOT packaging

Sends messages and receives updates via the Telegram Bot API.

Port: 9000
NATS subject: cascadia.connectors.telegram-connector.>
Auth: API key (bot token in payload)
"""

import asyncio
import json
import logging
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "telegram-connector"
VERSION = "1.0.0"
PORT = 9000
TELEGRAM_API_BASE = "https://api.telegram.org"
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
# Token loading — vault → env → config file
# ---------------------------------------------------------------------------

def _load_bot_token() -> str:
    """Load Telegram bot token. Load order: vault → TELEGRAM_BOT_TOKEN env → telegram.config.json."""
    try:
        from cascadia_sdk import vault_get  # type: ignore
        val = vault_get("telegram.bot_token")
        if val:
            return val
    except ImportError:
        pass
    val = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if val:
        return val
    cfg_path = Path(__file__).parent / "telegram.config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("bot_token", "")
    except Exception:
        return ""


_BOT_TOKEN: str = _load_bot_token()
if not _BOT_TOKEN:
    log.warning("TELEGRAM_BOT_TOKEN not set — set via vault, TELEGRAM_BOT_TOKEN env var, or telegram.config.json")


# ---------------------------------------------------------------------------
# Telegram Bot API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _bot_post(method: str, bot_token: str, body: dict) -> dict:
    """POST to the Telegram Bot API and return the parsed JSON response."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def send_message(chat_id: str | int, text: str, bot_token: str) -> dict:
    """Send a text message to a Telegram chat.

    Returns:
        dict with keys: ok, message_id, chat_id
    """
    log.info("send_message chat_id=%s", chat_id)
    result = _bot_post(
        "sendMessage",
        bot_token,
        {"chat_id": chat_id, "text": text},
    )
    msg = result.get("result", {})
    return {
        "ok": result.get("ok", False),
        "message_id": msg.get("message_id"),
        "chat_id": msg.get("chat", {}).get("id"),
        "description": result.get("description"),
    }


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")
    # Connector owns the token — loaded at startup from vault/env/config
    bot_token = _BOT_TOKEN

    if action == "send_message":
        return send_message(
            chat_id=payload["chat_id"],
            text=payload["text"],
            bot_token=bot_token,
        )
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the telegram-connector subject tree.

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
