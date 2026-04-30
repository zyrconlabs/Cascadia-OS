#!/usr/bin/env python3
"""
Email Connector — B2
Cascadia OS DEPOT packaging

Send and receive email via SMTP and IMAP without cloud account lock-in.

Port: 9010
NATS subject: cascadia.connectors.email-connector.>
Auth: smtp_credentials (smtp_host, smtp_port, username, password per payload)
"""

import asyncio
import email as _email_lib
import imaplib
import json
import logging
import smtplib
import ssl
import threading
from email.header import decode_header as _decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "email-connector"
VERSION = "1.0.0"
PORT = 9010
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
# Helpers
# ---------------------------------------------------------------------------

def _decode_str(value: str | bytes | None) -> str:
    """Decode an RFC 2047-encoded header value to a plain string."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    parts = _decode_header(value)
    decoded_parts = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(part)
    return "".join(decoded_parts)


def _imap_connect(imap_host: str, username: str, password: str) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP4_SSL connection."""
    conn = imaplib.IMAP4_SSL(imap_host)
    conn.login(username, password)
    return conn


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def send_email(
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    use_tls: bool = True,
) -> dict:
    """Send an email via SMTP.

    Uses STARTTLS when use_tls=True (port 587 style).
    Falls back to plain connection when use_tls=False.

    Returns:
        dict with keys: ok, message (or error)
    """
    log.info("send_email to=%s subject=%r via %s:%s", to, subject, smtp_host, smtp_port)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = to
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp_host, int(smtp_port), timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(username, password)
                server.sendmail(username, [to], msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, int(smtp_port), timeout=20) as server:
                server.login(username, password)
                server.sendmail(username, [to], msg.as_string())

        return {"ok": True, "message": f"Email sent to {to}"}
    except Exception as exc:  # noqa: BLE001
        log.error("send_email failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def list_inbox(
    imap_host: str,
    username: str,
    password: str,
    folder: str = "INBOX",
    limit: int = 10,
) -> dict:
    """List the most recent messages in an IMAP folder.

    Returns:
        dict with keys: ok, messages ([{id, subject, from, date}])
    """
    log.info("list_inbox host=%s folder=%s limit=%d", imap_host, folder, limit)
    try:
        conn = _imap_connect(imap_host, username, password)
        conn.select(folder, readonly=True)
        status, data = conn.search(None, "ALL")
        if status != "OK":
            conn.logout()
            return {"ok": False, "error": f"SEARCH failed: {status}"}

        msg_ids = data[0].split()
        # Take the last `limit` messages (newest first)
        selected = msg_ids[-limit:][::-1]

        messages = []
        for mid in selected:
            fetch_status, fetch_data = conn.fetch(mid, "(BODY[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if fetch_status != "OK" or not fetch_data or fetch_data[0] is None:
                continue
            raw_headers = fetch_data[0][1] if isinstance(fetch_data[0], tuple) else b""
            parsed = _email_lib.message_from_bytes(raw_headers)
            messages.append({
                "id": mid.decode("utf-8"),
                "subject": _decode_str(parsed.get("Subject")),
                "from": _decode_str(parsed.get("From")),
                "date": _decode_str(parsed.get("Date")),
            })

        conn.logout()
        return {"ok": True, "messages": messages}
    except Exception as exc:  # noqa: BLE001
        log.error("list_inbox failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def get_message(
    imap_host: str,
    username: str,
    password: str,
    msg_id: str,
) -> dict:
    """Fetch a full message by its IMAP sequence number or UID.

    Returns:
        dict with keys: ok, id, subject, from, to, date, body (plain text)
    """
    log.info("get_message host=%s msg_id=%s", imap_host, msg_id)
    try:
        conn = _imap_connect(imap_host, username, password)
        conn.select("INBOX", readonly=True)
        fetch_status, fetch_data = conn.fetch(msg_id.encode(), "(RFC822)")
        if fetch_status != "OK" or not fetch_data or fetch_data[0] is None:
            conn.logout()
            return {"ok": False, "error": f"FETCH failed: {fetch_status}"}

        raw = fetch_data[0][1] if isinstance(fetch_data[0], tuple) else b""
        parsed = _email_lib.message_from_bytes(raw)

        # Extract plain-text body
        body_text = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body_text = payload.decode(charset, errors="replace")
                        break
        else:
            payload = parsed.get_payload(decode=True)
            if payload:
                charset = parsed.get_content_charset() or "utf-8"
                body_text = payload.decode(charset, errors="replace")

        conn.logout()
        return {
            "ok": True,
            "id": msg_id,
            "subject": _decode_str(parsed.get("Subject")),
            "from": _decode_str(parsed.get("From")),
            "to": _decode_str(parsed.get("To")),
            "date": _decode_str(parsed.get("Date")),
            "body": body_text,
        }
    except Exception as exc:  # noqa: BLE001
        log.error("get_message failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def search_messages(
    imap_host: str,
    username: str,
    password: str,
    criteria: str = "ALL",
) -> dict:
    """Search messages in INBOX using an IMAP search criterion string.

    Common criteria: ALL, UNSEEN, FROM "alice@example.com", SUBJECT "hello"

    Returns:
        dict with keys: ok, message_ids ([str])
    """
    log.info("search_messages host=%s criteria=%r", imap_host, criteria)
    try:
        conn = _imap_connect(imap_host, username, password)
        conn.select("INBOX", readonly=True)
        status, data = conn.search(None, criteria)
        if status != "OK":
            conn.logout()
            return {"ok": False, "error": f"SEARCH failed: {status}"}

        ids = [mid.decode("utf-8") for mid in data[0].split() if mid]
        conn.logout()
        return {"ok": True, "message_ids": ids, "count": len(ids)}
    except Exception as exc:  # noqa: BLE001
        log.error("search_messages failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")

    if action == "send_email":
        return send_email(
            smtp_host=payload["smtp_host"],
            smtp_port=payload.get("smtp_port", 587),
            username=payload["username"],
            password=payload["password"],
            to=payload["to"],
            subject=payload["subject"],
            body=payload["body"],
            use_tls=payload.get("use_tls", True),
        )
    elif action == "list_inbox":
        return list_inbox(
            imap_host=payload["imap_host"],
            username=payload["username"],
            password=payload["password"],
            folder=payload.get("folder", "INBOX"),
            limit=payload.get("limit", 10),
        )
    elif action == "get_message":
        return get_message(
            imap_host=payload["imap_host"],
            username=payload["username"],
            password=payload["password"],
            msg_id=payload["msg_id"],
        )
    elif action == "search_messages":
        return search_messages(
            imap_host=payload["imap_host"],
            username=payload["username"],
            password=payload["password"],
            criteria=payload.get("criteria", "ALL"),
        )
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the email-connector subject tree.

    Flow:
      1. Parse JSON from raw bytes.
      2. If the action requires approval, publish to cascadia.approvals.request
         and return {"ok": True, "status": "pending_approval"} — do NOT execute.
      3. Otherwise call execute_call and publish result to the response subject.
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
        pending = {"ok": True, "status": "pending_approval"}
        await nc.publish(
            RESPONSE_SUBJECT,
            json.dumps({"connector": NAME, "action": action, "result": pending}).encode("utf-8"),
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
# Health HTTP server
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"status": "ok", "connector": NAME}).encode("utf-8")
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
    log.info("Health server listening on port %d at /health", PORT)
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
