"""
tests/test_intent_router.py
Tests for the CHIEF intent router — classifier, validation, thresholds.
LLM calls are mocked so tests run offline.
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ── Stubs for cascadia infrastructure ────────────────────────────────────────

def _stub(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

_stub("cascadia.shared.config",
      load_config=lambda p: {"components": [], "log_dir": "/tmp"})

class _FakeRuntime:
    def __init__(self, **kw):
        self.logger = MagicMock()
        for method in ("info", "warning", "error", "debug"):
            setattr(self.logger, method, lambda *a, **kw: None)
    def register_route(self, *a, **kw): pass
    def register_ws_route(self, *a, **kw): pass
    def start(self): pass

_stub("cascadia.shared.service_runtime", ServiceRuntime=_FakeRuntime)
_stub("cascadia.shared.logger", configure_logging=MagicMock())

# Real imports
from cascadia.chief.intent_router import (  # noqa: E402
    RoutingDecision,
    OPERATOR_CATALOG,
    MISSION_CATALOG,
    CONFIDENCE_DISPATCH,
    CONFIDENCE_CLARIFY,
    classify_intent,
    validate_routing_decision,
    append_history,
    get_history,
    set_last_action,
    get_last_action,
    _chat_history,
    _last_action,
    _parse_decision,
)
from cascadia.chief import operator_selector as sel  # noqa: E402
from cascadia.chief.server import ChiefService       # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _urlopen_mock(data: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp

def _llm_mock(decision_dict: dict):
    """Return a mock for urlopen that yields the given decision JSON."""
    resp = MagicMock()
    content = json.dumps(decision_dict)
    llm_payload = {"choices": [{"message": {"content": content}}]}
    resp.read.return_value = json.dumps(llm_payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp

def _make_svc() -> ChiefService:
    svc = ChiefService.__new__(ChiefService)
    svc.runtime = _FakeRuntime()
    return svc

CREW_ALL = {
    "crew_size": 3,
    "operators": {
        "recon":       {"operator_id": "recon",
                        "capabilities": ["research.outbound", "lead.scan", "report.csv"]},
        "quote_brief": {"operator_id": "quote_brief",
                        "capabilities": ["quote.generate", "proposal.draft"]},
        "scout":       {"operator_id": "scout",
                        "capabilities": ["lead.qualify"]},
    },
}


# ── 1. Exact keyword → fast-path (no LLM call) ───────────────────────────────

class TestKeywordFastPath(unittest.TestCase):
    def test_exact_keyword_fast_path(self):
        """'run recon' keyword match (confidence 1.0) must dispatch without LLM."""
        svc = _make_svc()
        llm_called = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "/crew" in url:
                return _urlopen_mock(CREW_ALL)
            if "4011" in url:
                llm_called.append(True)
                return _llm_mock({"action": "conversation", "confidence": 0.5, "reason": "x"})
            # BEACON
            return _urlopen_mock({"ok": True,
                                   "forward_response": {"result": "scan started"}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "run recon",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {},
            })

        self.assertEqual(code, 200)
        self.assertEqual(body["selected_type"], "operator")
        self.assertEqual(body["selected_target"], "recon")
        self.assertEqual(len(llm_called), 0, "LLM must NOT be called on keyword fast-path")


# ── 2. Natural language → LLM → dispatch recon ───────────────────────────────

class TestNaturalLanguageLeadRequest(unittest.TestCase):
    def test_natural_language_lead_request(self):
        """'I need to find new HVAC clients in Houston' → dispatch recon."""
        svc = _make_svc()
        decision = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.88,
            "reason": "User wants to find new clients.",
            "required_inputs": {"industry": "HVAC", "location": "Houston"},
            "missing_inputs": [],
            "question": None,
        }

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                return _llm_mock(decision)
            if "/crew" in url:
                return _urlopen_mock(CREW_ALL)
            return _urlopen_mock({"ok": True,
                                   "forward_response": {"result": "scan started"}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "I need to find new HVAC clients in Houston",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {},
            })

        self.assertEqual(body["selected_type"], "operator")
        self.assertEqual(body["selected_target"], "recon")


# ── 3. Quote request → quote_brief ───────────────────────────────────────────

class TestQuoteRequest(unittest.TestCase):
    def test_quote_request(self):
        """Proposal request → dispatch quote_brief."""
        svc = _make_svc()
        decision = {
            "action": "dispatch_operator",
            "target": "quote_brief",
            "confidence": 0.92,
            "reason": "User wants a proposal.",
            "required_inputs": {"job_type": "mezzanine"},
            "missing_inputs": [],
            "question": None,
        }

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                return _llm_mock(decision)
            if "/crew" in url:
                return _urlopen_mock(CREW_ALL)
            return _urlopen_mock({"ok": True,
                                   "forward_response": {"result": "proposal draft"}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "Can you draft a proposal for a mezzanine project?",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {},
            })

        self.assertEqual(body["selected_target"], "quote_brief")
        self.assertEqual(body["selected_type"], "operator")


# ── 4. Clarification — missing required inputs ────────────────────────────────

class TestClarificationMissingInputs(unittest.TestCase):
    def test_clarification_missing_inputs(self):
        """'Find me customers' → ask_clarification (missing industry + location)."""
        svc = _make_svc()
        decision = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.83,
            "reason": "Lead search requested.",
            "required_inputs": {},
            "missing_inputs": ["industry", "location"],
            "question": "What industry and location should I search for?",
        }

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                return _llm_mock(decision)
            if "/crew" in url:
                return _urlopen_mock(CREW_ALL)
            return _urlopen_mock({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "Find me customers",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {},
            })

        self.assertEqual(body["selected_type"], "none")
        self.assertIn("industry", body["reply_text"].lower() + str(body.get("raw_result", "")))


# ── 5. Conversation action ────────────────────────────────────────────────────

class TestConversation(unittest.TestCase):
    def test_conversation(self):
        """'What can you help me with?' → conversation, not a dispatch."""
        svc = _make_svc()
        decision = {
            "action": "conversation",
            "target": None,
            "confidence": 0.91,
            "reason": "General question.",
            "missing_inputs": [],
            "question": None,
        }

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                return _llm_mock(decision)
            if "/crew" in url:
                return _urlopen_mock({"crew_size": 0, "operators": {}})
            # LLM fallback call
            fallback = {"choices": [{"message": {"content": "I can help with leads and proposals."}}]}
            return _urlopen_mock(fallback) if "chat" not in url else _urlopen_mock(fallback)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "What can you help me with?",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {},
            })

        self.assertEqual(code, 200)
        self.assertEqual(body["selected_type"], "none")
        # Must NOT dispatch to any operator
        self.assertNotEqual(body.get("selected_target"), "recon")


# ── 6. Multi-step plan ────────────────────────────────────────────────────────

class TestMultiStep(unittest.TestCase):
    def test_multi_step(self):
        """Multi-step LLM decision → plan summary + first-step dispatch.
        Select_target is patched to return no match so we always enter the LLM path.
        email_outreach is in_development so validation strips it — only recon dispatched.
        """
        svc = _make_svc()
        decision = {
            "action": "multi_step_plan",
            "targets": ["recon", "email_outreach"],
            "confidence": 0.82,
            "reason": "User asked for two operations.",
            "missing_inputs": [],
            "question": None,
        }

        beacon_calls = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                return _llm_mock(decision)
            if "/route" in url:
                beacon_calls.append(json.loads(req.data.decode()))
                return _urlopen_mock({"ok": True,
                                       "forward_response": {"result": "scan started"}})
            return _urlopen_mock({})

        no_match = {"ok": False, "selected_type": "none",
                    "target": None, "confidence": 0.0, "reason": "no keyword"}

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("cascadia.chief.server.select_target", return_value=no_match):
            code, body = svc.handle_task({
                "task": "Scout my pipeline and then send outreach messages",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {},
            })

        self.assertEqual(code, 200)
        # email_outreach is in_development so validation strips it — only recon remains
        self.assertIn("recon", body.get("selected_target", ""))
        self.assertIn("plan", body["reply_text"].lower())


# ── 7. Unknown operator rejected ─────────────────────────────────────────────

class TestUnknownOperatorRejected(unittest.TestCase):
    def test_unknown_operator_rejected(self):
        """LLM returns target='magic_operator' → validation downgrades to conversation."""
        bad = RoutingDecision(
            action="dispatch_operator",
            target="magic_operator",
            confidence=0.95,
            reason="test",
        )
        result = validate_routing_decision(bad)
        self.assertEqual(result.action, "conversation")
        self.assertIsNone(result.target)


# ── 8. Low confidence → no dispatch ──────────────────────────────────────────

class TestLowConfidenceNoDispatch(unittest.TestCase):
    def test_low_confidence_no_dispatch(self):
        """confidence=0.50 on dispatch_operator → downgraded to ask_clarification."""
        svc = _make_svc()
        decision = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.50,
            "reason": "Uncertain lead request.",
            "required_inputs": {"industry": "HVAC", "location": "Houston"},
            "missing_inputs": [],
            "question": None,
        }

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                return _llm_mock(decision)
            if "/crew" in url:
                return _urlopen_mock({"crew_size": 0, "operators": {}})
            return _urlopen_mock({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, body = svc.handle_task({
                "task": "maybe find some leads?",
                "source_channel": "telegram",
                "sender": "andy",
                "metadata": {},
            })

        self.assertEqual(body["selected_type"], "none")
        beacon_target = body.get("selected_target")
        self.assertNotEqual(beacon_target, "recon",
                            "Low-confidence decision must not dispatch to recon")


# ── 9. In-development operator → ask_clarification ───────────────────────────

class TestInDevelopmentNoDispatch(unittest.TestCase):
    def test_in_development_no_dispatch(self):
        """email_outreach (in_development) → validate downgrades to ask_clarification."""
        bad = RoutingDecision(
            action="dispatch_operator",
            target="email_outreach",
            confidence=0.85,
            reason="email outreach requested",
            required_inputs={"recipient": "leads"},
        )
        result = validate_routing_decision(bad)
        self.assertEqual(result.action, "ask_clarification")
        self.assertIsNone(result.target)
        self.assertIn("roadmap", result.question.lower())


# ── 10. Follow-up resolves with history ──────────────────────────────────────

class TestFollowupResolvesWithHistory(unittest.TestCase):
    def test_followup_resolves_with_history(self):
        """History containing RECON report → 'how many emails' dispatches recon."""
        # Pre-seed history as if RECON already ran
        history = [
            {"role": "assistant",
             "content": "📊 RECON Report. Total leads: 240. "
                        "Top: Abacus Plumbing 713-812-7070. "
                        "High confidence: 130, Medium: 110."},
        ]

        decision_dict = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.82,
            "reason": "Follow-up about contacts from prior RECON scan.",
            "required_inputs": {"industry": "plumbing", "location": "Houston"},
            "missing_inputs": [],
            "question": None,
        }

        messages_sent = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                body = json.loads(req.data.decode())
                messages_sent.extend(body.get("messages", []))
                return _llm_mock(decision_dict)
            return _urlopen_mock({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = classify_intent(
                "how many emails in those contacts?",
                conversation_history=history,
            )

        self.assertEqual(result.action, "dispatch_operator")
        self.assertEqual(result.target, "recon")

        # History must be present in the messages array sent to LLM
        sent_roles = [m["role"] for m in messages_sent]
        self.assertIn("assistant", sent_roles,
                      "Prior assistant context must be passed to LLM")
        # The RECON report content must appear in what the LLM received
        all_content = " ".join(m["content"] for m in messages_sent)
        self.assertIn("RECON Report", all_content)


# ── 11. Repeat request resolves with history ──────────────────────────────────

class TestRepeatRequestWithHistory(unittest.TestCase):
    def test_repeat_request_with_history(self):
        """'do it again' with prior 'run recon' → dispatch recon."""
        history = [
            {"role": "user",      "content": "run recon"},
            {"role": "assistant", "content": "🔍 RECON scan started. I'll message you when done."},
        ]

        decision_dict = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.88,
            "reason": "User wants to repeat prior RECON scan.",
            "required_inputs": {"industry": "HVAC", "location": "Houston"},
            "missing_inputs": [],
            "question": None,
        }

        messages_sent = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                body = json.loads(req.data.decode())
                messages_sent.extend(body.get("messages", []))
                return _llm_mock(decision_dict)
            return _urlopen_mock({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = classify_intent("do it again", conversation_history=history)

        self.assertEqual(result.action, "dispatch_operator")
        self.assertEqual(result.target, "recon")

        # Prior "run recon" must reach the LLM
        all_content = " ".join(m["content"] for m in messages_sent)
        self.assertIn("run recon", all_content)


# ── 12. append_history / get_history round-trip ───────────────────────────────

class TestHistoryStore(unittest.TestCase):
    def setUp(self):
        # Isolate by using a fresh chat_id per test
        self.cid = "test_history_store_999"
        _chat_history.pop(self.cid, None)

    def test_append_and_get(self):
        append_history(self.cid, "user", "hello")
        append_history(self.cid, "assistant", "hi there")
        h = get_history(self.cid)
        self.assertEqual(len(h), 2)
        self.assertEqual(h[0]["role"], "user")
        self.assertEqual(h[1]["content"], "hi there")

    def test_maxlen_eviction(self):
        for i in range(8):
            append_history(self.cid, "user", f"msg {i}")
        h = get_history(self.cid)
        self.assertEqual(len(h), 6, "Store must cap at HISTORY_LIMIT=6")
        self.assertEqual(h[0]["content"], "msg 2",
                         "Oldest messages must be evicted first")

    def test_empty_chat_id_ignored(self):
        append_history("", "user", "should not store")
        self.assertEqual(get_history(""), [])


# ── 13. "do it again" resolves via last_action ────────────────────────────────

class TestDoItAgainResolvesWithLastAction(unittest.TestCase):
    def setUp(self):
        self.cid = "test_last_action_do_it_again"
        _last_action.pop(self.cid, None)

    def test_do_it_again_resolves(self):
        """set_last_action(recon) → 'do it again' LLM receives last_action context."""
        set_last_action(self.cid, "dispatch_operator", "recon", "240 leads found")

        decision_dict = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.88,
            "reason": "Repeat of prior RECON scan.",
            "required_inputs": {"industry": "HVAC", "location": "Houston"},
            "missing_inputs": [],
            "question": None,
        }

        messages_sent = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                body = json.loads(req.data.decode())
                messages_sent.extend(body.get("messages", []))
                return _llm_mock(decision_dict)
            return _urlopen_mock({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = classify_intent(
                "do it again", [], OPERATOR_CATALOG, MISSION_CATALOG,
                chat_id=self.cid,
            )

        self.assertEqual(result.action, "dispatch_operator")
        self.assertEqual(result.target, "recon")

        # System prompt must contain last_action context
        system_content = next(
            (m["content"] for m in messages_sent if m["role"] == "system"), ""
        )
        self.assertIn("CURRENT SESSION CONTEXT", system_content)
        self.assertIn("recon", system_content)
        self.assertIn("240 leads found", system_content)


# ── 14. "those contacts" resolves via last_action ────────────────────────────

class TestThoseContactsResolvesWithLastAction(unittest.TestCase):
    def setUp(self):
        self.cid = "test_last_action_those_contacts"
        _last_action.pop(self.cid, None)

    def test_those_contacts_resolves(self):
        """last_action with RECON result → 'how many have emails?' must not ask_clarification."""
        set_last_action(
            self.cid, "dispatch_operator", "recon",
            "Total leads: 240. Top: Abacus Plumbing 713-812-7070",
        )

        decision_dict = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.84,
            "reason": "Follow-up query about prior RECON result.",
            "required_inputs": {"industry": "plumbing", "location": "Houston"},
            "missing_inputs": [],
            "question": None,
        }

        messages_sent = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                body = json.loads(req.data.decode())
                messages_sent.extend(body.get("messages", []))
                return _llm_mock(decision_dict)
            return _urlopen_mock({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = classify_intent(
                "how many of those have emails?", [], OPERATOR_CATALOG, MISSION_CATALOG,
                chat_id=self.cid,
            )

        self.assertNotEqual(result.action, "ask_clarification",
                            "With last_action context LLM should not ask for clarification")

        system_content = next(
            (m["content"] for m in messages_sent if m["role"] == "system"), ""
        )
        self.assertIn("CURRENT SESSION CONTEXT", system_content)
        self.assertIn("Abacus Plumbing", system_content)


# ── 15. No last_action → prompt unchanged, still routes correctly ─────────────

class TestNoLastActionStillWorks(unittest.TestCase):
    def setUp(self):
        self.cid = "test_no_last_action_new_user_999"
        _last_action.pop(self.cid, None)

    def test_no_last_action_still_works(self):
        """Fresh chat_id with no last_action → system prompt has no SESSION CONTEXT block."""
        decision_dict = {
            "action": "dispatch_operator",
            "target": "recon",
            "confidence": 0.90,
            "reason": "Explicit recon request.",
            "required_inputs": {"industry": "HVAC", "location": "Houston"},
            "missing_inputs": [],
            "question": None,
        }

        messages_sent = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "4011" in url:
                body = json.loads(req.data.decode())
                messages_sent.extend(body.get("messages", []))
                return _llm_mock(decision_dict)
            return _urlopen_mock({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = classify_intent(
                "run recon", [], OPERATOR_CATALOG, MISSION_CATALOG,
                chat_id=self.cid,
            )

        self.assertEqual(result.target, "recon")

        system_content = next(
            (m["content"] for m in messages_sent if m["role"] == "system"), ""
        )
        self.assertNotIn("CURRENT SESSION CONTEXT", system_content,
                         "No last_action should mean no context block in system prompt")


if __name__ == "__main__":
    unittest.main()
