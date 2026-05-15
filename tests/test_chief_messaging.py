"""
tests/test_chief_messaging.py
Live end-to-end messaging tests via Telegram webhook.

Requires all services running:
  Port 9000 — Telegram connector
  Port 6202 — VANGUARD
  Port 6211 — CHIEF
  Port 6200 — BEACON
  Port 5100 — CREW
  Port 8002 — RECON dashboard (for operator tests)

Run:
  python3 tests/test_chief_messaging.py

Messages are sent to chat_id 1535010257 via the live @ZyrconBot.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

WEBHOOK_URL = "http://127.0.0.1:9000/webhook"
SENT_FILE   = Path(
    "/Users/andy/Zyrcon/operators/cascadia-os-operators/telegram/data/telegram_sent.json"
)
TEST_CHAT_ID    = 1535010257
# Use time-based offset so re-runs never collide with dedup cache
_RUN_OFFSET     = int(time.time()) % 1_000_000
BASE_UPDATE_ID  = 9_000_000 + _RUN_OFFSET
BASE_MESSAGE_ID = 900_000   + _RUN_OFFSET


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_sent() -> list[dict]:
    if not SENT_FILE.exists():
        return []
    try:
        return json.loads(SENT_FILE.read_text())
    except Exception:
        return []


def _services_up() -> bool:
    for port, path in [(9000, "/api/health"), (6211, "/health"), (6202, "/health")]:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=2
            ) as r:
                if r.status != 200:
                    return False
        except Exception:
            return False
    return True


def send_and_get_reply(
    text: str,
    update_id: int,
    message_id: int,
    chat_id: int = TEST_CHAT_ID,
    timeout: int = 12,
) -> str | None:
    """
    POST a Telegram update to the webhook, then poll telegram_sent.json
    for a non-ACK reply to this chat_id that arrived after the send time.
    Returns the reply text, or None on timeout.
    """
    baseline = datetime.utcnow()

    payload = {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "Andy",
                "username": "beast_popovich",
            },
            "chat": {
                "id": chat_id,
                "first_name": "Andy",
                "username": "beast_popovich",
                "type": "private",
            },
            "date": int(time.time()),
            "text": text,
        },
    }
    try:
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(
            WEBHOOK_URL, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        print(f"    [webhook POST failed: {exc}]")
        return None

    # Poll for reply
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        for msg in reversed(_load_sent()):
            if str(msg.get("chat_id")) != str(chat_id):
                continue
            sent_at_str = msg.get("sent_at", "")
            if not sent_at_str:
                continue
            try:
                msg_time = datetime.fromisoformat(sent_at_str.replace("Z", ""))
            except ValueError:
                continue
            if msg_time <= baseline:
                continue
            reply_text = msg.get("text", "")
            if "Received. CHIEF is on it" in reply_text:
                continue    # skip ACK, keep polling for final reply
            return reply_text
    return None


def _pass(condition: bool) -> str:
    return "PASS" if condition else "FAIL"


def _preview(text: str | None, width: int = 48) -> str:
    if text is None:
        return "<no reply>"
    flat = text.replace("\n", " ")
    return flat[:width] + "…" if len(flat) > width else flat


# ── Test runner ────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    if not _services_up():
        print("❌  Required services not running. Start ports 9000, 6202, 6211 first.")
        return 0

    print(
        "\nCHIEF Live Messaging Test Suite\n"
        "Messages are sent to @ZyrconBot → chat_id 1535010257\n"
        + "─" * 78
    )
    print(
        f"{'TEST':<6} {'INPUT':<30} {'RESULT':<7} REPLY PREVIEW"
    )
    print("─" * 78)

    results: list[tuple[str, str, str, str]] = []   # (id, input, pass/fail, preview)

    def run(
        tid: str,
        text: str,
        check_fn,
        uid_offset: int,
        timeout: int = 12,
        chat_id: int = TEST_CHAT_ID,
    ) -> str | None:
        uid   = BASE_UPDATE_ID + uid_offset
        mid   = BASE_MESSAGE_ID + uid_offset
        reply = send_and_get_reply(text, uid, mid, chat_id=chat_id, timeout=timeout)
        passed = check_fn(reply)
        label  = _pass(passed)
        prev   = _preview(reply)
        results.append((tid, text, label, prev))
        print(f"{tid:<6} {text[:30]:<30} {label:<7} {prev}")
        return reply

    # ── GROUP 1: OPERATOR ROUTING ─────────────────────────────────────────────

    run("T01", "run recon",
        lambda r: r and ("scan" in r.lower() or "recon" in r.lower() or "leads" in r.lower())
                  and "registered worker" not in (r or ""),
        uid_offset=1, timeout=16)

    time.sleep(3)   # let RECON scan start settle before next query
    run("T02", "how many contacts found?",
        lambda r: r and any(c.isdigit() for c in (r or ""))
                  and ("lead" in r.lower() or "contact" in r.lower()),
        uid_offset=2, timeout=20)

    time.sleep(3)
    run("T03", "Draft a proposal for warehouse mezzanine installation",
        lambda r: r and any(w in r.lower() for w in
                            ("proposal", "quote", "mezzanine", "completed", "brief")),
        uid_offset=3, timeout=18)

    time.sleep(3)
    run("T04", "Find me HVAC contractors in Houston",
        lambda r: r and "could not find" not in r.lower()
                  and "registered worker" not in r.lower(),
        uid_offset=4, timeout=20)

    time.sleep(3)
    run("T05", "I need to find new clients for my plumbing business",
        lambda r: r and "registered worker" not in (r or ""),
        uid_offset=5, timeout=18)

    # ── GROUP 2: COMMAND FAST-PATH ────────────────────────────────────────────

    time.sleep(3)
    run("T06", "/recon",
        lambda r: r and ("scan" in r.lower() or "recon" in r.lower() or "leads" in r.lower()),
        uid_offset=6, timeout=16)

    time.sleep(3)
    run("T07", "/quote warehouse mezzanine",
        lambda r: r and any(w in r.lower() for w in ("quote", "proposal", "completed", "brief")),
        uid_offset=7, timeout=35)

    time.sleep(6)   # T07 (quote_brief + LLM) can be slow; give system a moment
    run("T08", "/status",
        lambda r: r and ("status" in r.lower() or "crew" in r.lower()
                         or "ready" in r.lower() or "cascadia" in r.lower()),
        uid_offset=8, timeout=15)

    time.sleep(4)
    run("T09", "/help",
        lambda r: r and "/recon" in r and "/quote" in r,
        uid_offset=9, timeout=20)

    time.sleep(2)
    run("T10", "/operators",
        lambda r: r and ("recon" in r.lower() or "quote" in r.lower()
                         or "operator" in r.lower()),
        uid_offset=10)

    # ── GROUP 3: CONVERSATIONAL FALLBACK ──────────────────────────────────────

    time.sleep(2)
    run("T11", "What is the weather today?",
        lambda r: r and "registered worker" not in r.lower()
                  and len(r) > 10,
        uid_offset=11)

    time.sleep(2)
    run("T12", "Can you send an invoice?",
        lambda r: r and ("invoice" in r.lower() or "roadmap" in r.lower()
                         or "available" in r.lower()),
        uid_offset=12, timeout=20)

    time.sleep(4)
    run("T13", "Hello",
        lambda r: r and len(r) > 5,
        uid_offset=13, timeout=20)

    time.sleep(2)
    run("T14", "What can you do?",
        lambda r: r and ("error" not in r.lower() or len(r) > 20),
        uid_offset=14, timeout=18)

    # ── GROUP 4: CONTEXT / MULTI-TURN ─────────────────────────────────────────

    # T15: "/recon" → "do it again"
    # Use command fast-path to guarantee dispatch + last_action set
    time.sleep(3)
    run("T15a", "/recon",
        lambda r: r and ("scan" in r.lower() or "recon" in r.lower() or "leads" in r.lower()),
        uid_offset=15, timeout=16)
    time.sleep(6)   # let last_action settle in shared file before next request
    run("T15b", "do it again",
        lambda r: r and ("scan" in r.lower() or "recon" in r.lower()
                         or "leads" in r.lower())
                  and "what" not in r.lower()[:30],
        uid_offset=16, timeout=20)

    # T16: "how many contacts found?" → "how many of those have emails?"
    time.sleep(3)
    run("T16a", "how many contacts found?",
        lambda r: r is not None,
        uid_offset=17, timeout=18)
    time.sleep(3)
    run("T16b", "how many of those have emails?",
        lambda r: r and "what contacts" not in r.lower(),
        uid_offset=18, timeout=18)

    # ── GROUP 5: EDGE CASES ───────────────────────────────────────────────────

    run("T17", " ",
        lambda r: r is not None and len(r) > 0,
        uid_offset=19)

    run("T18", "asdfghjkl random gibberish",
        lambda r: r and "registered worker" not in r.lower(),
        uid_offset=20)

    run("T19", "/unknowncommand",
        lambda r: r and ("don't know" in r.lower() or "unknown" in r.lower()
                         or "help" in r.lower()),
        uid_offset=21)

    run("T20", "x" * 520,
        lambda r: r is not None,
        uid_offset=22, timeout=15)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("─" * 78)
    passed = sum(1 for _, _, label, _ in results if label == "PASS")
    total  = len(results)
    print(f"\n{'✅' if passed == total else '⚠️ '} Final score: {passed}/{total} passed\n")

    failures = [(tid, text, prev) for tid, text, label, prev in results if label == "FAIL"]
    if failures:
        print("Failed tests:")
        for tid, text, prev in failures:
            print(f"  {tid}: input={text[:40]!r}")
            print(f"        reply={prev!r}")

    return passed


if __name__ == "__main__":
    run_all_tests()
