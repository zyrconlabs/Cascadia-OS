"""
BELL — Cascadia OS inbound chat interface (mobile block-message facet).

Serves ONLY the mobile chat contract the iOS client already speaks:

  POST /api/bell/chat
       body: {session_id, message, operator_context}
       -> {message_id, session_id, status}                (immediate)
  GET  /api/chat/stream/{session}[?message_id=…]
       -> {message_id, content, status, block_message}    (poll)
  GET  /health                                            (ServiceRuntime built-in)

It bridges to CHIEF's session-based chat (/session/start -> /message) and runs
the reply through chat_blocks.format_chief_reply_as_blocks() to produce the
additive `block_message` envelope. All state is in-memory.

NOTE: /api/chat (synchronous classification used by EMAIL + RECON) is
INTENTIONALLY NOT implemented here. Standing up that path activates EMAIL's
dormant LLM lead-creation (fallback confidence 0.5 currently sits below its 0.7
threshold) and is a deliberate, separate rollout — not a side effect of this
task. Until it exists, EMAIL/RECON keep getting a 404 on /api/chat, which their
urllib callers catch exactly as they catch connection-refused today.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime
from cascadia.dashboard.chat_blocks import format_chief_reply_as_blocks

CHIEF_URL = os.environ.get("CHIEF_URL", "http://127.0.0.1:6211")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BellService:
    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config["components"] if c["name"] == name)
        self.runtime = ServiceRuntime(
            name=name,
            port=component["port"],
            pulse_file=component["pulse_file"],
            log_dir=self.config["log_dir"],
        )
        # mobile session_id -> CHIEF bell_<uuid> session_id
        self._session_map: Dict[str, str] = {}
        # mobile session_id -> {message_id, content, status, block_message}
        # Keyed by session (not message_id) because ServiceRuntime drops the
        # query string before dispatch, so the client's ?message_id= is not
        # visible to the handler. One in-flight message per session at a time.
        self._results: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        self.runtime.register_route("POST", "/api/bell/chat", self.bell_chat)
        self.runtime.register_route("GET", "/api/chat/stream/{session}", self.chat_stream)
        self.runtime.register_route("POST", "/api/bell/approve", self.bell_approve)

    # --- CHIEF bridge -------------------------------------------------------

    def _http_post_json(self, url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _run_chief_message(self, mobile_session_id: str, content: str) -> None:
        try:
            with self._lock:
                chief_session: Optional[str] = self._session_map.get(mobile_session_id)
            if chief_session is None:
                start = self._http_post_json(f"{CHIEF_URL}/session/start", {}, 10)
                chief_session = start.get("session_id")
                if not chief_session:
                    raise RuntimeError("CHIEF /session/start returned no session_id")
                with self._lock:
                    self._session_map[mobile_session_id] = chief_session

            chief_response = self._http_post_json(
                f"{CHIEF_URL}/message",
                {"session_id": chief_session, "content": content},
                60,
            )
            # CHIEF /message returns run_state / current_step; chat_blocks reads
            # state / step. Normalize (and prefer assistant_message, fall back to
            # draft_preview) before formatting.
            normalized = {
                "run_id": chief_response.get("run_id"),
                "pending_approval_id": chief_response.get("pending_approval_id"),
                "assistant_message": chief_response.get("assistant_message")
                or chief_response.get("draft_preview", ""),
                "state": chief_response.get("run_state"),
                "step": chief_response.get("current_step"),
            }
            block_message = format_chief_reply_as_blocks(normalized)
            self._store(mobile_session_id, normalized["assistant_message"], "complete", block_message)
        except Exception as exc:
            self.runtime.logger.error("BELL /message bridge failed: %s", exc)
            self._store(mobile_session_id, "", "error", None)

    def _store(self, session_id: str, content: str, status: str,
               block_message: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            prev = self._results.get(session_id, {})
            self._results[session_id] = {
                "message_id": prev.get("message_id", ""),
                "content": content,
                "status": status,
                "block_message": block_message,
            }

    # --- auth gate ----------------------------------------------------------

    def _authenticate(self, payload: Dict[str, Any]) -> Optional[tuple[int, Dict[str, Any]]]:
        """Require a valid paired-device bearer token.

        Reads `Authorization: Bearer <token>` from the request headers
        (ServiceRuntime injects them as payload['__headers__'] on POST),
        validates the token against the paired_devices table with a
        constant-time comparison, and refreshes last_seen on success.

        Returns None when the caller is authenticated (proceed), or a
        (status, body) 401 tuple the route must return immediately.
        """
        headers = payload.get("__headers__") or {}
        auth = ""
        for key, value in headers.items():
            if key.lower() == "authorization":
                auth = value or ""
                break
        if not auth.startswith("Bearer "):
            return 401, {"error": "authentication required"}
        token = auth[len("Bearer "):].strip()
        if not token:
            return 401, {"error": "authentication required"}
        if not self._token_is_valid(token):
            return 401, {"error": "invalid or expired token"}
        return None

    def _token_is_valid(self, token: str) -> bool:
        """Constant-time check of `token` against paired_devices; touch last_seen."""
        db_path = str(Path(self.config.get("database_path", "./data/runtime/cascadia.db")))
        try:
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT token FROM paired_devices").fetchall()
                match: Optional[str] = None
                for (stored,) in rows:
                    # Compare EVERY row (no early break) so timing does not leak
                    # which row matched; each compare is itself constant-time.
                    if stored and hmac.compare_digest(str(stored), token):
                        match = stored
                if match is None:
                    return False
                conn.execute(
                    "UPDATE paired_devices SET last_seen=? WHERE token=?", (_now(), match)
                )
                return True
        except sqlite3.OperationalError:
            # paired_devices does not exist yet (no device has ever paired) —
            # treat as "no valid tokens" and reject.
            return False

    # --- routes -------------------------------------------------------------

    def bell_chat(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        denied = self._authenticate(payload)
        if denied is not None:
            return denied
        session_id = payload.get("session_id") or ""
        message = payload.get("message", "")
        message_id = uuid.uuid4().hex
        with self._lock:
            self._results[session_id] = {
                "message_id": message_id,
                "content": "",
                "status": "pending",
                "block_message": None,
            }
        threading.Thread(
            target=self._run_chief_message, args=(session_id, message), daemon=True
        ).start()
        return 200, {"message_id": message_id, "session_id": session_id, "status": "pending"}

    def chat_stream(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        session = payload.get("session", "")
        with self._lock:
            result = self._results.get(session)
        if result is None:
            return 200, {"message_id": "", "content": "", "status": "pending", "block_message": None}
        return 200, {
            "message_id": result["message_id"],
            "content": result["content"],
            "status": result["status"],
            "block_message": result["block_message"],
        }

    def bell_approve(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Proxy an approve/reject decision to CHIEF /approve.

        Body: {mobile_session_id, run_id, approval_id, decision}
              (decision: "approved" | "denied")
        Resolves the CHIEF session via the same _session_map /api/bell/chat
        populated, coerces approval_id to int (CHIEF's record_decision requires
        int), and passes CHIEF's response back to the client unmodified.
        """
        denied = self._authenticate(payload)
        if denied is not None:
            return denied
        mobile_session_id = payload.get("mobile_session_id") or ""
        run_id = payload.get("run_id") or ""
        approval_id = payload.get("approval_id")
        decision = payload.get("decision", "")

        with self._lock:
            chief_session = self._session_map.get(mobile_session_id)
        if chief_session is None:
            return 404, {"error": "no CHIEF session for this mobile session"}
        if approval_id is None:
            return 400, {"error": "approval_id required"}
        try:
            approval_id_int = int(approval_id)
        except (TypeError, ValueError):
            return 400, {"error": "approval_id must be an integer"}

        try:
            chief_response = self._http_post_json(
                f"{CHIEF_URL}/approve",
                {
                    "session_id": chief_session,
                    "run_id": run_id,
                    "approval_id": approval_id_int,
                    "decision": decision,
                },
                60,
            )
        except urllib.error.HTTPError as e:
            # CHIEF rejected the request (e.g. 400 bad decision). Surface its
            # status + body rather than masking it as a generic failure.
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {"error": f"CHIEF returned HTTP {e.code}"}
            return e.code, body
        except Exception as exc:
            self.runtime.logger.error("BELL /approve bridge failed: %s", exc)
            return 502, {"error": f"CHIEF approve failed: {exc}"}

        return 200, chief_response

    def start(self) -> None:
        self.runtime.logger.info("BELL inbound chat interface active (mobile facet)")
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description="BELL - Cascadia OS inbound chat interface")
    p.add_argument("--config", required=True)
    p.add_argument("--name", required=True)
    a = p.parse_args()
    BellService(a.config, a.name).start()


if __name__ == "__main__":
    main()
