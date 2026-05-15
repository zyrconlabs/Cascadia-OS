"""
cascadia/chief/fallback.py
3-tier intelligent fallback for CHIEF when no operator matches.

Tier 1: Handled upstream — an operator matched and was dispatched.
Tier 2: Message relates to a known-but-not-yet-built capability.
         Returns a friendly "coming soon" reply + what's available.
Tier 3: Unknown topic — calls local LLM for a conversational response.
         Falls back to a static message if LLM is unreachable.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

LOCAL_LLM_URL = "http://127.0.0.1:4011/v1/chat/completions"
LOCAL_LLM_MODEL = "zyrcon-3b"

KNOWN_CAPABILITIES: dict[str, str] = {
    "recon":       "Find and research contractor leads in Houston",
    "quote_brief": "Draft proposals and quotes for jobs",
    "lead":        "Manage and qualify leads",
    "seo":         "SEO research and Google ranking analysis",
    "competitor":  "Competitor and market research",
    "contract":    "Contract and deadline management",
    "research":    "General research and data enrichment",
    "code":        "Code, bug fixes, and GitHub tasks",
}

IN_DEVELOPMENT: list[str] = [
    "scheduling", "invoice", "invoicing", "payment", "payments", "crm sync",
    "email campaign", "email campaigns", "social media", "social posting",
]

_SYSTEM_PROMPT = (
    "You are Chief, an AI business assistant for a contractor and trades company. "
    "You help with lead research, proposals, quotes, and job management. "
    "Keep responses friendly, professional, and brief (2-3 sentences). "
    "Never mention error messages, operators, workers, or technical terms."
)


def _capability_list() -> str:
    lines = ["Here's what I can do:"]
    for desc in KNOWN_CAPABILITIES.values():
        lines.append(f"• {desc}")
    return "\n".join(lines)


def _call_local_llm(user_prompt: str) -> str | None:
    payload = json.dumps({
        "model": LOCAL_LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": 150,
        "temperature": 0.7,
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
        content = (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
        )
        return content or None
    except Exception:
        return None


def intelligent_fallback(text: str, channel: str = "telegram") -> str:
    text_lower = text.lower()

    # Tier 2 — known in-development feature
    for topic in IN_DEVELOPMENT:
        if topic in text_lower:
            friendly = topic.replace(" campaign", "").replace(" sync", "").title()
            return (
                f"That's not something I can handle just yet — "
                f"{friendly} is on the roadmap. In the meantime, "
                f"here's what I can help with right now:\n\n"
                + _capability_list()
            )

    # Tier 3 — LLM conversational response
    prompt = (
        f"I received this message: \"{text}\"\n\n"
        f"I don't have a specific tool for this right now. "
        f"Respond in 2-3 sentences. Either ask a clarifying question, "
        f"explain what I CAN help with and offer to do that instead, "
        f"or if it's a general question, answer it helpfully.\n\n"
        f"Available capabilities:\n{_capability_list()}"
    )
    llm_reply = _call_local_llm(prompt)
    if llm_reply:
        return llm_reply

    # Static fallback if LLM is unreachable
    return (
        "I'm not sure how to help with that one. "
        "Here's what I can do right now:\n\n"
        + _capability_list()
    )
