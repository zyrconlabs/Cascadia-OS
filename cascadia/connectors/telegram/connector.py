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
VANGUARD_URL = "http://127.0.0.1:6202"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
ACTIONS_REQUIRING_APPROVAL = {"send_message"}
POLL_INTERVAL = 3
PRISM_URL = "http://127.0.0.1:6300"
OWNER_CHAT_ID_KEY = "telegram:owner_chat_id"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


# ---------------------------------------------------------------------------
# Token loading — vault → env → config file
# ---------------------------------------------------------------------------

def _load_bot_token() -> str:
    """Load Telegram bot token. Load order: vault (SDK) → vault (direct) → env → config file."""
    try:
        from cascadia_sdk import vault_get  # type: ignore
        val = vault_get("telegram:bot_token", namespace="secrets")
        if val:
            return val
    except ImportError:
        pass
    # Direct VaultStore read — works when VAULT_ENCRYPTION_KEY env var is shared across processes.
    try:
        from cascadia.memory.vault import VaultStore  # type: ignore
        _db = Path(__file__).parent.parent.parent.parent / "data" / "runtime" / "cascadia_vault.db"
        val = VaultStore(str(_db)).read("telegram:bot_token", namespace="secrets")
        if val:
            return val
    except Exception:
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
# Inbound polling — getUpdates every POLL_INTERVAL seconds → VANGUARD
# ---------------------------------------------------------------------------

_poll_stop = threading.Event()


def _forward_to_vanguard(chat_id: int, text: str, update_id: int) -> None:
    payload = json.dumps({
        "channel": "telegram",
        "sender": str(chat_id),
        "content": text,
        "chat_id": chat_id,
        "metadata": {"update_id": update_id, "chat_id": chat_id},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{VANGUARD_URL}/inbound",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            log.info("Forwarded to VANGUARD chat_id=%s update_id=%s message_id=%s",
                     chat_id, update_id, result.get("message_id"))
    except Exception as exc:
        log.warning("Forward to VANGUARD failed: %s", exc)


def _vault_store():
    """Return a VaultStore instance for the runtime vault DB."""
    from cascadia.memory.vault import VaultStore  # type: ignore
    _db = Path(__file__).parent.parent.parent.parent / "data" / "runtime" / "cascadia_vault.db"
    return VaultStore(str(_db))


def _save_owner_chat_id(chat_id: int) -> None:
    """Persist chat_id to vault + PRISM config on first /start. No-op if already set."""
    try:
        store = _vault_store()
        if store.read(OWNER_CHAT_ID_KEY, namespace="secrets"):
            log.info("/start received but owner_chat_id already configured — skipping")
            return
        store.write(OWNER_CHAT_ID_KEY, str(chat_id), created_by="telegram-connector", namespace="secrets")
        log.info("Saved owner_chat_id=%s to vault", chat_id)
    except Exception as exc:
        log.warning("Failed to write owner_chat_id to vault: %s", exc)
        return

    # Mirror to PRISM config so the settings panel shows the value.
    try:
        body = json.dumps({
            "target_type": "connector",
            "target_id": "telegram",
            "changes": {"owner_chat_id": str(chat_id)},
            "confirmed": True,
            "source": "telegram-connector:/start",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{PRISM_URL}/api/config/connector/telegram/save",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        log.info("Saved owner_chat_id=%s to PRISM config", chat_id)
    except Exception as exc:
        log.warning("Failed to mirror owner_chat_id to PRISM: %s", exc)

    # Confirm to the user on Telegram.
    if _BOT_TOKEN:
        try:
            send_message(
                chat_id,
                "✅ Chief is connected. Your chat ID has been saved.\n"
                "You can now send tasks and receive approvals here.",
                _BOT_TOKEN,
            )
        except Exception as exc:
            log.warning("Failed to send /start confirmation: %s", exc)


def _poll_updates() -> None:
    """Background thread: poll getUpdates, forward each message to VANGUARD."""
    if not _BOT_TOKEN:
        log.warning("Polling disabled — no bot token")
        return

    offset = 0
    log.info("Telegram update polling started (interval=%ds)", POLL_INTERVAL)

    while not _poll_stop.is_set():
        try:
            params = json.dumps({"offset": offset, "timeout": 2}).encode("utf-8")
            req = urllib.request.Request(
                f"{TELEGRAM_API_BASE}/bot{_BOT_TOKEN}/getUpdates",
                data=params,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if not data.get("ok"):
                log.warning("getUpdates error: %s", data)
                _poll_stop.wait(POLL_INTERVAL)
                continue

            for update in data.get("result", []):
                update_id = update.get("update_id", 0)
                offset = update_id + 1

                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id")
                if not chat_id or not text:
                    continue

                log.info("Received message chat_id=%s update_id=%s", chat_id, update_id)
                if text.strip() == "/start":
                    _save_owner_chat_id(chat_id)
                    continue  # /start is handled locally — not forwarded to VANGUARD
                _forward_to_vanguard(chat_id, text, update_id)

        except Exception as exc:
            log.warning("Poll cycle error: %s", exc)

        _poll_stop.wait(POLL_INTERVAL)

    log.info("Telegram update polling stopped")


def _start_polling() -> threading.Thread:
    thread = threading.Thread(target=_poll_updates, daemon=True, name="telegram-poller")
    thread.start()
    return thread


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
        self._respond(200, body)

    def do_POST(self):  # noqa: N802
        if self.path != "/send":
            self._respond(404, json.dumps({"error": "not found"}).encode("utf-8"))
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._respond(400, json.dumps({"success": False, "error": str(exc)}).encode("utf-8"))
            return

        chat_id = payload.get("chat_id")
        text = payload.get("text", "")
        if not chat_id or not text:
            self._respond(400, json.dumps({"success": False, "error": "chat_id and text required"}).encode("utf-8"))
            return

        if not _BOT_TOKEN:
            self._respond(503, json.dumps({"success": False, "error": "bot token not configured"}).encode("utf-8"))
            return

        try:
            result = send_message(chat_id, text, _BOT_TOKEN)
            if result.get("ok"):
                body = json.dumps({"success": True, "chat_id": chat_id}).encode("utf-8")
                self._respond(200, body)
            else:
                err = result.get("description", "sendMessage failed")
                body = json.dumps({"success": False, "error": err}).encode("utf-8")
                self._respond(502, body)
        except Exception as exc:
            body = json.dumps({"success": False, "error": str(exc)}).encode("utf-8")
            self._respond(500, body)

    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
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
    _start_polling()
    asyncio.run(_nats_main())


if __name__ == "__main__":
    main()
