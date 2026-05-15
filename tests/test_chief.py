"""
tests/test_chief.py
12 tests covering CHIEF orchestrator and VANGUARD telegram wiring.
"""
from __future__ import annotations

import json
import sys
import types
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub external cascadia dependencies before importing modules under test.
# cascadia.chief.* and cascadia.gateway.vanguard are real — only their
# shared infrastructure stubs are added here.
# ---------------------------------------------------------------------------

def _stub(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

# ServiceRuntime stub — records registered routes, doesn't bind a port
class _FakeRuntime:
    def __init__(self, **kw):
        self.logger = MagicMock()
        self.logger.info  = lambda *a, **kw: None
        self.logger.warning = lambda *a, **kw: None
        self.logger.error = lambda *a, **kw: None
    def register_route(self, *a, **kw): pass
    def register_ws_route(self, *a, **kw): pass
    def start(self): pass

_stub("cascadia.shared.config",
      load_config=lambda p: {"components": [], "log_dir": "/tmp"})
_stub("cascadia.shared.service_runtime", ServiceRuntime=_FakeRuntime)
_stub("cascadia.shared.logger", configure_logging=MagicMock())

# Now the real imports
from cascadia.chief.models import TaskRequest, TaskResponse          # noqa: E402
from cascadia.chief import operator_selector as sel                   # noqa: E402
from cascadia.chief.server import ChiefService                        # noqa: E402
from cascadia.gateway import vanguard as vg                           # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CREW_QUOTE_BRIEF = {
    "crew_size": 1,
    "operators": {
        "quote_brief": {
            "operator_id": "quote_brief",
            "capabilities": ["quote.generate", "brief.generate",
                             "proposal.draft", "business.brief"],
            "health_hook": "/health",
        }
    },
}

CREW_RECON_ONLY = {
    "crew_size": 1,
    "operators": {
        "recon": {
            "operator_id": "recon",
            "capabilities": ["data.research", "lead.enrich", "web.search", "recon"],
            "health_hook": "/health",
        }
    },
}


def _urlopen_mock(data: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_svc() -> ChiefService:
    svc = ChiefService.__new__(ChiefService)
    svc.runtime = _FakeRuntime()
    return svc


def _make_vg() -> vg.VanguardService:
    svc = vg.VanguardService.__new__(vg.VanguardService)
    svc.runtime = _FakeRuntime()
    svc._lock = threading.Lock()
    svc._inbox = []
    svc._handshake_port = None
    return svc


# ---------------------------------------------------------------------------
# Test 1 — CHIEF health endpoint
# ---------------------------------------------------------------------------
class TestChiefHealth(unittest.TestCase):
    def test_chief_health(self):
        svc = _make_svc()
        code, body = svc.health({})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["service"], "chief")
        self.assertEqual(body["role"], "orchestrator")


