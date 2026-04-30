#!/usr/bin/env python3
"""
Google Accounts Connector — CON-020
Cascadia OS DEPOT packaging

OAuth2 identity and account access for Google services.
Handles the full OAuth2 flow and token lifecycle; downstream connectors
(Gmail, Calendar, Drive) obtain access tokens by calling get_user_info
or refresh_access_token rather than managing credentials themselves.

Port: 9020
NATS subject: cascadia.connectors.google-connector.>
Auth: OAuth2 (client_id + client_secret via env vars)

Environment variables required:
  GOOGLE_CLIENT_ID      — OAuth2 client ID from Google Cloud Console
  GOOGLE_CLIENT_SECRET  — OAuth2 client secret

Optional:
  GOOGLE_TOKEN_FILE     — path to persist tokens (default: ~/.cascadia/google_tokens.json)
  GOOGLE_REDIRECT_URI   — OAuth2 redirect URI (default: http://localhost:9020/oauth2/callback)
  NATS_URL              — NATS server URL (default: nats://localhost:4222)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "google-connector"
VERSION = "1.0.0"
PORT = 9020

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

DEFAULT_SCOPES = ["openid", "email", "profile"]
DEFAULT_REDIRECT_URI = f"http://localhost:{PORT}/oauth2/callback"
DEFAULT_TOKEN_FILE = os.path.expanduser("~/.cascadia/google_tokens.json")

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
ACTIONS_REQUIRING_APPROVAL = {"revoke_token"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)

# Shared pending-callback state: maps state → asyncio.Future resolved on redirect
_pending_callbacks: dict[str, asyncio.Future] = {}
_pending_callbacks_lock = threading.Lock()
_event_loop: asyncio.AbstractEventLoop | None = None


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _client_id() -> str:
    val = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not val:
        raise EnvironmentError("GOOGLE_CLIENT_ID is not set")
    return val


def _client_secret() -> str:
    val = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not val:
        raise EnvironmentError("GOOGLE_CLIENT_SECRET is not set")
    return val


def _token_file() -> Path:
    return Path(os.environ.get("GOOGLE_TOKEN_FILE", DEFAULT_TOKEN_FILE))


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI", DEFAULT_REDIRECT_URI)


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

def _load_tokens() -> dict:
    path = _token_file()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_tokens(tokens: dict) -> None:
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2))


# ---------------------------------------------------------------------------
# Google API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _google_post(url: str, data: dict) -> dict:
    """POST application/x-www-form-urlencoded to a Google token endpoint."""
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _google_get(url: str, access_token: str, params: dict | None = None) -> dict:
    """GET from a Google API endpoint with a Bearer token."""
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        full_url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# OAuth2 business logic
# ---------------------------------------------------------------------------

def get_auth_url(
    scopes: list[str] | None = None,
    state: str = "",
    redirect_uri: str | None = None,
    access_type: str = "offline",
    prompt: str = "consent",
) -> dict:
    """Build a Google OAuth2 authorization URL.

    Returns:
        dict with keys: ok, url
    """
    try:
        params = {
            "client_id": _client_id(),
            "redirect_uri": redirect_uri or _redirect_uri(),
            "response_type": "code",
            "scope": " ".join(scopes or DEFAULT_SCOPES),
            "access_type": access_type,
            "prompt": prompt,
        }
        if state:
            params["state"] = state
        url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
        log.info("get_auth_url scopes=%s", scopes)
        return {"ok": True, "url": url}
    except EnvironmentError as exc:
        return {"ok": False, "error": str(exc)}


def exchange_code(code: str, redirect_uri: str | None = None) -> dict:
    """Exchange an authorization code for access and refresh tokens.

    Persists the token response to the token file.

    Returns:
        dict with keys: ok, access_token, refresh_token, expires_in, scope
    """
    log.info("exchange_code")
    try:
        data = {
            "code": code,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": redirect_uri or _redirect_uri(),
            "grant_type": "authorization_code",
        }
        tokens = _google_post(GOOGLE_TOKEN_URL, data)
        _save_tokens(tokens)
        return {
            "ok": True,
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "expires_in": tokens.get("expires_in"),
            "scope": tokens.get("scope"),
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def refresh_access_token(refresh_tok: str | None = None) -> dict:
    """Use a refresh token to obtain a new access token.

    If refresh_tok is not provided, uses the token persisted by exchange_code.

    Returns:
        dict with keys: ok, access_token, expires_in
    """
    log.info("refresh_access_token")
    try:
        tok = refresh_tok
        if not tok:
            stored = _load_tokens()
            tok = stored.get("refresh_token")
        if not tok:
            return {"ok": False, "error": "no refresh_token available"}
        data = {
            "refresh_token": tok,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "refresh_token",
        }
        tokens = _google_post(GOOGLE_TOKEN_URL, data)
        # Merge into stored tokens (refresh_token is not returned on refresh)
        stored = _load_tokens()
        stored["access_token"] = tokens["access_token"]
        stored["expires_in"] = tokens.get("expires_in")
        _save_tokens(stored)
        return {
            "ok": True,
            "access_token": tokens["access_token"],
            "expires_in": tokens.get("expires_in"),
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def get_user_info(access_token: str | None = None) -> dict:
    """Fetch the Google account profile for the authenticated user.

    If access_token is not provided, uses the persisted access token.

    Returns:
        dict with keys: ok, sub, email, name, picture, email_verified
    """
    log.info("get_user_info")
    try:
        tok = access_token
        if not tok:
            stored = _load_tokens()
            tok = stored.get("access_token")
        if not tok:
            return {"ok": False, "error": "no access_token available"}
        profile = _google_get(GOOGLE_USERINFO_URL, tok)
        return {
            "ok": True,
            "sub": profile.get("sub"),
            "email": profile.get("email"),
            "name": profile.get("name"),
            "picture": profile.get("picture"),
            "email_verified": profile.get("email_verified", False),
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def revoke_token(token: str | None = None) -> dict:
    """Revoke an OAuth2 token (access or refresh), clearing stored credentials.

    This action requires human approval before execution.

    Returns:
        dict with keys: ok
    """
    log.info("revoke_token")
    try:
        tok = token
        if not tok:
            stored = _load_tokens()
            tok = stored.get("access_token") or stored.get("refresh_token")
        if not tok:
            return {"ok": False, "error": "no token available to revoke"}
        encoded = urllib.parse.urlencode({"token": tok}).encode("utf-8")
        req = urllib.request.Request(
            GOOGLE_REVOKE_URL,
            data=encoded,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
        # Clear stored tokens after successful revocation
        path = _token_file()
        if path.exists():
            path.unlink()
        return {"ok": True}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")

    if action == "get_auth_url":
        return get_auth_url(
            scopes=payload.get("scopes"),
            state=payload.get("state", ""),
            redirect_uri=payload.get("redirect_uri"),
            access_type=payload.get("access_type", "offline"),
            prompt=payload.get("prompt", "consent"),
        )
    elif action == "exchange_code":
        return exchange_code(
            code=payload["code"],
            redirect_uri=payload.get("redirect_uri"),
        )
    elif action == "refresh_access_token":
        return refresh_access_token(
            refresh_tok=payload.get("refresh_token"),
        )
    elif action == "get_user_info":
        return get_user_info(
            access_token=payload.get("access_token"),
        )
    elif action == "revoke_token":
        return revoke_token(
            token=payload.get("token"),
        )
    else:
        return {"ok": False, "error": f"unknown action: {action!r}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the google-connector subject tree.

    revoke_token is the only action that requires human approval — it
    destroys the stored credentials, which cannot be undone without
    re-authenticating. All other actions are read or internal token ops.
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
# HTTP server — health + OAuth2 callback
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send_health()
        elif self.path.startswith("/oauth2/callback"):
            self._handle_callback()
        else:
            self._json(404, {"error": "not found"})

    def _send_health(self) -> None:
        body = json.dumps(
            {
                "status": "healthy",
                "connector": NAME,
                "version": VERSION,
                "port": PORT,
                "tokens_stored": _token_file().exists(),
            }
        ).encode("utf-8")
        self._raw(200, "application/json", body)

    def _handle_callback(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        code = params.get("code", "")
        state = params.get("state", "")
        error = params.get("error", "")

        if error:
            log.warning("OAuth2 callback error: %s", error)
            self._raw(400, "text/html", f"<p>OAuth2 error: {error}</p>".encode())
            return

        if not code:
            self._raw(400, "text/html", b"<p>Missing authorization code.</p>")
            return

        result = exchange_code(code)
        if result.get("ok"):
            log.info("OAuth2 exchange succeeded via callback (state=%s)", state)
            # Notify any waiting NATS subscriber
            with _pending_callbacks_lock:
                fut = _pending_callbacks.pop(state, None)
            if fut and _event_loop and not fut.done():
                _event_loop.call_soon_threadsafe(fut.set_result, result)
            self._raw(
                200,
                "text/html",
                b"<html><body><h2>Google account connected.</h2>"
                b"<p>You can close this window.</p></body></html>",
            )
        else:
            log.error("OAuth2 exchange failed: %s", result.get("error"))
            self._raw(
                500,
                "text/html",
                f"<p>Token exchange failed: {result.get('error')}</p>".encode(),
            )

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self._raw(code, "application/json", body)

    def _raw(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # suppress access log noise
        pass


# Keep the old alias so tests that import _HealthHandler still work
_HealthHandler = _Handler


def _start_http_server() -> threading.Thread:
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("HTTP server (health + OAuth2 callback) listening on port %d", PORT)
    return thread


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_main() -> None:
    global _event_loop
    _event_loop = asyncio.get_running_loop()

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
    _start_http_server()
    asyncio.run(_nats_main())


if __name__ == "__main__":
    main()
