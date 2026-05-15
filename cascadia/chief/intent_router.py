"""
cascadia/chief/intent_router.py
LLM-based semantic intent classifier for CHIEF.

The LLM returns a structured routing decision JSON.
CHIEF validates and applies policy gates before any dispatch.
The LLM never executes operators directly.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
import urllib.error
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("chief.intent_router")

LOCAL_LLM_URL   = "http://127.0.0.1:4011/v1/chat/completions"
LOCAL_LLM_MODEL = "zyrcon-3b"

CONFIDENCE_DISPATCH = 0.80
CONFIDENCE_CLARIFY  = 0.55

# ── Conversation history store ────────────────────────────────────────────────
# 3 exchanges = 6 messages per chat_id; evicted automatically via deque maxlen

HISTORY_LIMIT = 6

_history_lock: threading.Lock = threading.Lock()
_chat_history: dict[str, deque] = {}

_last_action_lock: threading.Lock = threading.Lock()
_last_action: dict[str, dict] = {}


def set_last_action(
    chat_id: str, action: str, target: str | None, result_preview: str
) -> None:
    if not chat_id:
        return
    with _last_action_lock:
        _last_action[chat_id] = {
            "action":         action,
            "target":         target,
            "result_preview": result_preview[:300],
        }


def get_last_action(chat_id: str) -> dict:
    if not chat_id:
        return {}
    with _last_action_lock:
        return dict(_last_action.get(chat_id, {}))


def append_history(chat_id: str, role: str, content: str) -> None:
    """Record one message turn for chat_id."""
    if not chat_id:
        return
    with _history_lock:
        if chat_id not in _chat_history:
            _chat_history[chat_id] = deque(maxlen=HISTORY_LIMIT)
        _chat_history[chat_id].append({"role": role, "content": content})


def get_history(chat_id: str) -> list[dict]:
    """Return the last HISTORY_LIMIT messages for chat_id (newest last)."""
    if not chat_id:
        return []
    with _history_lock:
        return list(_chat_history.get(chat_id, []))

VALID_ACTIONS = frozenset({
    "dispatch_operator",
    "start_mission",
    "ask_clarification",
    "conversation",
    "multi_step_plan",
})

# ── Catalogs ──────────────────────────────────────────────────────────────────

OPERATOR_CATALOG: dict[str, dict[str, Any]] = {
    "recon": {
        "display_name": "RECON",
        "description": "Find and research contractor leads in Houston",
        "example_phrases": [
            "find leads", "search for contractors",
            "I need new clients", "find HVAC companies",
            "run recon", "scan for leads",
        ],
        "required_inputs": ["industry", "location"],
        "status": "available",
    },
    "quote_brief": {
        "display_name": "Quote Brief",
        "description": "Draft proposals and quotes for jobs",
        "example_phrases": [
            "draft a proposal", "write a quote",
            "create an estimate", "proposal for a job",
            "mezzanine installation quote",
        ],
        "required_inputs": ["job_type"],
        "status": "available",
    },
    "scout": {
        "display_name": "SCOUT",
        "description": "Monitor inbound leads and qualify prospects",
        "example_phrases": [
            "qualify this lead", "check inbound leads",
            "score this prospect",
        ],
        "required_inputs": [],
        "status": "available",
    },
    "email_outreach": {
        "display_name": "Email Outreach",
        "description": "Draft and send outreach emails to leads",
        "example_phrases": [
            "send outreach", "draft an email to leads",
            "email campaign",
        ],
        "required_inputs": ["recipient"],
        "status": "in_development",
    },
    "social": {
        "display_name": "Social",
        "description": "Social media posting and campaigns",
        "example_phrases": ["post on social", "social campaign"],
        "required_inputs": [],
        "status": "in_development",
    },
}

MISSION_CATALOG: dict[str, dict[str, Any]] = {
    "revenue_sales": {
        "display_name": "Revenue & Sales",
        "description": "Find leads, qualify prospects, close deals",
        "suggested_operators": ["recon", "scout", "quote_brief"],
        "status": "available",
    },
    "brand_reputation": {
        "display_name": "Brand & Reputation",
        "description": "SEO, reviews, social presence",
        "suggested_operators": ["seo", "social"],
        "status": "in_development",
    },
}

# ── Routing decision ───────────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    action: str
    target: str | None = None
    targets: list[str] = field(default_factory=list)
    mission: str | None = None
    confidence: float = 0.0
    reason: str = ""
    required_inputs: dict[str, Any] = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    question: str | None = None

    def to_dict(self) -> dict:
        return {
            "action":          self.action,
            "target":          self.target,
            "targets":         self.targets,
            "mission":         self.mission,
            "confidence":      self.confidence,
            "reason":          self.reason,
            "required_inputs": self.required_inputs,
            "missing_inputs":  self.missing_inputs,
            "question":        self.question,
        }


def _conversation_fallback(reason: str = "classifier failed") -> RoutingDecision:
    return RoutingDecision(action="conversation", confidence=0.0, reason=reason)


# ── Catalog prompt builder ─────────────────────────────────────────────────────

def _build_catalog_text() -> str:
    lines = ["AVAILABLE OPERATORS:"]
    for op_id, op in OPERATOR_CATALOG.items():
        status = op["status"]
        examples = ", ".join(f'"{e}"' for e in op["example_phrases"][:3])
        req = ", ".join(op["required_inputs"]) or "none"
        lines.append(
            f'  {op_id} [{status}]: {op["description"]} '
            f'| examples: {examples} | required_inputs: {req}'
        )
    lines.append("\nAVAILABLE MISSIONS:")
    for m_id, m in MISSION_CATALOG.items():
        ops = ", ".join(m["suggested_operators"])
        lines.append(f'  {m_id} [{m["status"]}]: {m["description"]} | operators: {ops}')
    return "\n".join(lines)


_BASE_SYSTEM_PROMPT = """\
You are a routing classifier for Cascadia OS, a business management platform for \
contractor and trades companies.