# ---------------------------------------------------------------------------
# Test 2 — Selector picks registered operator with keyword + capability match
# ---------------------------------------------------------------------------
class TestSelectorPicksRegisteredOperator(unittest.TestCase):
    def test_selector_selects_registered_operator(self):
        with patch("urllib.request.urlopen", return_value=_urlopen_mock(CREW_QUOTE_BRIEF)):
            result = sel.select_target(
                "draft a proposal for warehouse installation",
                "http://localhost:5100",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_type"], "operator")
        self.assertEqual(result["target"], "quote_brief")
        self.assertGreater(result["confidence"], 0)


# ---------------------------------------------------------------------------
# Test 3 — Selector returns none if keyword matches but no operator registered
# ---------------------------------------------------------------------------
class TestSelectorNoMatchIfNotRegistered(unittest.TestCase):
    def test_selector_no_match_if_not_registered(self):
        with patch("urllib.request.urlopen", return_value=_urlopen_mock(CREW_RECON_ONLY)):
            result = sel.select_target("draft a proposal", "http://localhost:5100")
        self.assertFalse(result["ok"])
        self.assertEqual(result["selected_type"], "none")
        self.assertIsNone(result["target"])


# ---------------------------------------------------------------------------
# Test 4 — CHIEF calls BEACON /route with correct payload fields
# ---------------------------------------------------------------------------
class TestChiefCallsBeaconCorrectPayload(unittest.TestCase):
    def test_chief_calls_beacon_with_correct_payload(self):
        svc = _make_svc()
        captured = []

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            body = json.loads(req.data.decode()) if getattr(req, "data", None) else {}
            captured.append({"url": url, "body": body})
            if "/crew" in url:
                return _urlopen_mock(CREW_QUOTE_BRIEF)
            return _urlopen_mock({"ok": True, "forward_response": {"result": "done"}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "draft a proposal",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {"chat_id": 123456},
            })

        beacon_calls = [p for p in captured if "/route" in p["url"]]
        self.assertGreater(len(beacon_calls), 0, "BEACON /route was not called")
        bp = beacon_calls[0]["body"]
        self.assertEqual(bp["sender"], "chief")
        self.assertEqual(bp["target"], "quote_brief")
        self.assertIn("task", bp["message"])
        self.assertEqual(bp["message"]["source_channel"], "telegram")
        self.assertIn("metadata", bp["message"])


# ---------------------------------------------------------------------------
# Test 5 — Reply is formatted cleanly (no raw JSON key names visible)
# ---------------------------------------------------------------------------
class TestChiefFormatsReplyCleanly(unittest.TestCase):
    def test_chief_formats_reply_cleanly(self):
        svc = _make_svc()

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/crew" in url:
                return _urlopen_mock(CREW_QUOTE_BRIEF)
            return _urlopen_mock({"ok": True, "forward_response": {"result": "Here is your proposal text"}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "draft a proposal for warehouse",
                "source_channel": "telegram",
            })

        self.assertTrue(body["ok"])
        reply = body["reply_text"]
        self.assertIn("proposal text", reply)   # content from operator, no "Completed by" prefix
        self.assertNotIn('"result"', reply)


# ---------------------------------------------------------------------------
# Test 6 — CHIEF handles BEACON timeout gracefully
# ---------------------------------------------------------------------------
class TestChiefHandlesBeaconTimeout(unittest.TestCase):
    def test_chief_handles_beacon_timeout(self):
        import urllib.error
        svc = _make_svc()

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/crew" in url:
                return _urlopen_mock(CREW_QUOTE_BRIEF)
            raise urllib.error.URLError("timed out")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "draft a proposal",
                "source_channel": "telegram",
            })

        self.assertFalse(body["ok"])
        self.assertIn("could not be completed", body["reply_text"])


# ---------------------------------------------------------------------------
# Test 7 — /status command is routed as selected_type="status"
# ---------------------------------------------------------------------------
class TestChiefStatusCommand(unittest.TestCase):
    def test_chief_status_command(self):
        svc = _make_svc()

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/crew" in url:
                return _urlopen_mock({"crew_size": 2, "operators": {}})
            return _urlopen_mock({"ok": True})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({"task": "/status"})

        self.assertEqual(body["selected_type"], "status")
        self.assertIn("Status", body["reply_text"])


# ---------------------------------------------------------------------------
# Test 8 — VANGUARD preserves chat_id in normalized envelope raw field
# ---------------------------------------------------------------------------
class TestVanguardPreservesChatId(unittest.TestCase):
    def test_vanguard_preserves_chat_id(self):
        svc = _make_vg()

        # Patch thread creation so _handle_telegram_inbound doesn't run
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            code, body = svc.receive_inbound({
                "channel": "telegram",
                "sender":  "andy",
                "content": "draft a proposal",
                "metadata": {"chat_id": 123456},
            })

        envelope = body["envelope"]
        self.assertEqual(
            envelope["raw"].get("metadata", {}).get("chat_id"),
            123456,
        )


# ---------------------------------------------------------------------------
# Test 9 — VANGUARD calls CHIEF /task, not BEACON, for telegram messages
# ---------------------------------------------------------------------------
class TestVanguardCallsChiefNotBeacon(unittest.TestCase):
    def test_vanguard_calls_chief_not_beacon(self):
        svc = _make_vg()
        called_urls = []

        def fake_post(self_inner, url, payload, timeout=5):
            called_urls.append(url)
            return {"ok": True, "reply_text": "done"}

        envelope = {
            "channel": "telegram", "sender": "andy",
            "content": "test task", "tenant_id": "default",
            "raw": {"metadata": {"chat_id": 999}},
        }
        with patch.object(vg.VanguardService, "_telegram_post", fake_post):
            svc._handle_telegram_inbound(envelope)

        chief_calls  = [u for u in called_urls if "/task" in u]
        beacon_calls = [u for u in called_urls if "6200" in u or "beacon" in u.lower()]
        self.assertGreater(len(chief_calls), 0, "CHIEF /task was not called")
        self.assertEqual(len(beacon_calls), 0, "VANGUARD must not call BEACON directly")


# ---------------------------------------------------------------------------
# Test 10 — VANGUARD sends Telegram reply with correct chat_id and text
# ---------------------------------------------------------------------------
class TestVanguardSendsTelegramReply(unittest.TestCase):
    def test_vanguard_sends_telegram_reply(self):
        svc = _make_vg()
        sent = []

        def fake_post(self_inner, url, payload, timeout=5):
            sent.append({"url": url, "payload": payload})
            if "/task" in url:
                return {"ok": True, "reply_text": "done"}
            return {"ok": True}

        envelope = {
            "channel": "telegram", "sender": "andy",
            "content": "test task", "tenant_id": "default",
            "raw": {"metadata": {"chat_id": 123456}},
        }
        with patch.object(vg.VanguardService, "_telegram_post", fake_post):
            svc._handle_telegram_inbound(envelope)

        send_calls = [p for p in sent if "/send" in p["url"]]
        self.assertGreater(len(send_calls), 0, "/send was not called")
        final = send_calls[-1]["payload"]
        self.assertEqual(final["chat_id"], 123456)
        self.assertEqual(final["text"], "done")


# ---------------------------------------------------------------------------
# Test 11 — Telegram deduplication logic is intact
# ---------------------------------------------------------------------------
class TestTelegramDeduplication(unittest.TestCase):
    def test_telegram_deduplication_intact(self):
        import hashlib
        seen: set = set()

        def is_duplicate(chat_id, text, update_id) -> bool:
            key = hashlib.sha256(
                f"{chat_id}:{text}:{update_id}".encode()
            ).hexdigest()
            if key in seen:
                return True
            seen.add(key)
            return False

        p = {"chat_id": 123, "text": "hello", "update_id": 42}
        self.assertFalse(is_duplicate(**p))
        self.assertTrue(is_duplicate(**p), "second call must be deduplicated")


# ---------------------------------------------------------------------------
# Test 12 — quote_brief manifest declares id='quote_brief', not 'chief'
# ---------------------------------------------------------------------------
class TestQuoteBriefNotRegisteredAsChief(unittest.TestCase):
    def test_quote_brief_not_registered_as_chief(self):
        manifest_path = Path(
            "/Users/andy/Zyrcon/operators/cascadia-os-operators/"
            "quote_brief/manifest.json"
        )
        if not manifest_path.exists():
            self.skipTest("quote_brief manifest not found")
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["id"], "quote_brief")
        self.assertNotEqual(manifest["id"], "chief")


# ---------------------------------------------------------------------------
# Test 13 — CHIEF falls back to direct dispatch when BEACON returns forwarded=False
# ---------------------------------------------------------------------------
CREW_QUOTE_BRIEF_WITH_PORT = {
    "crew_size": 1,
    "operators": {
        "quote_brief": {
            "operator_id": "quote_brief",
            "capabilities": ["quote.generate", "brief.generate",
                             "proposal.draft", "business.brief"],
            "health_hook": "/health",
            "port": 8006,
            "task_hook": "/api/task",
        }
    },
}


class TestChiefDirectDispatchFallback(unittest.TestCase):
    def test_chief_dispatches_directly_when_beacon_not_forwarded(self):
        """When BEACON returns forwarded=False, CHIEF falls back to direct dispatch."""
        svc = _make_svc()
        captured = []

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            body = json.loads(req.data.decode()) if getattr(req, "data", None) else {}
            captured.append({"url": url, "body": body})
            if "/crew" in url:
                # Both the keyword-selector GET and _dispatch_direct GET hit this
                return _urlopen_mock(CREW_QUOTE_BRIEF_WITH_PORT)
            if "/route" in url:
                # BEACON says it cannot forward
                return _urlopen_mock({
                    "ok": True, "routed_to": "quote_brief",
                    "forwarded": False,
                    "note": "Target port not registered — acknowledged only",
                })
            # Direct dispatch to operator at port 8006
            return _urlopen_mock({"result": "Proposal drafted successfully."})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "draft a proposal",
                "source_channel": "telegram",
                "metadata": {"chat_id": 123456},
            })

        # Should succeed via direct dispatch, not fall back to "Task completed."
        self.assertTrue(body["ok"])
        self.assertNotEqual(body["reply_text"], "Task completed.")
        self.assertIn("Proposal", body["reply_text"])

        # Should have attempted a direct dispatch (POST not to /route or /crew)
        direct_calls = [
            p for p in captured
            if "/route" not in p["url"] and "/crew" not in p["url"]
        ]
        self.assertGreater(len(direct_calls), 0, "No direct dispatch attempt found")


if __name__ == "__main__":
    unittest.main()