Your job: read the user message and return a JSON routing decision.

{catalog}

ROUTING RULES:
- action must be one of: dispatch_operator, start_mission, ask_clarification, \
conversation, multi_step_plan
- Do NOT invent operator names. Only use operator ids from the catalog above.
- If the operator status is "in_development", use action=ask_clarification and \
explain it is not yet available.
- If required_inputs are missing from the user message, use action=ask_clarification \
and set missing_inputs accordingly.
- Use action=conversation for general questions, greetings, or when no operator applies.
- Use action=multi_step_plan only when the user explicitly requests two or more \
distinct operations.
- confidence is 0.0–1.0. Be conservative — prefer 0.6–0.85 range unless very clear.
- Never say something was executed. Only route.
- Return ONLY valid JSON with no markdown, no explanation, no code fences.

JSON schema:
{{
  "action": str,
  "target": str or null,
  "targets": list[str] or [],
  "mission": str or null,
  "confidence": float,
  "reason": str,
  "required_inputs": dict,
  "missing_inputs": list[str],
  "question": str or null
}}
""".format(catalog=_build_catalog_text())


def _build_system_prompt(chat_id: str) -> str:
    last = get_last_action(chat_id)
    if not last:
        return _BASE_SYSTEM_PROMPT
    target = last.get("target") or "none"
    return _BASE_SYSTEM_PROMPT + (
        f"\n\nCURRENT SESSION CONTEXT:\n"
        f"Last action: {last['action']}\n"
        f"Last operator used: {target}\n"
        f"Last result summary: {last.get('result_preview', '')}\n\n"
        f"Use this context to resolve references:\n"
        f"- \"do it again\" → repeat last operator ({target})\n"
        f"- \"those contacts\" → refers to last operator result above\n"
        f"- \"how many of those\" → refers to last result above\n\n"
        f"Do NOT ask for clarification if last_action answers the question."
    )


# ── LLM call ──────────────────────────────────────────────────────────────────

def _call_llm(messages: list[dict]) -> str | None:
    payload = json.dumps({
        "model":       LOCAL_LLM_MODEL,
        "messages":    messages,
        "max_tokens":  300,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        LOCAL_LLM_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        return (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
        )
    except Exception as exc:
        log.warning("intent_router: LLM call failed: %s", exc)
        return None


def _parse_decision(raw: str) -> RoutingDecision | None:
    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        d = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        log.warning("intent_router: JSON parse failed for: %s", raw[:120])
        return None

    action = d.get("action", "")
    if action not in VALID_ACTIONS:
        log.warning("intent_router: invalid action %r — discarding", action)
        return None

    confidence = float(d.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))

    return RoutingDecision(
        action          = action,
        target          = d.get("target") or None,
        targets         = d.get("targets") or [],
        mission         = d.get("mission") or None,
        confidence      = confidence,
        reason          = str(d.get("reason", "")),
        required_inputs = d.get("required_inputs") or {},
        missing_inputs  = d.get("missing_inputs") or [],
        question        = d.get("question") or None,
    )


# ── Public classifier ──────────────────────────────────────────────────────────

def classify_intent(
    user_message: str,
    conversation_history: list[dict] | None = None,
    operator_catalog: dict | None = None,
    mission_catalog: dict | None = None,
    chat_id: str = "",
) -> RoutingDecision:
    """
    Call the local LLM to classify the user's intent and return a RoutingDecision.
    operator_catalog / mission_catalog override module-level catalogs for testing.
    chat_id is used to inject last_action context into the system prompt.
    """
    messages: list[dict] = [{"role": "system", "content": _build_system_prompt(chat_id)}]
    if conversation_history:
        messages.extend(conversation_history[-6:])  # last 3 exchanges
    messages.append({"role": "user", "content": user_message})

    last = get_last_action(chat_id)
    log.info(
        "classify_intent: msg=%r history_turns=%d last_action=%r total_messages=%d",
        user_message[:60], len(conversation_history or []),
        last.get("action") if last else None, len(messages),
    )

    raw = _call_llm(messages)
    if not raw:
        return _conversation_fallback("LLM unreachable")

    decision = _parse_decision(raw)
    if decision is None:
        return _conversation_fallback("JSON parse failed")

    return decision


# ── Validation layer ───────────────────────────────────────────────────────────

def validate_routing_decision(
    decision: RoutingDecision,
    catalog: dict | None = None,
) -> RoutingDecision:
    """
    Enforce policy gates. Returns a (possibly downgraded) RoutingDecision.
    Never raises — bad decisions become conversation.
    """
    cat = catalog if catalog is not None else OPERATOR_CATALOG

    # Clamp confidence
    decision.confidence = max(0.0, min(1.0, decision.confidence))

    if decision.action not in VALID_ACTIONS:
        return _conversation_fallback(f"invalid action: {decision.action!r}")

    if decision.action == "dispatch_operator":
        target = decision.target
        if not target or target not in cat:
            log.warning("intent_router: unknown operator %r — downgrading", target)
            return _conversation_fallback(f"unknown operator: {target!r}")

        op = cat[target]
        status = op.get("status", "available")

        if status == "disabled":
            return _conversation_fallback(f"operator {target!r} is disabled")

        if status == "in_development":
            display = op.get("display_name", target)
            return RoutingDecision(
                action     = "ask_clarification",
                confidence = decision.confidence,
                reason     = f"{display} is in development",
                question   = (
                    f"{display} isn't available just yet — it's on the roadmap. "
                    f"Can I help you with something else?"
                ),
            )

        # Check required inputs — only flag as missing if not already provided
        required = op.get("required_inputs", [])
        missing  = [r for r in required if r not in (decision.required_inputs or {})]
        if missing:
            decision.missing_inputs = missing
            decision.action = "ask_clarification"
            if not decision.question:
                missing_str = " and ".join(missing)
                decision.question = (
                    f"To use {op.get('display_name', target)} I need a bit more info: "
                    f"what {missing_str} should I use?"
                )

    if decision.action == "multi_step_plan":
        valid_targets = [t for t in (decision.targets or []) if t in cat]
        # Filter out unavailable operators silently
        available_targets = [
            t for t in valid_targets
            if cat[t].get("status") == "available"
        ]
        if not available_targets:
            return _conversation_fallback("no available operators in multi-step plan")
        decision.targets = available_targets

    if decision.action == "start_mission":
        if not decision.mission or decision.mission not in MISSION_CATALOG:
            return _conversation_fallback(f"unknown mission: {decision.mission!r}")

    return decision
