"""
cascadia/chief/server.py - Cascadia OS 2026.5
CHIEF: Task orchestrator and operator router.

Owns: inbound task receipt, operator selection, BEACON dispatch,
      reply formatting, CREW self-registration.
Does not own: operator execution, capability validation (BEACON/CREW own that),
              session management (BELL owns that), channel I/O (VANGUARD owns that).

CHIEF is how a task finds the right worker.
"""
# MATURITY: PRODUCTION — keyword+capability selector, BEACON dispatch, sync reply path.
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import threading
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime
from cascadia.chief.models import TaskRequest, TaskResponse
from cascadia.chief.operator_selector import select_target
from cascadia.chief.fallback import intelligent_fallback

# ── gen_image debounce ───────────────────────────────────────────────────────
# Ignore repeat gen_image taps (or Telegram callback re-deliveries) while a gen
# for the same post is in-flight. Cleared when the gen thread finishes, so a
# deliberate 🔄 New Image after seeing the result still works.
_gen_in_flight: Dict[str, float] = {}
_gen_lock = threading.Lock()
_GEN_DEBOUNCE_TTL = 60  # seconds


def _gen_acquire(key: str) -> bool:
    """True if a gen for `key` may start; False if one is already in-flight
    (within the TTL). Marks it in-flight on success."""
    now = time.time()
    with _gen_lock:
        ts = _gen_in_flight.get(key)
        if ts is not None and (now - ts) < _GEN_DEBOUNCE_TTL:
            return False
        _gen_in_flight[key] = now
        return True


def _gen_release(key: str) -> None:
    with _gen_lock:
        _gen_in_flight.pop(key, None)


# ── GPU yield coordinator (Tier 2) ───────────────────────────────────────────
# Signal RECON to yield the GPU during an LLM-heavy CHIEF dispatch. RECON reads
# the top-level "gpu_yield" flag from operator_intent.json each cycle and skips
# its qualify call while it is True. Always cleared in a finally so RECON is
# never permanently locked out.
_GPU_YIELD_INTENT = Path(__file__).parent.parent.parent / "data" / "runtime" / "operator_intent.json"


def _set_gpu_yield(busy: bool) -> None:
    try:
        d = json.loads(_GPU_YIELD_INTENT.read_text()) if _GPU_YIELD_INTENT.exists() else {}
        d["gpu_yield"] = busy
        _GPU_YIELD_INTENT.write_text(json.dumps(d, indent=2))
    except Exception as e:
        log.warning("_set_gpu_yield(%s) failed: %s", busy, e)


from cascadia.chief.commands import (
    parse_command, build_help_text, build_operators_text,
    parse_contact_command, parse_quote_command, parse_approval_command,
)
from cascadia.chief.intent_router import (
    classify_intent,
    validate_routing_decision,
    append_history,
    get_history,
    get_last_action,
    set_last_action,
    OPERATOR_CATALOG,
    CONFIDENCE_DISPATCH,
    CONFIDENCE_CLARIFY,
)
from cascadia.automation.workflow_runtime import WorkflowRuntime
from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep

_VERSION = "2026.6"

# Environment overrides — ports resolved from config at startup, not hardcoded
CHIEF_PORT = int(os.environ.get("CHIEF_PORT", "6211"))
CREW_URL   = os.environ.get("CREW_URL", "http://127.0.0.1:5100")
BEACON_URL = os.environ.get("BEACON_URL", "http://127.0.0.1:6200")
MISSION_MANAGER_URL = os.environ.get("MISSION_MANAGER_URL", "http://127.0.0.1:6207")
BELL_URL   = os.environ.get("BELL_URL", "http://127.0.0.1:6204")
TELEGRAM_URL = os.environ.get("TELEGRAM_URL", "http://127.0.0.1:9000")
OM_URL     = os.environ.get("OM_URL", "http://127.0.0.1:6210")

_PENDING_OUTREACH_PATH = Path(os.path.expanduser(
    "~/Zyrcon/operators/cascadia-os-operators/recon/output/pending_outreach.json"
))
_REPLIES_PATH = Path(os.path.expanduser(
    "~/Zyrcon/operators/cascadia-os-operators/recon/output/replies.json"
))
_OWN_EMAIL = "hello@zyrcon.ai"

_WAKE_WAIT = 35  # seconds to wait for operator health after wake (covers OM's 30s poll cycle)

_last_startup_report_at: float = 0.0
_STARTUP_REPORT_COOLDOWN: int = 300  # 5 minutes — suppresses duplicate scheduler fires

# ── Business hours gate (Mon-Sat 08:15–16:00 CT) ──────────────────────────
_SEND_TZ    = ZoneInfo("America/Chicago")
_SEND_START = datetime.time(8, 15)
_SEND_END   = datetime.time(16, 0)
_SEND_DAYS  = {0, 1, 2, 3, 4, 5}  # Mon=0, Sat=5

def _is_business_hours() -> bool:
    now = datetime.datetime.now(_SEND_TZ)
    return now.weekday() in _SEND_DAYS and _SEND_START <= now.time() < _SEND_END

def _next_send_window() -> datetime.datetime:
    now  = datetime.datetime.now(_SEND_TZ)
    cand = now.replace(hour=8, minute=15, second=0, microsecond=0)
    if now.weekday() in _SEND_DAYS and cand > now:
        return cand
    cand += datetime.timedelta(days=1)
    while cand.weekday() not in _SEND_DAYS:
        cand += datetime.timedelta(days=1)
    return cand
# ── End business hours gate ───────────────────────────────────────────────


def _http_post(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _tg_send(
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
) -> None:
    """Send a Telegram message via the connector. Optionally attach a keyboard."""
    tg_payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup:
        tg_payload["reply_markup"] = reply_markup
    if parse_mode:
        tg_payload["parse_mode"] = parse_mode
    try:
        _http_post(
            os.environ.get("TELEGRAM_URL", "http://127.0.0.1:9000") + "/send",
            tg_payload,
            timeout=8,
        )
    except Exception:
        pass


def _http_get(url: str, timeout: int = 5) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_source(metadata: dict | None) -> str:
    """Source channel from task metadata ('prism', 'telegram', ...)."""
    return metadata.get("source", "") if metadata else ""


def _is_prism(metadata: dict | None) -> bool:
    """True when the task originated from the PRISM dashboard chat bar.
    PRISM expects full results returned synchronously in reply_text rather
    than pushed to Telegram."""
    return _get_source(metadata) == "prism"


# ── Menu system helpers ───────────────────────────────────────────────────────

def _get_menu_counts() -> dict:
    """Return live counts for button labels: pending approvals and unread replies."""
    pending = 0
    replies = 0
    try:
        if _PENDING_OUTREACH_PATH.exists():
            data = json.loads(_PENDING_OUTREACH_PATH.read_text())
            pending = len(data) if isinstance(data, dict) else len(data)
    except Exception:
        pass
    try:
        if _REPLIES_PATH.exists():
            data = json.loads(_REPLIES_PATH.read_text())
            if isinstance(data, list):
                replies = len([r for r in data
                               if isinstance(r, dict) and r.get("from", "") != _OWN_EMAIL])
    except Exception:
        pass
    return {"pending": pending, "replies": replies}


def _build_inline_keyboard(rows: list[list[dict]]) -> dict:
    """Build a Telegram InlineKeyboardMarkup dict from a list of button rows."""
    return {
        "inline_keyboard": [
            [{"text": btn["text"], "callback_data": btn["data"]} for btn in row]
            for row in rows
        ]
    }


_MENU_TEXT = "🏠 <b>Zyrcon Command Center</b>\nSelect a mission area:"

def _main_menu_keyboard(pending_count: int = 0) -> dict:
    approve_label = f"✅ Approve ({pending_count})" if pending_count > 0 else "✅ Approve"
    return {"inline_keyboard": [
        [
            {"text": "💼 Sales",       "callback_data": "menu_sales"},
            {"text": "💰 Finances",    "callback_data": "menu_finances"},
        ],
        [
            {"text": "📣 Marketing",   "callback_data": "menu_marketing"},
            {"text": "🏃 Management",  "callback_data": "menu_management"},
        ],
        [
            {"text": approve_label,    "callback_data": "menu_approve"},
            {"text": "📥 Inbox",       "callback_data": "menu_inbox"},
        ],
        [
            {"text": "📊 Reports",     "callback_data": "menu_reports"},
            {"text": "⚙️ System",      "callback_data": "menu_system"},
        ],
    ]}


def _sales_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📡 RECON",   "callback_data": "menu_recon"},
            {"text": "🎯 SCOUT",   "callback_data": "menu_scout"},
        ],
        [
            {"text": "📧 EMAIL",   "callback_data": "menu_email"},
            {"text": "👥 CRM",     "callback_data": "menu_crm"},
        ],
        [
            {"text": "🎪 DEMO",    "callback_data": "menu_demo"},
            {"text": "🏠 Menu",    "callback_data": "back_to_menu"},
        ],
    ]}


def _finances_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📋 Quote",   "callback_data": "menu_quote"},
            {"text": "🏠 Menu",    "callback_data": "back_to_menu"},
        ],
    ]}


def _marketing_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "𝕏 X",            "callback_data": "menu_x"},
            {"text": "📘 Facebook",     "callback_data": "menu_facebook"},
        ],
        [
            {"text": "📸 Instagram",    "callback_data": "menu_instagram"},
            {"text": "🚀 Campaigns",    "callback_data": "menu_campaigns"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _management_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📅 Daily Ops",    "callback_data": "menu_daily_ops"},
            {"text": "⚙️ Orchestration","callback_data": "menu_orchestration"},
        ],
        [
            {"text": "👨‍💻 Code",         "callback_data": "cmd_code"},
            {"text": "📋 Code Projects", "callback_data": "cmd_code_list"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _recon_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📡 Run RECON",    "callback_data": "cmd_recon"},
            {"text": "📋 Leads",        "callback_data": "cmd_leads"},
        ],
        [
            {"text": "📊 Pipeline",     "callback_data": "cmd_pipeline"},
            {"text": "📤 Outreach",     "callback_data": "cmd_outreach"},
        ],
        [
            {"text": "✅ Approve All",  "callback_data": "cmd_approve_all"},
            {"text": "🔄 Follow-ups",   "callback_data": "cmd_followups"},
        ],
        [
            {"text": "📥 Replies",      "callback_data": "cmd_replies"},
            {"text": "♻️ Reactivate",   "callback_data": "cmd_reactivate"},
        ],
        [
            {"text": "▶️ Start",        "callback_data": "cmd_recon_start"},
            {"text": "⏹ Stop",          "callback_data": "cmd_recon_stop"},
        ],
        [
            {"text": "💼 Sales",        "callback_data": "menu_sales"},
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _scout_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "🎯 Scout",        "callback_data": "cmd_scout"},
            {"text": "🔻 Funnel",       "callback_data": "cmd_funnel"},
        ],
        [
            {"text": "✅ Email Approve","callback_data": "cmd_email_approve"},
            {"text": "⏭ Email Skip",    "callback_data": "cmd_email_skip"},
        ],
        [
            {"text": "💼 Sales",        "callback_data": "menu_sales"},
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _email_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📥 Inbox Check",  "callback_data": "cmd_inbox_check"},
            {"text": "📊 Email Status", "callback_data": "cmd_email_status"},
        ],
        [
            {"text": "💼 Sales",        "callback_data": "menu_sales"},
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _crm_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "👥 CRM",          "callback_data": "cmd_crm"},
            {"text": "😴 Sleep",        "callback_data": "cmd_crm_sleep"},
        ],
        [
            {"text": "⏰ Wake",          "callback_data": "cmd_crm_wake"},
            {"text": "💼 Sales",        "callback_data": "menu_sales"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _demo_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "🎪 Demo Status",  "callback_data": "cmd_demo_status"},
            {"text": "💼 Sales",        "callback_data": "menu_sales"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _quote_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📋 Quote",        "callback_data": "cmd_quote"},
            {"text": "🤝 Close",        "callback_data": "cmd_close"},
        ],
        [
            {"text": "🧾 Invoice",      "callback_data": "cmd_invoice"},
            {"text": "⭐ Review",       "callback_data": "cmd_review"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _x_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "✅ Approve",      "callback_data": "cmd_x_approve"},
            {"text": "⏭ Skip",          "callback_data": "cmd_x_skip"},
        ],
        [
            {"text": "✅ Approve All",  "callback_data": "cmd_approve_all_x"},
            {"text": "🚀 Post Now",     "callback_data": "cmd_x_post_now"},
        ],
        [
            {"text": "📊 Status",       "callback_data": "cmd_x_status"},
            {"text": "📣 Marketing",    "callback_data": "menu_marketing"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _facebook_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "✅ Approve",      "callback_data": "cmd_fb_approve"},
            {"text": "⏭ Skip",          "callback_data": "cmd_fb_skip"},
        ],
        [
            {"text": "✅ Approve All",  "callback_data": "cmd_approve_all_fb"},
            {"text": "🚀 Post Now",     "callback_data": "cmd_fb_post_now"},
        ],
        [
            {"text": "📊 Status",       "callback_data": "cmd_fb_status"},
            {"text": "📣 Marketing",    "callback_data": "menu_marketing"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _instagram_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "✅ Approve",      "callback_data": "cmd_ig_approve"},
            {"text": "⏭ Skip",          "callback_data": "cmd_ig_skip"},
        ],
        [
            {"text": "✅ Approve All",  "callback_data": "cmd_approve_all_ig"},
            {"text": "🚀 Post Now",     "callback_data": "cmd_ig_post_now"},
        ],
        [
            {"text": "🖼 Gen Image",    "callback_data": "cmd_ig_gen_image"},
            {"text": "🔄 Regen",        "callback_data": "cmd_ig_regen"},
        ],
        [
            {"text": "📊 Status",       "callback_data": "cmd_ig_status"},
            {"text": "🗑 Clear Image",  "callback_data": "cmd_clear_image"},
        ],
        [
            {"text": "📣 Marketing",    "callback_data": "menu_marketing"},
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _campaigns_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "🚀 Generate",     "callback_data": "cmd_social_generate"},
            {"text": "📊 Social",       "callback_data": "cmd_social"},
        ],
        [
            {"text": "📣 Marketing",    "callback_data": "menu_marketing"},
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _daily_ops_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "🌅 Brief",        "callback_data": "cmd_brief"},
            {"text": "📅 Schedule",     "callback_data": "cmd_schedule"},
        ],
        [
            {"text": "🚧 Blockers",     "callback_data": "cmd_blockers"},
            {"text": "🌆 EOD",          "callback_data": "cmd_eod"},
        ],
        [
            {"text": "📆 Weekly",       "callback_data": "cmd_weekly"},
            {"text": "🎯 Missions",     "callback_data": "cmd_missions"},
        ],
        [
            {"text": "📊 Performance",  "callback_data": "cmd_performance"},
            {"text": "📈 KPIs",         "callback_data": "cmd_performance_kpis"},
        ],
        [
            {"text": "🏃 Management",   "callback_data": "menu_management"},
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _orchestration_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📊 Status",       "callback_data": "cmd_status"},
            {"text": "🤖 Operators",    "callback_data": "cmd_operators"},
        ],
        [
            {"text": "🔢 Version",      "callback_data": "cmd_version"},
            {"text": "🏃 Management",   "callback_data": "menu_management"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _reports_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "📊 Startup",      "callback_data": "cmd_startup_report"},
            {"text": "📧 Email Status", "callback_data": "cmd_email_status"},
        ],
        [
            {"text": "🪙 Tokens Today", "callback_data": "cmd_token"},
            {"text": "📅 Token Week",   "callback_data": "cmd_token_week"},
        ],
        [
            {"text": "📆 Token Month",  "callback_data": "cmd_token_month"},
            {"text": "🧠 RAM",          "callback_data": "cmd_ram"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}


def _system_menu_keyboard() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "🔑 Check Creds",  "callback_data": "cmd_check_credentials"},
            {"text": "🔑 Check Live",   "callback_data": "cmd_check_credentials_live"},
        ],
        [
            {"text": "🧙 Wizard",       "callback_data": "cmd_wizard"},
            {"text": "❓ Help",          "callback_data": "cmd_help"},
        ],
        [
            {"text": "🏠 Menu",         "callback_data": "back_to_menu"},
        ],
    ]}

def _persistent_keyboard() -> dict:
    """ReplyKeyboardMarkup pinned above the text input at all times."""
    return {
        "keyboard": [[
            {"text": "🏠 Menu"},
            {"text": "✅ Approve All"},
            {"text": "📥 Inbox"},
        ]],
        "resize_keyboard": True,
        "persistent": True,
        "input_field_placeholder": "Or type a command...",
    }

# ── End menu system helpers ───────────────────────────────────────────────────


# ── Outreach send-time safety gate ───────────────────────────────────────────
# Final check before any outreach email leaves CHIEF. Catches bad drafts that
# entered the queue before the promote/scrape filters existed: masked/role/
# scraper emails, national chains, and cross-attributed domains. Mirrors the
# operator-side filters (recon/outreach.py) since CHIEF is a separate package.
_SAFE_BAD_PREFIXES = ("noreply", "no-reply", "privacy", "donotreply",
                      "postmaster", "mailer-daemon", "abuse", "spam")
_SAFE_BAD_DOMAINS  = ("council.bbb.org", "bbb.org", "contactout.com", "hunter.io",
                      "rocketreach.co", "apollo.io", "zoominfo.com", "lusha.com")
# (name token, domain token) — name matched on word boundary, domain on substring.
_SAFE_CHAIN_NAME   = ("one hour", "one-hour", "ars rescue", "roto-rooter",
                      "roto rooter", "benjamin franklin", "mr. rooter", "mr rooter",
                      "homeadvisor", "servicemaster", "comfort systems",
                      "sears home", "homeserve")
_SAFE_CHAIN_DOMAIN = ("onehour", "rotorooter", "benjaminfranklin", "mrrooter",
                      "homeadvisor", "servicemaster", "comfortsystems",
                      "searshome", "homeserve")
_SAFE_GENERIC_DOMAINS = {"gmail", "yahoo", "aol", "hotmail", "outlook", "icloud", "mail"}
# Trade/legal/connector words removed by EXACT word match (not substring) when
# extracting the significant tokens of a business name. Geo words (houston, katy,
# texas...) are intentionally kept significant so a cross-attributed domain still
# trips the check. Word-match avoids the " co" ⊂ "conditioning" class of bug.
_SAFE_TRADE_WORDS = {
    "plumbing", "hvac", "air", "heating", "cooling", "services", "service",
    "company", "co", "corp", "ltd", "inc", "llc", "repair", "and", "the", "of",
}
# Off-target org types — never owner-operated contractor prospects.
_SAFE_SCHOOL_WORDS = ("school", "university", "college", "training",
                      "institute", "academy")


def _outreach_safety_reason(business_name: str, email: str) -> str | None:
    """Return a human-readable block reason if this draft should NOT be emailed,
    else None. Final gate before SMTP."""
    e = (email or "").strip().lower()
    n = (business_name or "").strip().lower()

    # 1. Email validity
    if "@" not in e or "*" in e:
        return "invalid/masked email"
    local, _, domain = e.partition("@")
    if any(local.startswith(p) for p in _SAFE_BAD_PREFIXES):
        return "role/no-reply email"
    if any(d in domain for d in _SAFE_BAD_DOMAINS):
        return "scraper/non-prospect domain"

    # 2. Off-target org type (school / training / etc.)
    if any(k in n for k in _SAFE_SCHOOL_WORDS):
        return "off-target (school/training)"

    # 3. National chain — check business name (word boundary) and email domain
    dom_root = domain.split(".")[0]
    if any(re.search(r"\b" + re.escape(c) + r"\b", n) for c in _SAFE_CHAIN_NAME):
        return "national chain"
    if any(t in dom_root for t in _SAFE_CHAIN_DOMAIN):
        return "national chain"

    # 3. Domain consistency — a significant name word should appear in the domain
    #    (generic mailbox providers can't be verified, so they pass)
    tokens = re.split(r"[^a-z0-9]+", n)
    words = [w for w in tokens if w and len(w) >= 3 and w not in _SAFE_TRADE_WORDS]
    if words and dom_root not in _SAFE_GENERIC_DOMAINS \
            and not any(w in dom_root for w in words):
        return f"domain mismatch ({domain})"

    return None


def _chat_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ChatSession:
    """One human conversation session. State only — no execution logic."""

    def __init__(self, session_id: str, tenant_id: str = 'default') -> None:
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.created_at = _chat_now()
        self.last_active = time.time()
        self.messages: list = []
        self.pending_approvals: list = []
        self.linked_run_ids: list = []

    def add_message(self, role: str, content: str, metadata: dict | None = None) -> dict:
        msg = {
            'id': uuid.uuid4().hex[:8],
            'session_id': self.session_id,
            'role': role,
            'content': content,
            'ts': _chat_now(),
            'metadata': metadata or {},
        }
        self.messages.append(msg)
        self.last_active = time.time()
        return msg

    def to_dict(self) -> dict:
        return {
            'session_id': self.session_id,
            'tenant_id': self.tenant_id,
            'created_at': self.created_at,
            'message_count': len(self.messages),
            'pending_approvals': self.pending_approvals,
            'linked_runs': self.linked_run_ids,
        }


# ── Mission readiness + alert dedup (v3.4 Phase 2) ───────────────────────────

_MISSIONS_DIR = os.path.expanduser(
    "~/Zyrcon/operators/cascadia-os-operators/missions"
)
_ALERT_DEDUP_PATH = os.path.expanduser(
    "~/Zyrcon/operators/cascadia-os-operators/core/data/readiness_alerts.json"
)
_ALERT_DEDUP_TTL = 4 * 3600  # suppress same issue for 4 hours


def _load_dedup() -> dict:
    try:
        with open(_ALERT_DEDUP_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_dedup(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_ALERT_DEDUP_PATH), exist_ok=True)
        with open(_ALERT_DEDUP_PATH, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def _should_alert(issue_key: str) -> bool:
    """Returns True (and records timestamp) if not already alerted within 4h."""
    dedup = _load_dedup()
    now   = time.time()
    if now - dedup.get(issue_key, 0) < _ALERT_DEDUP_TTL:
        return False
    dedup[issue_key] = now
    _save_dedup(dedup)
    return True


def _compute_mission_status(manifest: dict, op_readiness: dict) -> tuple[str, str | None]:
    """Return (status, blocking_reason) for one mission manifest.
    status: 'ready' | 'degraded' | 'blocked'"""
    crit_blocked = False
    imp_missing  = False
    blocking     = None
    for req in manifest.get("required_capabilities", []):
        op   = req.get("operator", "")
        cap  = req.get("capability", "")
        crit = req.get("criticality", "critical")
        st   = op_readiness.get(op, {}).get("readiness_status", "unreachable")
        is_ok = st in ("ready", "healthy")
        if not is_ok:
            if crit == "critical":
                crit_blocked = True
                blocking = f"{op.upper()} unavailable"
            elif crit == "important":
                imp_missing = True
    if crit_blocked:
        return "blocked", blocking
    if imp_missing:
        return "degraded", "some capabilities degraded"
    return "ready", None


def _compute_all_missions(op_readiness: dict) -> dict:
    """Load all top-level *.json manifests in missions/ and compute status.
    Returns {mission_id: (status, reason, display_name)}"""
    results: dict = {}
    try:
        for fname in sorted(os.listdir(_MISSIONS_DIR)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(_MISSIONS_DIR, fname)
            try:
                with open(fpath) as f:
                    manifest = json.load(f)
                if "required_capabilities" not in manifest:
                    continue
                mid   = manifest.get("id", fname[:-5])
                name  = manifest.get("name", mid)
                st, reason = _compute_mission_status(manifest, op_readiness)
                results[mid] = (st, reason, name)
            except Exception:
                pass
    except Exception:
        pass
    return results


_HELP_TEXT = (
    "🤖 <b>ZYRCON BOT</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 <b>MISSIONS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "💼 <b>SALES</b>\n\n"
    "  📡 <b>RECON</b>\n"
    "  /recon · /leads · /pipeline\n"
    "  /outreach · /preview · /send_outreach\n"
    "  /approve_all · /followups · /replies\n"
    "  /reactivate · /archive\n"
    "  /recon_start · /recon_stop\n\n"
    "  🎯 <b>SCOUT</b>\n"
    "  /scout · /funnel\n"
    "  /email_approve · /email_skip\n\n"
    "  📧 <b>EMAIL</b>\n"
    "  /inbox_check · /email_status\n\n"
    "  👥 <b>CRM</b>\n"
    "  /crm · /crm_sleep · /crm_wake\n\n"
    "  🎪 <b>DEMO</b>\n"
    "  /demo_status\n\n"
    "💰 <b>FINANCES</b>\n\n"
    "  📋 <b>QUOTE</b>\n"
    "  /quote · /close · /invoice · /review\n\n"
    "📣 <b>MARKETING</b>\n\n"
    "  𝕏 <b>X</b>\n"
    "  /x_approve · /x_skip · /approve_all_x\n"
    "  /x_post_now · /x_status\n\n"
    "  📘 <b>FACEBOOK</b>\n"
    "  /fb_approve · /fb_skip · /approve_all_fb\n"
    "  /fb_post_now · /fb_status\n\n"
    "  📸 <b>INSTAGRAM</b>\n"
    "  /ig_approve · /ig_skip · /approve_all_ig\n"
    "  /ig_post_now · /ig_gen_image · /ig_regen\n"
    "  /clear_image · /ig_status\n\n"
    "  🚀 <b>CAMPAIGNS</b>\n"
    "  /social_generate · /social\n\n"
    "🏃 <b>MANAGEMENT</b>\n\n"
    "  📅 <b>DAILY OPS</b>\n"
    "  /brief · /schedule · /blockers\n"
    "  /eod · /weekly · /missions\n\n"
    "  ⚙️ <b>ORCHESTRATION</b>\n"
    "  /status · /operators · /version\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "📊 <b>REPORTS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "/startup_report · /email_status\n"
    "/token · /token_week · /token_month · /ram\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚙️ <b>SYSTEM</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "/check_credentials · /check_credentials --live\n"
    "/wizard · /help\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🔬 <b>ADVANCED</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "/node_sync · /node_sync_status"
)


class ChiefService:
    """
    CHIEF - Task orchestrator.
    Owns operator selection and BEACON dispatch.
    Does not own execution or session state.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config["components"] if c["name"] == name)

        # Resolve URLs from config port map so no hardcoded ports leak
        port_map = {c["name"]: c["port"] for c in self.config.get("components", [])}
        global CREW_URL, BEACON_URL, MISSION_MANAGER_URL, BELL_URL, OM_URL
        if not os.environ.get("CREW_URL"):
            CREW_URL = f"http://127.0.0.1:{port_map.get('crew', 5100)}"
        if not os.environ.get("BEACON_URL"):
            BEACON_URL = f"http://127.0.0.1:{port_map.get('beacon', 6200)}"
        if not os.environ.get("MISSION_MANAGER_URL"):
            MISSION_MANAGER_URL = f"http://127.0.0.1:{port_map.get('mission_manager', 6207)}"
        if not os.environ.get("BELL_URL"):
            BELL_URL = f"http://127.0.0.1:{port_map.get('bell', 6204)}"
        if not os.environ.get("OM_URL"):
            OM_URL = f"http://127.0.0.1:{port_map.get('operator_manager', 6210)}"

        self.runtime = ServiceRuntime(
            name=name,
            port=component["port"],
            pulse_file=component["pulse_file"],
            log_dir=self.config["log_dir"],
        )

        self.runtime.register_route("GET",  "/health",          self.health)
        self.runtime.register_route("POST", "/task",            self.handle_task)
        self.runtime.register_route("GET",  "/tasks/{task_id}", self.get_task)
        self.runtime.register_route("GET",  "/tasks",           self.list_tasks)
        self.runtime.register_route("POST", "/outreach/approval/request",
                                    self._handle_outreach_approval_request)
        self.runtime.register_route("GET",  "/api/startup_report",
                                    self.startup_report)

        # ── Chat / BELL-absorbed routes ──────────────────────────────────────
        db_path = self.config.get('database_path', './data/runtime/cascadia.db')
        self._chat_wf_runtime = WorkflowRuntime(db_path)
        self._chat_wf_definitions = self._build_chat_workflow_definitions()
        self._chat_sessions: Dict[str, ChatSession] = {}
        self._chat_lock = threading.Lock()

        self.runtime.register_route('POST', '/session/start',   self.chat_start_session)
        self.runtime.register_route('POST', '/message',         self.chat_receive_message)
        self.runtime.register_route('POST', '/approve',         self.chat_receive_approval)
        self.runtime.register_route('POST', '/approve/edit',    self.chat_edit_and_approve)
        self.runtime.register_route('GET',  '/sessions',        self.chat_list_sessions)
        self.runtime.register_route('POST', '/session/history', self.chat_get_history)
        self.runtime.register_route('POST', '/callback',        self.handle_callback)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            "ok": True,
            "service": "chief",
            "role": "orchestrator",
            "version": _VERSION,
            "crew_url": CREW_URL,
            "beacon_url": BEACON_URL,
        }

    # ------------------------------------------------------------------
    # Startup report
    # ------------------------------------------------------------------

    def startup_report(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/startup_report — build full health report and push to Telegram owner."""
        global _last_startup_report_at
        now = time.time()
        if now - _last_startup_report_at < _STARTUP_REPORT_COOLDOWN:
            self.runtime.logger.debug("startup_report: cooldown active, skipping Telegram")
            return 200, {"ok": True, "skipped": True, "reason": "cooldown"}
        _last_startup_report_at = now
        report = self._build_startup_report()
        try:
            _http_post(
                f"{TELEGRAM_URL}/send",
                {"chat_id": "1535010257", "text": report},
                timeout=10,
            )
        except Exception as exc:
            self.runtime.logger.warning(
                "CHIEF startup_report Telegram post failed: %s", exc
            )
        return 200, {"ok": True, "report": report}

    def _build_startup_report(self) -> str:
        """Build a formatted system health report for post-boot Telegram delivery."""
        import socket
        from datetime import datetime, timezone, timedelta

        try:
            from zoneinfo import ZoneInfo
            ct  = ZoneInfo("America/Chicago")
            now = datetime.now(timezone.utc).astimezone(ct).strftime(
                "%Y-%m-%d %H:%M CDT"
            )
        except Exception:
            now = datetime.now(timezone(timedelta(hours=-5))).strftime(
                "%Y-%m-%d %H:%M CDT"
            )

        def _check(port: int) -> bool:
            for path in ("/health", "/api/health"):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{path}", timeout=2
                    ) as r:
                        body = r.read().decode()
                        if any(k in body for k in
                               ("ok", "ready", "healthy", "online", "degraded")):
                            return True
                except Exception:
                    pass
            if port == 4222:
                try:
                    s = socket.create_connection(("127.0.0.1", 4222), timeout=2)
                    s.close()
                    return True
                except Exception:
                    pass
            return False

        CORE = {
            "NATS":   4222, "CREW":   5100, "BEACON": 6200,
            "CHIEF":  6211, "PRISM":  6300, "LLM":    8080,
        }
        OPERATORS = {
            "RECON":       8002, "EMAIL":      8010, "PULSE":    8016,
            "SCHEDULER":   8014, "SCOUT":      7002, "TELEGRAM": 9000,
            "SOCIAL":      8011, "QUOTE-BRIEF": 8006,
        }

        issues: list[str] = []
        lines  = ["🚀 CASCADIA OS — STARTUP REPORT", now, ""]

        lines.append("CORE SERVICES")
        for name, port in CORE.items():
            ok   = _check(port)
            icon = "✅" if ok else "❌"
            lines.append(f"  {icon} {name:<10} ({port})")
            if not ok:
                issues.append(f"{name} ({port})")

        lines.append("")
        lines.append("OPERATORS")
        for name, port in OPERATORS.items():
            ok   = _check(port)
            icon = "✅" if ok else "❌"
            lines.append(f"  {icon} {name:<10} ({port})")
            if not ok:
                issues.append(f"{name} ({port})")

        try:
            pipeline = self._read_pipeline_stats()
            if pipeline:
                lines += [
                    "",
                    "PIPELINE",
                    f"  Leads ready:    {pipeline.get('total', 0)}",
                    f"  Follow-ups due: {pipeline.get('followups_due', 0)}",
                    f"  Contacted:      {pipeline.get('contacted', 0)}",
                ]
        except Exception:
            pass

        try:
            recon = self._read_recon_stats()
            icon, label = self._recon_icon_and_label(recon)
            lines += [
                "",
                "RECON",
                f"  {icon} {label} — cycle {recon.get('cycle', 0)}",
                f"  Task:  {recon.get('task', '?')}",
                f"  Leads: {recon.get('master_rows', 0)}",
            ]
        except Exception:
            pass

        lines.append("")
        if issues:
            lines.append(f"⚠️ {len(issues)} issue(s):")
            for issue in issues:
                lines.append(f"  • {issue}")
        else:
            lines.append("✅ All systems nominal")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Task dispatch
    # ------------------------------------------------------------------

    def handle_task(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        task_id  = uuid.uuid4().hex
        req      = TaskRequest.from_dict(payload)
        chat_id  = str(req.metadata.get("chat_id") or "")

        self.runtime.logger.info(
            "CHIEF received task [%s] from %s via %s",
            task_id, req.sender, req.source_channel,
        )

        # ── Persistent keyboard button intercept ─────────────────────────────
        # ReplyKeyboardMarkup sends plain text — must be caught before parse_command.
        _task_text = req.task.strip()
        if _task_text == "/start":
            _tg_send(
                chat_id,
                "👋 Welcome to Zyrcon Command Center\n\n"
                "Use the buttons below or tap 🏠 Menu to navigate by mission.",
                _persistent_keyboard(),
            )
            return 200, {"ok": True, "task_id": task_id, "silent": True}
        if _task_text == "🏠 Menu":
            self._handle_menu(chat_id)
            return 200, {"ok": True, "task_id": task_id, "silent": True}
        if _task_text == "✅ Approve All":
            if chat_id:
                n_out = len(self._load_outreach_approvals())
                n_q   = len(self._load_approvals())
                if n_out + n_q == 0:
                    reply_text = "✅ Nothing pending — queue is empty."
                else:
                    threading.Thread(
                        target=self._approve_all_and_notify, args=(chat_id,),
                        daemon=True, name="chief-kb-approveall",
                    ).start()
                    reply_text = f"⚙️ Approving {n_out + n_q} pending item(s)... Stand by."
            else:
                reply_text = "❌ chat_id required."
            return 200, TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target="/approve_all",
                reply_text=reply_text,
            ).to_dict()
        if _task_text == "📥 Inbox":
            _tg_send(chat_id, self._inbox_check(1))
            return 200, {"ok": True, "task_id": task_id, "silent": True}

        # ── Step 0 — Slash command fast-path (100% accuracy, no LLM) ────────
        # /contact_N must come FIRST — not in COMMANDS dict, would otherwise be "unknown"
        contact_cmd = parse_contact_command(req.task)
        if contact_cmd is not None:
            reply_text = self._mark_lead_contacted(
                contact_cmd["row_id"], contact_cmd["status"], chat_id
            )
            return 200, TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target="/contact",
                reply_text=reply_text,
            ).to_dict()

        quote_cmd = parse_quote_command(req.task)
        if quote_cmd is not None:
            if not chat_id:
                return 200, TaskResponse(
                    ok=False, task_id=task_id, selected_type="none",
                    reply_text="❌ /quote_N requires a Telegram chat_id.",
                ).to_dict()
            threading.Thread(
                target=self._generate_quote_for_lead,
                args=(quote_cmd["row_id"], quote_cmd["description"], chat_id),
                daemon=True,
                name=f"chief-quote-{task_id[:8]}",
            ).start()
            return 200, TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target="/quote",
                reply_text=f"📄 Drafting proposal for lead #{quote_cmd['row_id']}. Stand by...",
            ).to_dict()

        approval_cmd = parse_approval_command(req.task)
        if approval_cmd is not None:
            reply_text = self._handle_approval(
                approval_cmd["action"], approval_cmd["row_id"], chat_id
            )
            return 200, TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target=f"/{approval_cmd['action']}",
                reply_text=reply_text,
            ).to_dict()

        parsed_cmd = parse_command(req.task)
        if parsed_cmd is not None:
            cmd = parsed_cmd["command"]
            if parsed_cmd.get("unknown"):
                # Dynamic /lead_NNN[_action] commands are not in the static
                # COMMANDS registry but are valid — intercept before rejecting.
                if cmd.startswith("/lead_"):
                    parts   = cmd[6:].split("_")   # strip "/lead_"
                    lead_id = parts[0]
                    action  = parts[1] if len(parts) > 1 else "view"
                    if lead_id.isdigit():
                        return 200, TaskResponse(
                            ok=True, task_id=task_id,
                            selected_type="status", selected_target=cmd,
                            reply_text=self._lead_command(int(lead_id), action),
                        ).to_dict()
                if cmd.startswith("/wp_approve_"):
                    draft_id = cmd[len("/wp_approve_"):]
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._wp_command(draft_id, "approve"),
                    ).to_dict()
                if cmd.startswith("/wp_skip_"):
                    draft_id = cmd[len("/wp_skip_"):]
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._wp_command(draft_id, "skip"),
                    ).to_dict()
                reply_text = (
                    f"I don't know that command: {cmd}\n"
                    f"Try /help to see what's available."
                )
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="none",
                    reply_text=reply_text,
                ).to_dict()
            if cmd.startswith("/performance"):
                # /performance, /performance_morning, /performance_noon,
                # /performance_evening, /performance_kpis, /performance_history
                suffix = cmd[len("/performance"):].lstrip("_") or "morning"
                if suffix in ("kpis", "snapshot"):
                    action = "snapshot"
                elif suffix == "history":
                    action = "history"
                elif suffix == "noon":
                    action = "noon"
                elif suffix == "evening":
                    action = "evening"
                else:
                    action = "morning"
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._performance_command(action),
                ).to_dict()
            if cmd.startswith("/token") or cmd.startswith("/meter"):
                # /token, /token_day, /token_week, /token_month, /token_all
                # /meter, /meter_today, /meter_week, /meter_month, /meter_all
                if cmd.startswith("/token"):
                    suffix = cmd[len("/token"):]
                else:
                    suffix = cmd[len("/meter"):]
                period = suffix.lstrip("_") or "default"
                if period == "today":
                    period = "default"
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._meter_command(period),
                ).to_dict()
            if cmd.startswith("/code"):
                code_args = (cmd[5:].lstrip("_").strip() + " " +
                             parsed_cmd.get("args", "")).strip()
                reply = self._code_command(code_args, chat_id=chat_id)
                if not reply:
                    reply = "⏳ Creating project — you'll receive a proposal shortly..."
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=reply,
                ).to_dict()
            if cmd == "/demo_status":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._demo_command("status"),
                ).to_dict()
            if cmd.startswith("/demo_start"):
                prospect_id = cmd[len("/demo_start"):].strip()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._demo_command("start", prospect_id),
                ).to_dict()
            if cmd.startswith("/demo_reset"):
                prospect_id = cmd[len("/demo_reset"):].strip()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._demo_command("reset", prospect_id),
                ).to_dict()
            if cmd.startswith("/demo_close"):
                prospect_id = cmd[len("/demo_close"):].strip()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._demo_command("close", prospect_id),
                ).to_dict()
            if cmd == "/help":
                _tg_send(chat_id, _HELP_TEXT, parse_mode="HTML")
                return 200, {"ok": True, "task_id": task_id, "silent": True}
            if cmd == "/wizard":
                _tg_send(chat_id, self._cmd_wizard(), parse_mode="HTML")
                return 200, {"ok": True, "task_id": task_id, "silent": True}
            if cmd == "/operators":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=build_operators_text(OPERATOR_CATALOG),
                ).to_dict()
            if cmd == "/status":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._status_summary(),
                ).to_dict()
            if cmd == "/missions":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._missions_summary(),
                ).to_dict()
            if cmd == "/pipeline":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._pipeline_snapshot(),
                ).to_dict()
            if cmd == "/recon_start":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._recon_start(),
                ).to_dict()
            if cmd == "/recon_stop":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._recon_stop(),
                ).to_dict()
            if cmd == "/archive":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._archive_leads(),
                ).to_dict()
            if cmd == "/startup_report":
                report = self._build_startup_report()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=report,
                ).to_dict()
            if cmd.startswith("/check_credentials"):
                live = "--live" in cmd
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._check_credentials_report(live),
                ).to_dict()
            if cmd == "/ram":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._ram_status(),
                ).to_dict()
            if cmd == "/version" or cmd == "/about":
                # /about replaces /version — the richer release card.
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._about_info(),
                ).to_dict()
            if cmd == "/drift":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._drift_info(),
                ).to_dict()
            if cmd == "/update":
                # Owner-only — /update stops + restarts ALL services.
                if str(chat_id) != str(self._OWNER_CHAT):
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text="🔒 /update is owner-only.",
                    ).to_dict()
                try:
                    _cr_path = os.path.normpath(os.path.join(
                        os.path.dirname(__file__), "..", "..", "data", "runtime", "current_release.json"))
                    _cr = json.load(open(_cr_path))
                    _v, _nr = _cr.get("version", "?"), _cr.get("node_role", "?")
                except Exception:
                    _v, _nr = "?", "?"
                _tg_send(
                    chat_id,
                    ("⚠️ Update Cascadia OS?\n\nThis will STOP all services, pull "
                     f"the pinned release, and restart.\n\nCurrent: v{_v} ({_nr})"),
                    reply_markup={"inline_keyboard": [[
                        {"text": "✅ Yes, update", "callback_data": "cmd_update_confirm"},
                        {"text": "❌ Cancel",       "callback_data": "cmd_update_cancel"},
                    ]]},
                )
                return 200, {"ok": True, "task_id": task_id, "silent": True}
            if cmd == "/email_status":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._email_status(),
                ).to_dict()
            if cmd == "/x":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._x_command(parsed_cmd.get("args", "")),
                ).to_dict()
            if cmd == "/fb":
                _fb_text = (parsed_cmd.get("args", "") or "").strip()
                _fb_reply = self._direct_post("facebook", _fb_text)
                if _fb_reply == "":
                    _fb_reply = "Usage: /fb <text> — posts directly to Facebook (or send an image first)."
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=_fb_reply,
                ).to_dict()
            if cmd == "/ig":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._direct_post("instagram", parsed_cmd.get("args", "")),
                ).to_dict()
            if cmd == "/post":
                _all_text = (parsed_cmd.get("args", "") or "").strip()
                _all_reply = (self._direct_post_all(_all_text) if _all_text
                              else "Usage: /post <text> — posts to X, Facebook, and Instagram.")
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=_all_reply,
                ).to_dict()
            if cmd == "/x_approve":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._x_command("approve"),
                ).to_dict()
            if cmd == "/x_skip":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._x_command("skip"),
                ).to_dict()
            if cmd == "/crm":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._crm_command(parsed_cmd.get("args", "")),
                ).to_dict()
            if cmd == "/crm_sleep":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._crm_command("sleep"),
                ).to_dict()
            if cmd == "/crm_wake":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._crm_command("wake"),
                ).to_dict()
            if cmd == "/x_status":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._x_command("status"),
                ).to_dict()
            if cmd == "/fb_approve":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._fb_command("approve"),
                ).to_dict()
            if cmd == "/fb_skip":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._fb_command("skip"),
                ).to_dict()
            if cmd == "/fb_status":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._fb_command("status"),
                ).to_dict()
            if cmd == "/ig_approve":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._ig_command("approve"),
                ).to_dict()
            if cmd == "/ig_skip":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._ig_command("skip"),
                ).to_dict()
            if cmd == "/approve_all_x":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._approve_all_platform("x"),
                ).to_dict()
            if cmd == "/approve_all_fb":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._approve_all_platform("facebook"),
                ).to_dict()
            if cmd == "/approve_all_ig":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._approve_all_platform("instagram"),
                ).to_dict()
            if cmd in ("/ig_gen_image", "/ig_generate"):
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._ig_gen_image_command(),
                ).to_dict()
            if cmd == "/ig_regen":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._ig_regen_command(),
                ).to_dict()
            if cmd == "/email_approve":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._email_approve_command(),
                ).to_dict()
            if cmd == "/email_skip":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._email_skip_command(),
                ).to_dict()
            if cmd == "/ig_status":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._ig_command("status"),
                ).to_dict()
            if cmd == "/clear_image":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._clear_image_command(),
                ).to_dict()
            if cmd in ("/social", "/campaign"):
                topic = cmd_parsed.get("args", "").strip() or "daily social campaign"
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="social", selected_target="social",
                    reply_text=self._social_start(topic, chat_id),
                ).to_dict()
            if cmd == "/social_generate":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="social", selected_target="social_generate",
                    reply_text=self._social_generate_command(),
                ).to_dict()
            if cmd in ("/x_post_now", "/fb_post_now", "/ig_post_now"):
                platform = cmd.split("_")[0].lstrip("/")  # x / fb / ig
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="social", selected_target=cmd.lstrip("/"),
                    reply_text=self._post_now_command(platform),
                ).to_dict()
            if cmd == "/menu":
                self._handle_menu(chat_id)
                return 200, {"ok": True, "task_id": task_id, "silent": True}
            # ── Mission trigger commands ──────────────────────────────────────
            if cmd == "/brief":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("run_work", "morning_brief", chat_id),
                ).to_dict()
            if cmd == "/schedule":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("run_work", "schedule_check", chat_id),
                ).to_dict()
            if cmd == "/blockers":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("run_work", "blocker_watch", chat_id),
                ).to_dict()
            if cmd == "/eod":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("run_work", "end_of_day_report", chat_id),
                ).to_dict()
            if cmd == "/weekly":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("run_work", "weekly_summary", chat_id),
                ).to_dict()
            if cmd == "/review":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("run_work", "review_request", chat_id),
                ).to_dict()
            if cmd == "/reactivate":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("find_work", "lead_reactivation", chat_id),
                ).to_dict()
            if cmd == "/close":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("win_work", "close_the_job", chat_id),
                ).to_dict()
            if cmd == "/invoice":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("win_work", "get_paid", chat_id),
                ).to_dict()
            if cmd == "/funnel":
                return 200, TaskResponse(
                    ok=True, task_id=task_id, selected_type="status", selected_target=cmd,
                    reply_text=self._trigger_mission("win_work", "sales_funnel", chat_id),
                ).to_dict()
            if cmd == "/preview":
                if _is_prism(req.metadata):
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._preview_sync_for_prism(),
                    ).to_dict()
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /preview requires a Telegram chat_id.",
                    ).to_dict()
                threading.Thread(
                    target=self._preview_and_notify,
                    args=(chat_id,),
                    daemon=True,
                    name=f"chief-preview-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text="👁 Generating preview — stand by...",
                ).to_dict()
            if cmd == "/approve_all":
                if _is_prism(req.metadata):
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._approve_all_sync_for_prism(),
                    ).to_dict()
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /approve_all requires a Telegram chat_id.",
                    ).to_dict()
                n_out = len(self._load_outreach_approvals())
                n_q   = len(self._load_approvals())
                if n_out + n_q == 0:
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text="✅ Nothing pending — queue is empty.",
                    ).to_dict()
                threading.Thread(
                    target=self._approve_all_and_notify,
                    args=(chat_id,),
                    daemon=True,
                    name=f"chief-approveall-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=f"⚙️ Approving {n_out + n_q} pending item(s)... Stand by.",
                ).to_dict()
            if cmd == "/outreach":
                # Optional limit: "/outreach" → 20 (default), "/outreach 5" → 5,
                # capped at 10. Non-numeric arg falls back to the default.
                limit = 20
                arg = (parsed_cmd.get("args") or "").strip()
                if arg:
                    try:
                        limit = min(max(int(arg.split()[0]), 1), 50)
                    except (ValueError, IndexError):
                        limit = 3
                if _is_prism(req.metadata):
                    return 200, TaskResponse(
                        ok=True, task_id=task_id,
                        selected_type="status", selected_target=cmd,
                        reply_text=self._outreach_sync_for_prism(limit),
                    ).to_dict()
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /outreach requires a Telegram chat_id.",
                    ).to_dict()
                threading.Thread(
                    target=self._run_outreach_and_notify,
                    args=(chat_id, limit),
                    daemon=True,
                    name=f"chief-outreach-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=f"📋 Pulling top {limit} lead(s) for outreach. Stand by...",
                ).to_dict()
            if cmd == "/send_outreach":
                if not chat_id:
                    return 200, TaskResponse(
                        ok=False, task_id=task_id, selected_type="none",
                        reply_text="❌ /send_outreach requires a Telegram chat_id.",
                    ).to_dict()
                threading.Thread(
                    target=self._run_send_outreach_and_notify,
                    args=(chat_id,),
                    daemon=True,
                    name=f"chief-send-outreach-{task_id[:8]}",
                ).start()
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text="📤 Drafting and sending to top leads. Stand by...",
                ).to_dict()
            if cmd == "/followups":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._followups_snapshot(),
                ).to_dict()
            if cmd == "/replies":
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._replies_snapshot(),
                ).to_dict()
            if cmd == "/inbox_check":
                n = 1
                arg = (parsed_cmd.get("args") or "").strip()
                if arg:
                    try:
                        n = min(max(int(arg.split()[0]), 1), 20)
                    except (ValueError, IndexError):
                        n = 1
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=self._inbox_check(n),
                ).to_dict()
            if cmd in ("/node_sync", "/node_sync_status"):
                try:
                    import urllib.request as _ur, json as _j
                    _url = ("http://localhost:7015/api/sync"
                            if cmd == "/node_sync"
                            else "http://localhost:7015/api/sync/status")
                    with _ur.urlopen(_url, timeout=5) as _r:
                        _d = _j.loads(_r.read())
                    reply = f"🔄 Grid sync: {_d}"
                except Exception as _e:
                    reply = (f"⚠️ Grid not reachable: {_e}\n"
                             f"Start Grid first or check port 7015.")
                return 200, TaskResponse(
                    ok=True, task_id=task_id,
                    selected_type="status", selected_target=cmd,
                    reply_text=reply,
                ).to_dict()
            if parsed_cmd["operator"]:
                target = parsed_cmd["operator"]
                self.runtime.logger.info(
                    "CHIEF command fast-path: %s → operator=%s", cmd, target
                )
                raw_result = self._dispatch_via_beacon(req, target, task_id)
                reply_text = self._format_reply(target, raw_result)
                append_history(chat_id, "user",      req.task)
                append_history(chat_id, "assistant", reply_text[:500])
                set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=req.task)
                ok = "error" not in raw_result
                return 200, TaskResponse(
                    ok=ok, task_id=task_id,
                    selected_type="operator", selected_target=target,
                    reply_text=reply_text, raw_result=raw_result,
                ).to_dict()

        # ── A. Status commands — fast-path, not recorded in history ──────────
        selection = select_target(req.task, CREW_URL)
        if selection["selected_type"] == "status":
            reply_text = self._handle_status_command(selection["target"])
            resp = TaskResponse(
                ok=True, task_id=task_id,
                selected_type="status", selected_target=selection["target"],
                reply_text=reply_text,
            )
            return 200, resp.to_dict()

        # Record user message now (after status gate — we don't track /status etc.)
        append_history(chat_id, "user", req.task)

        # ── A2. Repeat fast-path — "do it again" without LLM ────────────────
        _REPEAT_PHRASES = frozenset({
            "do it again", "run it again", "run again", "repeat that",
            "again", "repeat", "same again", "do that again",
        })
        if req.task.lower().strip() in _REPEAT_PHRASES and chat_id:
            last = get_last_action(chat_id)
            if last and last.get("action") == "dispatch_operator" and last.get("target"):
                target = last["target"]
                original = last.get("original_task") or req.task
                self.runtime.logger.info(
                    "CHIEF repeat fast-path: repeating %s with original task=%r", target, original[:40]
                )
                # Replay with the original task so the operator recognizes the request
                repeat_req = TaskRequest(
                    task=original,
                    source_channel=req.source_channel,
                    reply_channel=req.reply_channel,
                    sender=req.sender,
                    tenant_id=req.tenant_id,
                    metadata=req.metadata,
                )
                raw_result = self._dispatch_via_beacon(repeat_req, target, task_id)
                reply_text = self._format_reply(target, raw_result)
                append_history(chat_id, "assistant", reply_text[:500])
                set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=original)
                ok = "error" not in raw_result
                return 200, TaskResponse(
                    ok=ok, task_id=task_id,
                    selected_type="operator", selected_target=target,
                    reply_text=reply_text, raw_result=raw_result,
                ).to_dict()

        # ── B. Keyword fast-path (confidence >= 0.90, no LLM needed) ─────────
        kw_confidence = selection.get("confidence", 0.0)
        if selection["ok"] and kw_confidence >= 0.90:
            self.runtime.logger.info(
                "CHIEF keyword fast-path: target=%s confidence=%.2f",
                selection["target"], kw_confidence,
            )
            target     = selection["target"]
            raw_result = self._dispatch_via_beacon(req, target, task_id)
            reply_text = self._format_reply(target, raw_result)
            append_history(chat_id, "assistant", reply_text[:500])
            set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=req.task)
            ok   = "error" not in raw_result
            resp = TaskResponse(
                ok=ok, task_id=task_id,
                selected_type="operator", selected_target=target,
                reply_text=reply_text, raw_result=raw_result,
            )
            return 200, resp.to_dict()

        # ── C. LLM intent classifier ── pass stored history ───────────────────
        history  = get_history(chat_id)
        decision = classify_intent(req.task, history, chat_id=chat_id)

        # ── D. Validate against catalog + policy ──────────────────────────────
        decision  = validate_routing_decision(decision)
        validated = decision.action not in ("conversation",) or decision.confidence > 0

        # ── E. Apply confidence thresholds ────────────────────────────────────
        if decision.action == "dispatch_operator":
            if decision.confidence < CONFIDENCE_DISPATCH:
                decision.action = "ask_clarification"
                if not decision.question:
                    decision.question = (
                        "I think I know what you need but I'm not sure — "
                        "could you give me a bit more detail?"
                    )
            elif CONFIDENCE_CLARIFY <= decision.confidence < CONFIDENCE_DISPATCH:
                decision.action = "ask_clarification"

        # ── Audit log ─────────────────────────────────────────────────────────
        self.runtime.logger.info(
            'INTENT_ROUTER | msg="%s" | action=%s | target=%s | '
            'confidence=%.2f | reason="%s" | validated=%s | final_action=%s',
            req.task[:50],
            decision.action,
            decision.target,
            decision.confidence,
            decision.reason[:60],
            "pass" if validated else "fail",
            decision.action,
        )

        # ── F. Dispatch operator ──────────────────────────────────────────────
        if decision.action == "dispatch_operator":
            target     = decision.target
            raw_result = self._dispatch_via_beacon(req, target, task_id)
            reply_text = self._format_reply(target, raw_result)
            append_history(chat_id, "assistant", reply_text[:500])
            set_last_action(chat_id, "dispatch_operator", target, reply_text[:300], original_task=req.task)
            ok   = "error" not in raw_result
            resp = TaskResponse(
                ok=ok, task_id=task_id,
                selected_type="operator", selected_target=target,
                reply_text=reply_text, raw_result=raw_result,
            )
            return 200, resp.to_dict()

        # ── G. Ask clarification ──────────────────────────────────────────────
        if decision.action == "ask_clarification":
            question = decision.question or "Could you give me a bit more detail?"
            append_history(chat_id, "assistant", question)
            set_last_action(chat_id, "conversation", None, question[:200])
            resp = TaskResponse(
                ok=True, task_id=task_id,
                selected_type="none",
                reply_text=question,
                raw_result={"reason": decision.reason, "missing": decision.missing_inputs},
            )
            return 200, resp.to_dict()

        # ── H. Multi-step plan ────────────────────────────────────────────────
        if decision.action == "multi_step_plan":
            targets    = decision.targets or []
            plan_lines = ["Here's my plan:"]
            for i, t in enumerate(targets, 1):
                plan_lines.append(f"  {i}. {t}")
            plan_lines.append("\nStarting with the first step now...")
            plan_summary = "\n".join(plan_lines)

            if targets:
                first_target = targets[0]
                raw_result   = self._dispatch_via_beacon(req, first_target, task_id)
                first_reply  = self._format_reply(first_target, raw_result)
                reply_text   = plan_summary + "\n\n" + first_reply
            else:
                reply_text = plan_summary

            append_history(chat_id, "assistant", reply_text[:500])
            set_last_action(chat_id, "dispatch_operator", targets[0] if targets else None, reply_text[:300])
            resp = TaskResponse(
                ok=True, task_id=task_id,
                selected_type="operator",
                selected_target=targets[0] if targets else None,
                reply_text=reply_text,
            )
            return 200, resp.to_dict()

        # ── I. Conversation / fallback ────────────────────────────────────────
        reply_text = intelligent_fallback(req.task, req.source_channel)
        append_history(chat_id, "assistant", reply_text[:500])
        set_last_action(chat_id, "conversation", None, reply_text[:200])
        resp = TaskResponse(
            ok=True, task_id=task_id,
            selected_type="none",
            reply_text=reply_text,
            raw_result={"intent_reason": decision.reason},
        )
        return 200, resp.to_dict()

    def get_task(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            "ok": False,
            "message": "Durable task state not yet implemented in v1",
        }

    def list_tasks(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            "ok": False,
            "message": "Task history not yet implemented in v1",
        }

    # ------------------------------------------------------------------
    # BEACON dispatch
    # ------------------------------------------------------------------

    def _wake_and_wait(self, target: str) -> bool:
        """Ask OM to wake a sleeping operator; poll its health until ready or timeout."""
        try:
            body = json.dumps({"reason": "dispatch_requested"}).encode()
            req = urllib.request.Request(
                f"{OM_URL}/operators/{target}/wake",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                result = json.loads(r.read().decode())
            if not result.get("ok"):
                self.runtime.logger.warning(
                    "CHIEF: OM rejected wake for %s: %s", target, result
                )
                return False
        except Exception as exc:
            self.runtime.logger.warning(
                "CHIEF: OM unreachable for wake(%s): %s", target, exc
            )
            return False

        # Get the operator's port so we can poll its health directly (faster than
        # waiting for OM's 30s monitoring cycle to mark it running)
        op_port = None
        try:
            with urllib.request.urlopen(
                f"{OM_URL}/operators/{target}/status", timeout=3
            ) as r:
                op_port = json.loads(r.read().decode()).get("port")
        except Exception:
            pass

        self.runtime.logger.info(
            "CHIEF: waking %s (port %s) — waiting up to %ds",
            target, op_port, _WAKE_WAIT,
        )
        deadline = time.time() + _WAKE_WAIT
        while time.time() < deadline:
            if op_port:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{op_port}/api/health", timeout=2
                    ) as r:
                        if r.status == 200:
                            time.sleep(2)  # allow CREW registration to complete
                            return True
                except Exception:
                    pass
            time.sleep(1)

        self.runtime.logger.warning(
            "CHIEF: %s did not become healthy within %ds", target, _WAKE_WAIT
        )
        return False

    def _version_info(self) -> str:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:6210/operators",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as _r:
                ops = json.loads(_r.read().decode())
            op_count = len(ops) if isinstance(ops, list) else len(ops.get("operators", ops))
        except Exception:
            op_count = "?"
        return (
            f"⚙️ Cascadia OS v{_VERSION}\n"
            f"───────────────────────\n"
            f"Operators: {op_count} registered\n"
            f"Node:      zyrcon-node-a\n"
            f"Build:     CalVer {_VERSION}\n"
            f"Repo:      github.com/zyrconlabs/cascadia-os"
        )

    def _about_info(self) -> str:
        """Release card from current_release.json + live CREW count + uptime.
        Replaces the old /version output."""
        import os as _os, time as _time
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        cr_path = _os.path.normpath(_os.path.join(
            _os.path.dirname(__file__), "..", "..", "data", "runtime", "current_release.json"))
        try:
            cr = json.load(open(cr_path))
        except FileNotFoundError:
            return "⚠ current_release.json not found — run /update first."
        except Exception as exc:
            return f"⚠ /about error: {exc}"
        repos = cr.get("repos", {})
        core_h = (repos.get("cascadia-os") or "unknown")[:7]
        ops_h  = (repos.get("cascadia-os-operators") or "unknown")[:7]
        ent_h  = (repos.get("enterprise") or "unknown")[:7]
        installed = cr.get("installed_at", "")
        try:
            installed_fmt = _dt.fromisoformat(installed).astimezone(
                _ZI("America/Chicago")).strftime("%Y-%m-%d %I:%M %p CT")
        except Exception:
            installed_fmt = installed or "unknown"
        try:
            with urllib.request.urlopen("http://127.0.0.1:5100/crew", timeout=3) as _r:
                crew = json.loads(_r.read().decode())
            if isinstance(crew, list):
                crew_count = len(crew)
            elif isinstance(crew.get("operators"), list):
                crew_count = len(crew["operators"])
            else:
                crew_count = crew.get("crew_size", "?")
        except Exception:
            crew_count = "?"
        try:
            up = _time.time() - _os.path.getmtime(cr_path)
            uptime_str = f"{int(up // 3600)}h {int((up % 3600) // 60)}m"
        except Exception:
            uptime_str = "unknown"
        return (
            f"ℹ️ Cascadia OS v{cr.get('version','unknown')} ({cr.get('channel','stable')})\n"
            f"───────────────────────\n"
            f"Node:       {cr.get('node_role','unknown')}\n"
            f"Core:       {core_h}\n"
            f"Operators:  {ops_h}\n"
            f"Enterprise: {ent_h}\n"
            f"Installed:  {installed_fmt}\n"
            f"Services:   {crew_count} registered\n"
            f"Uptime:     {uptime_str}"
        )

    def _drift_info(self) -> str:
        """Compare the deployed release (current_release.json) against the target
        (releases/stable.json) — overall version + per-repo commit drift.
        Paths are __file__-relative (portable across nodes)."""
        import os as _os
        cr_path = _os.path.normpath(_os.path.join(
            _os.path.dirname(__file__), "..", "..", "data", "runtime", "current_release.json"))
        st_path = _os.path.normpath(_os.path.join(
            _os.path.dirname(__file__), "..", "..", "..",
            "operators", "cascadia-os-operators", "releases", "stable.json"))
        try:
            cr = json.load(open(cr_path))
            st = json.load(open(st_path))
        except FileNotFoundError as exc:
            return f"⚠ /drift — file not found: {exc}"
        except Exception as exc:
            return f"⚠ /drift error: {exc}"
        cr_ver  = cr.get("version", "unknown")
        st_ver  = st.get("version", "unknown")
        in_sync = cr_ver == st_ver
        cr_repos, st_repos = cr.get("repos", {}), st.get("repos", {})
        lines = [
            f"{'✅' if in_sync else '⚠️'} Drift Report",
            "───────────────────────",
            f"Installed: {cr_ver}",
            f"Stable:    {st_ver}",
            "",
        ]
        for key, label in (("cascadia-os", "Core"),
                           ("cascadia-os-operators", "Operators"),
                           ("enterprise", "Enterprise")):
            cr_h = (cr_repos.get(key) or "unknown")[:7]
            st_e = st_repos.get(key, {})
            st_h = ((st_e.get("commit", "unknown") if isinstance(st_e, dict) else st_e) or "unknown")[:7]
            match = cr_h == st_h
            lines.append(f"{'✅' if match else '⚠️'} {label:<10} {cr_h} {'=' if match else '≠'} {st_h}")
        lines.append("")
        lines.append("✅ Node is up to date" if in_sync else "⚠️ Node is behind — run /update")
        return "\n".join(lines)

    def _run_update_and_notify(self, chat_id: str) -> None:
        """Launch update_node.sh detached, poll its log for the terminal state,
        report back. Runs in a background thread so the callback returns at once."""
        import subprocess as _sp, os as _os, time as _time
        script = _os.path.expanduser(
            "~/Zyrcon/operators/cascadia-os-operators/scripts/update_node.sh")
        log = "/tmp/update_node_telegram.log"
        try:
            with open(log, "w") as _lf:
                _sp.Popen(["bash", script], stdout=_lf, stderr=_sp.STDOUT,
                          start_new_session=True)
        except Exception as exc:
            _tg_send(chat_id, f"⚠️ Could not launch update: {exc}")
            return
        deadline = _time.time() + 300   # poll up to 5 minutes
        while _time.time() < deadline:
            _time.sleep(10)
            try:
                body = open(log).read()
            except Exception:
                continue
            if "=== SYNC OK" in body:
                _tg_send(chat_id, "✅ Update complete\n\n" + self._about_info())
                return
            if "ROLLBACK COMPLETE" in body:
                _tg_send(chat_id, "⚠️ Update failed — rolled back. Check /tmp/update_node_telegram.log")
                return
        _tg_send(chat_id, "⏳ Update still running — check /about when it settles.")

    def _email_status(self) -> str:
        import csv as _csv
        from datetime import datetime, timezone, timedelta
        from pathlib import Path
        OPERATORS = os.path.expanduser("~/Zyrcon/operators/cascadia-os-operators")
        now       = datetime.now(timezone.utc)
        today     = now.date()
        week_ago  = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        entries: list = []
        sent_path = Path(OPERATORS) / "email/data/sent.json"
        try:
            entries = json.loads(sent_path.read_text()) if sent_path.exists() else []
        except Exception:
            pass

        def _date(e: dict):
            try:
                return datetime.fromisoformat(e.get("sent_at", "")).date()
            except Exception:
                return None

        def _acct(e: dict) -> str:
            fe = e.get("from_email", e.get("from", ""))
            sl = str(e.get("slot", ""))
            if "a00.pro" in fe or sl == "1":
                return "email-02 (zyrcon@a00.pro)"
            return "email-01 (hello@zyrcon.ai)"

        def _stats(subset: list) -> dict:
            sent     = [e for e in subset if e.get("status") == "sent"]
            failed   = [e for e in subset if e.get("status") == "failed"]
            outreach = [e for e in sent if e.get("type", "outreach") != "followup"]
            followup = [e for e in sent if e.get("type") == "followup"]
            by_acct: dict = {}
            for e in sent:
                lbl = _acct(e)
                by_acct[lbl] = by_acct.get(lbl, 0) + 1
            return {"sent": len(sent), "failed": len(failed),
                    "outreach": len(outreach), "followup": len(followup),
                    "by_acct": by_acct}

        def _bucket(since):
            return [e for e in entries if (_d := _date(e)) and _d >= since]

        td = _stats(_bucket(today))
        wk = _stats(_bucket(week_ago))
        mo = _stats(_bucket(month_ago))

        # Live pool counters
        pool: dict = {}
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8010/api/pool/status",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as _r:
                pool = json.loads(_r.read().decode())
        except Exception:
            pass

        # Lead pipeline from CSV
        total_leads = contacted = followup_q = 0
        csv_path = Path(OPERATORS) / "recon/output/outreach_ready.csv"
        try:
            with open(csv_path) as _f:
                rows = list(_csv.DictReader(_f))
            total_leads = len(rows)
            contacted   = sum(1 for r in rows if r.get("contacted", "").lower() == "yes")
            followup_q  = sum(1 for r in rows
                              if int(r.get("followup_count", "0") or "0") > 0)
        except Exception:
            pass

        def _acct_lines(by_acct: dict) -> str:
            if not by_acct:
                return "    (no per-account data)"
            return "\n".join(f"    {lbl}: {n}" for lbl, n in sorted(by_acct.items()))

        def _pool_lines() -> str:
            s0 = pool.get("slot_0", {})
            s1 = pool.get("slot_1", {})
            if not s0 and not s1:
                return "  (pool counters unavailable)"
            lines = []
            for slot_key, label in [("slot_0", "email-01"), ("slot_1", "email-02")]:
                s = pool.get(slot_key, {})
                if s:
                    pct = round(s.get("sent", 0) / max(s.get("cap", 1), 1) * 100)
                    lines.append(
                        f"  {label} {s.get('email','?'):30s} "
                        f"{s.get('sent',0)}/{s.get('cap',0)} ({pct}%)"
                    )
            return "\n".join(lines) if lines else "  (pool counters unavailable)"

        return (
            f"📊 Email Status\n"
            f"{'─'*32}\n\n"
            f"📅 TODAY\n"
            f"  Sent:     {td['sent']}\n"
            f"  Failed:   {td['failed']}\n"
            f"  Outreach: {td['outreach']}  |  Follow-up: {td['followup']}\n"
            f"  By account:\n{_acct_lines(td['by_acct'])}\n\n"
            f"📅 THIS WEEK (7 days)\n"
            f"  Sent:     {wk['sent']}\n"
            f"  Failed:   {wk['failed']}\n"
            f"  Outreach: {wk['outreach']}  |  Follow-up: {wk['followup']}\n"
            f"  By account:\n{_acct_lines(wk['by_acct'])}\n\n"
            f"📅 THIS MONTH (30 days)\n"
            f"  Sent:     {mo['sent']}\n"
            f"  Failed:   {mo['failed']}\n"
            f"  Outreach: {mo['outreach']}  |  Follow-up: {mo['followup']}\n"
            f"  By account:\n{_acct_lines(mo['by_acct'])}\n\n"
            f"📬 LIVE POOL (today)\n"
            f"{_pool_lines()}\n\n"
            f"👥 LEAD PIPELINE\n"
            f"  Total leads:   {total_leads}\n"
            f"  Contacted:     {contacted}\n"
            f"  Follow-up due: {followup_q}\n\n"
            f"📌 Data: sent.json ({len(entries)} records)"
        )

    def _crm_command(self, args: str) -> str:
        sub = args.strip().lower()
        if sub == "sleep":
            try:
                body = json.dumps({"reason": "manual"}).encode()
                req = urllib.request.Request(
                    f"{OM_URL}/operators/crm/sleep",
                    data=body, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    result = json.loads(r.read().decode())
                if result.get("ok"):
                    return "💤 CRM sleeping — port :8015 released."
                return f"⚠️ OM rejected sleep: {result}"
            except Exception as exc:
                return f"❌ CRM sleep failed: {exc}"
        if sub == "wake":
            ok = self._wake_and_wait("crm")
            return "✅ CRM awake — :8015 healthy." if ok else "⚠️ CRM wake timed out."
        return "Usage: /crm_sleep | /crm_wake"

    def _x_command(self, args: str, post_id=None) -> str:
        """Post to X via the Social operator (single brain → Buffer → X).

          /x <text>    → post immediately
          /x status    → recent posts + Buffer state
          /x <text> at HH:MM | tomorrow | in N hours → scheduling (not yet enabled)
        """
        import re as _re
        social_url = "http://localhost:8011"
        body = (args or "").strip()

        # ── APPROVE / SKIP (approval gate for scheduled drafts) ──
        if body.lower() == "approve":
            try:
                _body = json.dumps({"post_id": post_id}).encode() if post_id else b"{}"
                req = urllib.request.Request(
                    f"{social_url}/api/x/approve", data=_body, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    res = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return "Nothing waiting for approval. Drafts arrive 9:00/13:00/17:00 CT."
                return f"❌ Approve failed: HTTP {e.code}"
            except Exception as exc:
                return f"❌ Approve error: {str(exc)[:80]}"
            if not res.get("success"):
                return f"❌ {res.get('error', 'approve failed')}"
            tag    = "⚠️ Posted (simulated)" if res.get("simulated") else "✅ Posted to X"
            rating = res.get("rating", "")
            stars  = f" · ⭐ {rating}/10" if rating else ""
            return (f"{tag}\n"
                    f"Post {res.get('position','?')} · {res.get('char_count','?')} chars{stars}\n"
                    f"{res.get('remaining','?')} remaining in queue\n"
                    f"https://x.com/beast_popovich")
        if body.lower() == "skip":
            try:
                _body = json.dumps({"post_id": post_id}).encode() if post_id else b"{}"
                req = urllib.request.Request(
                    f"{social_url}/api/x/skip", data=_body, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    res = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return "Nothing waiting to skip."
                return f"❌ Skip failed: HTTP {e.code}"
            except Exception as exc:
                return f"❌ Skip error: {str(exc)[:80]}"
            if not res.get("success"):
                return f"❌ {res.get('error', 'skip failed')}"
            msg = f"⏭ Skipped post {res.get('skipped','?')}. Pending: {res.get('pending','?')}"
            nxt = res.get("next_post")
            if nxt:
                msg += f"\nNext: post {nxt.get('position','?')} ({nxt.get('char_count','?')} chars)"
            return msg

        # ── STATUS ────────────────────────────────────────────
        if body.lower() in ("", "status"):
            try:
                with urllib.request.urlopen(f"{social_url}/api/x/status", timeout=8) as r:
                    data = json.loads(r.read().decode())
            except Exception as exc:
                return f"❌ X status unavailable: {str(exc)[:80]}"
            recent = data.get("recent_posts", [])
            buf_mode = (data.get("buffer") or {}).get("mode", "?")
            lines = ["📊 X Posting Status\n"]
            if recent:
                lines.append("Recent posts:")
                for p in recent[:3]:
                    ts = (p.get("created_at") or "?")[:16]
                    txt = (p.get("content") or "?")[:40]
                    sim = " (sim)" if p.get("simulated") else ""
                    lines.append(f"  • {ts} — {txt}…{sim}")
            else:
                lines.append("No posts yet.")
            lines.append(f"\nBuffer: {buf_mode}   Limit: 280 chars/post")
            return "\n".join(lines)

        # ── SCHEDULING (not yet enabled) ──────────────────────
        if (_re.search(r"\s+at\s+\d{1,2}:\d{2}\s*$", body, _re.I)
                or _re.search(r"\s+tomorrow\s*$", body, _re.I)
                or _re.search(r"\s+in\s+\d+\s+hours?\s*$", body, _re.I)):
            return ("⏳ Scheduled posting isn't enabled yet — only immediate "
                    "posting is available.\nDrop the time and resend to post now.")

        # ── IMMEDIATE POST (attaches any pending image) ───────
        return self._direct_post("x", body)

    def _fb_command(self, action: str, post_id=None) -> str:
        """Facebook queue actions via the Social operator (approval gate).

        action: approve | skip | status
        """
        social_url = "http://localhost:8011"
        if action == "status":
            try:
                with urllib.request.urlopen(f"{social_url}/api/fb/queue_status", timeout=8) as r:
                    d = json.loads(r.read().decode())
            except Exception as exc:
                return f"❌ Facebook status unavailable: {str(exc)[:80]}"
            lines = ["📘 Facebook Queue Status\n",
                     f"Pending: {d.get('pending','?')}   Posted: {d.get('posted','?')}"]
            nxt = d.get("next_post")
            if nxt:
                lines.append(f"Next: post {nxt.get('position','?')} "
                             f"({nxt.get('char_count','?')} chars)")
            lines.append("\nSchedule: 7:45 / 13:15 / 17:15 CT")
            return "\n".join(lines)

        path = "/api/fb/approve" if action == "approve" else "/api/fb/skip"
        try:
            _body = json.dumps({"post_id": post_id}).encode() if post_id else b"{}"
            req = urllib.request.Request(
                f"{social_url}{path}", data=_body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ("Nothing waiting for Facebook approval. Drafts arrive "
                        "7:45/13:15/17:15 CT." if action == "approve"
                        else "Nothing waiting to skip.")
            return f"❌ Facebook {action} failed: HTTP {e.code}"
        except Exception as exc:
            return f"❌ Facebook {action} error: {str(exc)[:80]}"
        if not res.get("success"):
            return f"❌ {res.get('error', action + ' failed')}"
        if action == "approve":
            tag    = "⚠️ Posted (simulated)" if res.get("simulated") else "✅ Posted to Facebook"
            rating = res.get("rating", "")
            stars  = f" · ⭐ {rating}/10" if rating else ""
            return (f"{tag}\n"
                    f"Post {res.get('position','?')} · {res.get('char_count','?')} chars{stars}\n"
                    f"{res.get('remaining','?')} remaining in queue")
        msg = f"⏭ Skipped post {res.get('skipped','?')}. Pending: {res.get('pending','?')}"
        nxt = res.get("next_post")
        if nxt:
            msg += f"\nNext: post {nxt.get('position','?')} ({nxt.get('char_count','?')} chars)"
        return msg

    def _ig_command(self, action: str) -> str:
        """Instagram queue actions via Social (approval gate, image required).

        action: approve | skip | status
        """
        social_url = "http://localhost:8011"
        if action == "status":
            try:
                with urllib.request.urlopen(f"{social_url}/api/ig/queue_status", timeout=8) as r:
                    d = json.loads(r.read().decode())
            except Exception as exc:
                return f"❌ Instagram status unavailable: {str(exc)[:80]}"
            lines = ["📸 Instagram Queue\n",
                     f"Pending: {d.get('pending','?')}   Posted: {d.get('posted','?')}"]
            if d.get('pending', 0) == 0:
                lines.append("\nQueue empty — load with POST /api/ig/load_queue")
            nxt = d.get("next_post")
            if nxt:
                lines.append(f"Next: post {nxt.get('position','?')} "
                             f"({nxt.get('char_count','?')} chars)")
            lines.append("\n⚠️ Image required — send image first, then /ig_approve")
            lines.append("Schedule: 8:00 / 12:15 / 17:30 CT")
            return "\n".join(lines)

        path = "/api/ig/approve" if action == "approve" else "/api/ig/skip"
        try:
            req = urllib.request.Request(
                f"{social_url}{path}", data=b"{}", method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("error", "")
            except Exception:
                pass
            if e.code == 400 and "image" in detail.lower():
                return ("❌ Instagram requires an image.\nSend image(s) to this chat "
                        "first, then tap /ig_approve.")
            if e.code == 404:
                return ("Nothing waiting for Instagram approval. Drafts arrive "
                        "8:00/12:15/17:30 CT." if action == "approve"
                        else "Nothing waiting to skip.")
            return f"❌ Instagram {action} failed: HTTP {e.code} {detail[:60]}"
        except Exception as exc:
            return f"❌ Instagram {action} error: {str(exc)[:80]}"
        if not res.get("success"):
            return f"❌ {res.get('error', action + ' failed')}"
        if action == "approve":
            tag    = "⚠️ Posted (simulated)" if res.get("simulated") else "✅ Posted to Instagram"
            rating = res.get("rating", "")
            stars  = f" · ⭐ {rating}/10" if rating else ""
            return (f"{tag}\n"
                    f"Post {res.get('position','?')} · {res.get('char_count','?')} chars{stars}\n"
                    f"{res.get('remaining','?')} remaining in queue")
        msg = f"⏭ Skipped post {res.get('skipped','?')}. Pending: {res.get('pending','?')}"
        nxt = res.get("next_post")
        if nxt:
            msg += f"\nNext: post {nxt.get('position','?')} ({nxt.get('char_count','?')} chars)"
        return msg

    def _post_now_command(self, platform: str) -> str:
        """POST /api/{platform}/post_now → force-draft next pending post to Telegram."""
        label = {"x": "X", "fb": "Facebook", "ig": "Instagram"}.get(platform, platform)
        try:
            res = _http_post(f"http://localhost:8011/api/{platform}/post_now", {}, timeout=20)
        except Exception as exc:
            return f"❌ {label} post_now error: {str(exc)[:80]}"
        if res.get("queued") == 0 or res.get("message", "").startswith("queue empty"):
            return f"📭 {label} queue is empty — nothing to draft."
        if res.get("status") == "awaiting_approval" and res.get("reminder"):
            pos = res.get("position", "?")
            return f"⏳ {label} post {pos} is already awaiting your approval."
        if res.get("success") or res.get("ok"):
            pos = res.get("position", "?")
            chars = res.get("char_count", "")
            suffix = f" ({chars}/280)" if platform == "x" and chars else ""
            return f"📝 {label} post {pos} drafted to Telegram{suffix}.\nUse /{platform}_approve to send."
        return f"⚠️ {label}: {res.get('error', 'unexpected response')}"

    def _social_generate_command(self) -> str:
        """POST /api/generate_batch → fresh social posts across X + FB + IG.
        This call is SYNCHRONOUS (blocks while social runs the LLM batch), so we
        hold the GPU-yield flag for its duration → RECON skips qualify meanwhile."""
        _set_gpu_yield(True)
        try:
            try:
                res = _http_post(
                    "http://localhost:8011/api/generate_batch",
                    {"campaign_duration": "daily", "platforms": ["x", "facebook", "instagram"]},
                    timeout=120,
                )
            except Exception as exc:
                return f"❌ Generate failed: {str(exc)[:80]}"
            if not res.get("ok"):
                return f"❌ {res.get('error', 'generate_batch failed')}"
            gen    = res.get("generated", {})
            depths = res.get("queue_depth", {})
            total  = res.get("total", 0)
            mode   = res.get("mode", "template")
            lines  = [f"✅ Generated {total} new posts ({mode})"]
            for slug, label in (("x", "X"), ("facebook", "Facebook"), ("instagram", "Instagram")):
                n     = gen.get(slug, 0)
                depth = depths.get(slug, "?")
                lines.append(f"  {label:<12} +{n}  ({depth} pending)")
            return "\n".join(lines)
        finally:
            _set_gpu_yield(False)

    def _ig_gen_image_command(self) -> str:
        """POST /api/ig/gen_image → LLM prompt + Pollinations download + Telegram photo preview."""
        try:
            res = _http_post("http://localhost:8011/api/ig/gen_image", {}, timeout=125)
        except Exception as exc:
            return f"❌ Image gen error: {str(exc)[:80]}"
        if not res.get("ok"):
            return f"❌ {res.get('error', 'gen_image failed')}"
        return (f"✅ Image preview sent below 👇\n"
                f"Post {res.get('position','?')} — {res.get('prompt','')[:60]}")

    def _ig_regen_command(self) -> str:
        """POST /api/ig/regen → new Pollinations seed for the same post."""
        try:
            res = _http_post("http://localhost:8011/api/ig/regen", {}, timeout=125)
        except Exception as exc:
            return f"❌ Image regen error: {str(exc)[:80]}"
        if not res.get("ok"):
            return f"❌ {res.get('error', 'regen failed')}"
        return (f"✅ New image preview sent below 👇\n"
                f"Post {res.get('position','?')} — {res.get('prompt','')[:60]}")

    def _x_gen_image_command(self, post_id=None) -> str:
        """POST /api/x/gen_image → Pollinations image for the awaiting X post;
        preview appears in Telegram with [Post with Image / New Image / Skip]."""
        try:
            res = _http_post("http://localhost:8011/api/x/gen_image",
                             {"post_id": post_id} if post_id else {}, timeout=125)
        except Exception as exc:
            return f"❌ X image gen error: {str(exc)[:80]}"
        if not res.get("ok"):
            return f"❌ {res.get('error', 'gen_image failed')}"
        return (f"✅ X image preview sent below 👇\n"
                f"Post {res.get('position','?')} — {res.get('prompt','')[:60]}")

    def _fb_gen_image_command(self, post_id=None) -> str:
        """POST /api/fb/gen_image → Pollinations image for the awaiting FB post."""
        try:
            res = _http_post("http://localhost:8011/api/fb/gen_image",
                             {"post_id": post_id} if post_id else {}, timeout=125)
        except Exception as exc:
            return f"❌ Facebook image gen error: {str(exc)[:80]}"
        if not res.get("ok"):
            return f"❌ {res.get('error', 'gen_image failed')}"
        return (f"✅ Facebook image preview sent below 👇\n"
                f"Post {res.get('position','?')} — {res.get('prompt','')[:60]}")

    def _approve_all_platform(self, platform: str) -> str:
        """POST /api/{x|fb|ig}/approve_all → move all awaiting_approval posts to pending."""
        slug = {"x": "x", "facebook": "fb", "instagram": "ig"}[platform]
        try:
            req = urllib.request.Request(
                f"http://localhost:8011/api/{slug}/approve_all",
                data=b"{}", method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode())
        except Exception as exc:
            return f"❌ approve_all_{slug} error: {str(exc)[:80]}"
        n = res.get("approved", 0)
        if n == 0:
            return f"Nothing awaiting approval for {platform}."
        return res.get("message", f"✅ {n} {platform} posts returned to queue.")

    def _clear_image_command(self) -> str:
        """Discard any images pending in the Telegram operator's memory."""
        try:
            body = json.dumps({"chat_id": "1535010257"}).encode()
            req = urllib.request.Request(
                "http://localhost:9000/api/media/clear", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                n = json.loads(r.read().decode()).get("removed", 0)
            return f"🗑 Cleared {n} pending image(s)."
        except Exception as exc:
            return f"❌ Could not clear images: {str(exc)[:80]}"

    def _lead_command(self, lead_id: int, action: str = "view") -> str:
        """Handle /lead_NNN[_action] hot lead commands.

        action: view (default) | contacted | skip
        """
        crm = "http://localhost:8015"

        if action == "view":
            try:
                with urllib.request.urlopen(
                        f"{crm}/api/contact/{lead_id}", timeout=5) as r:
                    d = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return f"❌ Lead {lead_id} not found (HTTP {e.code})"
            except Exception as exc:
                return f"❌ CRM error: {str(exc)[:80]}"
            if not d.get("success"):
                return f"❌ {d.get('error', 'not found')}"
            c     = d["contact"]
            name  = c.get("contact_name") or "Unknown"
            bname = c.get("business_name") or "Unknown"
            email = c.get("email") or "?"
            phone = c.get("phone") or "not collected"
            btype = c.get("business_type") or ""
            city  = c.get("city") or ""
            src   = c.get("hot_lead_source") or ""
            stage = c.get("conversation_stage") or 0
            booked = c.get("appointment_booked", 0)
            miss  = [f for f in ("contact_name", "phone") if not c.get(f)]
            loc   = f" · {city}" if city else ""
            bt    = f" ({btype})" if btype else ""
            return (
                f"📋 Lead #{lead_id}\n"
                f"{'─'*20}\n"
                f"👤 {name}\n"
                f"🏢 {bname}{bt}{loc}\n"
                f"📧 {email}\n"
                f"📱 {phone}\n"
                f"{'─'*20}\n"
                f"Source: {src}  Stage: {stage}/5\n"
                f"Booked: {'✓' if booked else '✗'}\n"
                f"Missing: {', '.join(miss) if miss else 'none'}\n"
                f"{'─'*20}\n"
                f"/lead_{lead_id}_contacted\n"
                f"/lead_{lead_id}_skip"
            )

        if action == "contacted":
            try:
                req = urllib.request.Request(
                    f"{crm}/api/crm/contact/{lead_id}/mark_contacted",
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5):
                    pass
                return f"✅ Lead #{lead_id} marked contacted\nReminders stopped."
            except Exception as exc:
                return f"❌ CRM error: {str(exc)[:80]}"

        if action == "skip":
            try:
                req = urllib.request.Request(
                    f"{crm}/api/crm/contact/{lead_id}/dismiss",
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5):
                    pass
                return f"🚫 Lead #{lead_id} dismissed\nReminders stopped permanently."
            except Exception as exc:
                return f"❌ CRM error: {str(exc)[:80]}"

        if action == "snooze":
            try:
                req = urllib.request.Request(
                    f"{crm}/api/crm/contact/{lead_id}/snooze",
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5):
                    pass
                return f"⏸ Lead #{lead_id} snoozed 1 hour\nReminders resume after 1h."
            except Exception as exc:
                return f"❌ CRM error: {str(exc)[:80]}"

        return (f"❌ Unknown action: {action}\n"
                f"/lead_{lead_id}           → view\n"
                f"/lead_{lead_id}_contacted → mark contacted\n"
                f"/lead_{lead_id}_skip      → dismiss permanently\n"
                f"/lead_{lead_id}_snooze    → snooze 1 hour")

    def _email_approve_command(self) -> str:
        """Approve and send the oldest pending Scout email reply draft."""
        email_op = "http://localhost:8010"
        try:
            # Check what's pending first
            with urllib.request.urlopen(
                    f"{email_op}/api/email/reply_drafts", timeout=5) as r:
                data = json.loads(r.read())
        except Exception as exc:
            return f"❌ Email operator unreachable: {str(exc)[:80]}"

        if data.get('count', 0) == 0:
            return "📭 No pending Scout reply drafts."

        draft = data['drafts'][0]
        to    = draft.get('to', '?')
        subj  = draft.get('subject', '?')[:55]
        stage = draft.get('stage', '?')
        miss  = draft.get('missing', [])

        try:
            req = urllib.request.Request(
                f"{email_op}/api/email/approve_reply",
                data=b'{}', method='POST',
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                result = json.loads(r.read())
        except Exception as exc:
            return f"❌ Approve failed: {str(exc)[:80]}"

        if result.get('ok'):
            miss_str = f"  Missing collected: {', '.join(miss)}" if miss else ""
            return (
                f"✅ Scout reply sent\n"
                f"To:      {to}\n"
                f"Subject: {subj}\n"
                f"Stage:   {stage}/5{miss_str}"
            )
        return f"❌ Send failed: {result.get('error', 'unknown')}"

    def _email_skip_command(self) -> str:
        """Skip (discard) the oldest pending Scout email reply draft."""
        email_op = "http://localhost:8010"
        try:
            with urllib.request.urlopen(
                    f"{email_op}/api/email/reply_drafts", timeout=5) as r:
                data = json.loads(r.read())
        except Exception as exc:
            return f"❌ Email operator unreachable: {str(exc)[:80]}"

        if data.get('count', 0) == 0:
            return "📭 No pending Scout reply drafts."

        draft = data['drafts'][0]
        try:
            req = urllib.request.Request(
                f"{email_op}/api/email/skip_reply",
                data=b'{}', method='POST',
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                result = json.loads(r.read())
        except Exception as exc:
            return f"❌ Skip failed: {str(exc)[:80]}"

        remaining = data.get('count', 1) - 1
        return (
            f"🗑 Scout reply discarded\n"
            f"To: {draft.get('to', '?')}\n"
            f"Remaining drafts: {remaining}"
        )

    def _wp_command(self, draft_id: str, action: str = "approve") -> str:
        """Handle /wp_approve_ID (publish) and /wp_skip_ID (delete draft)."""
        WP = "http://localhost:9008"
        endpoint = (
            f"{WP}/api/wordpress/approve/{draft_id}"
            if action == "approve"
            else f"{WP}/api/wordpress/skip/{draft_id}"
        )
        try:
            req = urllib.request.Request(
                endpoint, data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                result = json.loads(r.read())
        except Exception as exc:
            return f"❌ WordPress connector error: {str(exc)[:80]}"

        if result.get("success"):
            if action == "approve":
                url = result.get("public_url", "")
                return f"✅ WordPress post published\n🔗 {url}"
            else:
                return f"⏭ WordPress draft deleted.\nDraft {draft_id} removed."
        reason = result.get("reason", "")
        if reason == "credentials_missing":
            return "⚠️ WP credentials not yet configured in VAULT."
        error = result.get("error", "Unknown error")
        return f"❌ WP error: {error[:80]}"

    def _demo_command(self, action: str = "status", chat_id: str = "") -> str:
        """Handle /demo_start /demo_status /demo_reset /demo_close."""
        DEMO = "http://localhost:8029"

        def _post(path: str, body: dict) -> dict:
            payload = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{DEMO}{path}", data=payload,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())

        try:
            if action == "start":
                if not chat_id:
                    return "Usage: /demo_start [prospect_chat_id]"
                d = _post("/api/demo/start", {"chat_id": chat_id})
                if d.get("success"):
                    return f"🚀 Demo started for {chat_id}\nPhase 1 greeting sent."
                return f"❌ {d.get('error', 'Failed')}"

            if action == "status":
                req = urllib.request.Request(f"{DEMO}/api/demo/sessions", method="GET")
                with urllib.request.urlopen(req, timeout=5) as r:
                    d = json.loads(r.read())
                sessions = d.get("sessions", [])
                if not sessions:
                    return "📊 No active demo sessions"
                lines = [f"📊 Active demos: {len(sessions)}"]
                for s in sessions[:5]:
                    name  = s.get("name", "") or "collecting..."
                    biz   = s.get("business_name", "") or "?"
                    phase = s.get("phase", 1)
                    lines.append(
                        f"  👤 {name} · {biz}\n"
                        f"     Phase {phase}/5 · {s.get('industry', '?')}"
                    )
                return "\n".join(lines)

            if action == "reset":
                if not chat_id:
                    return "Usage: /demo_reset [prospect_chat_id]"
                _post(f"/api/demo/reset/{chat_id}", {})
                return f"🔄 Demo reset for {chat_id}"

            if action == "close":
                if not chat_id:
                    return "Usage: /demo_close [prospect_chat_id]"
                _post(f"/api/demo/close/{chat_id}", {"converted": True})
                return f"✅ Demo closed (converted) for {chat_id}"

        except Exception as e:
            return f"❌ Demo error: {str(e)[:100]}"

        return (
            "Demo commands:\n"
            "/demo_status\n"
            "/demo_start [chat_id]\n"
            "/demo_reset [chat_id]\n"
            "/demo_close [chat_id]"
        )

    def _meter_command(self, period: str = "default") -> str:
        """Handle /token and /meter [day|week|month|all]. Fetches token report."""
        ODOM = "http://localhost:8028"
        valid = {"day", "week", "month", "all", "default"}
        if period not in valid:
            period = "default"
        try:
            req = urllib.request.Request(
                f"{ODOM}/api/tokens/format?period={period}",
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            text = d.get("text", "")
            if not text:
                return "❌ Meter returned empty report"
            TELEGRAM   = "http://localhost:9000/send"
            OWNER_CHAT = "1535010257"
            payload = json.dumps(
                {"chat_id": OWNER_CHAT, "text": text, "parse_mode": "HTML"}
            ).encode()
            req2 = urllib.request.Request(
                TELEGRAM, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req2, timeout=5)
            return "✓ Meter sent"
        except Exception as e:
            return f"❌ Meter error: {str(e)[:80]}"

    def _performance_command(self, action: str = "morning") -> str:
        """Handle /performance* commands. Calls performance operator at :8030."""
        PERF = "http://localhost:8030"
        endpoints = {
            "morning":  f"{PERF}/api/performance/morning",
            "noon":     f"{PERF}/api/performance/noon",
            "evening":  f"{PERF}/api/performance/evening",
            "snapshot": f"{PERF}/api/performance/snapshot",
            "history":  f"{PERF}/api/performance/history",
        }
        url = endpoints.get(action, endpoints["morning"])
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            if action in ("morning", "noon", "evening"):
                sent = data.get("sent", False)
                return ("✅ Performance report sent to Telegram" if sent
                        else "✅ Check complete — on track, no alert needed")
            if action == "snapshot":
                kpis = data.get("kpis", {})
                lines = ["📊 <b>Performance KPIs</b>\n"]
                labels = {
                    "leads_researched": "Leads",
                    "emails_sent":      "Emails sent",
                    "hot_leads":        "Hot leads",
                    "token_cost_usd":   "Token cost",
                    "demo_sessions":    "Demo sessions",
                    "operator_uptime":  "Operators up",
                }
                for k, label in labels.items():
                    v = kpis.get(k, "—")
                    if k == "token_cost_usd" and isinstance(v, (int, float)):
                        v = f"${v:.4f}"
                    lines.append(f"  {label}: {v}")
                return "\n".join(lines)
            if action == "history":
                days = data.get("days", [])
                if not days:
                    return "No KPI history yet."
                lines = ["📈 <b>Performance — Last 7 Days</b>\n"]
                for d in days:
                    cost = d.get("token_cost_usd", 0)
                    lines.append(
                        f"  {d.get('snap_date','?')}  "
                        f"leads={d.get('leads_researched',0)}  "
                        f"emails={d.get('emails_sent',0)}  "
                        f"cost=${cost:.2f}"
                    )
                return "\n".join(lines)
            return "✅ Done"
        except Exception as e:
            return f"❌ Performance operator error: {str(e)[:80]}"

    def _code_command(self, args: str = "", chat_id: str = None) -> str:
        """Handle /code — create Code operator project or show status."""
        JR = "http://localhost:8004"
        chat_id = chat_id or self._OWNER_CHAT
        args = (args or "").strip()

        if args.lower() == "list":
            try:
                with urllib.request.urlopen(f"{JR}/api/projects", timeout=5) as r:
                    d = json.loads(r.read())
                projects = d.get("projects", [])
                if not projects:
                    return ("No projects yet.\nUse /code &lt;describe what you need&gt; "
                            "to start one.")
                icons = {"done": "✅", "executing": "⚙️",
                         "awaiting_approval": "⏳", "cancelled": "🗑", "error": "❌"}
                lines = ["📁 <b>Recent Projects</b>\n"]
                for p in projects[:8]:
                    icon = icons.get(p.get("status", ""), "📋")
                    name = p["folder_name"].replace(p["project_num"] + "-", "")
                    lines.append(f"{icon} {p['project_num']} — {name} [{p['status']}]")
                return "\n".join(lines)
            except Exception as exc:
                return f"❌ Code operator not reachable: {exc}"

        m = re.match(r"^status\s+(\d{4})$", args, re.I)
        if m:
            project_num = m.group(1)
            try:
                payload = json.dumps({"action": "get", "project_num": project_num}).encode()
                req = urllib.request.Request(
                    f"{JR}/api/task", data=payload, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    d = json.loads(r.read())
                if not d:
                    return f"Project {project_num} not found."
                return (f"📁 <b>Project {project_num}</b>\n"
                        f"Status: {d.get('status')}\n"
                        f"Folder: {d.get('folder_name')}\n"
                        f"Created: {(d.get('created_at') or '')[:16]}")
            except Exception as exc:
                return f"❌ Error: {exc}"

        m_edit = re.match(r"^edit\s+(\d{4})\s+(.+)$", args, re.I | re.S)
        if m_edit:
            project_num  = m_edit.group(1)
            adjustment   = m_edit.group(2).strip()
            try:
                payload = json.dumps({"adjustment": adjustment}).encode()
                req = urllib.request.Request(
                    f"{JR}/api/project/{project_num}/edit",
                    data=payload, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=45) as r:
                    json.loads(r.read())
                return f"✏️ Project {project_num} plan updated. Revised proposal sent."
            except Exception as exc:
                return f"❌ Edit failed: {exc}"

        if not args:
            return (
                "👨‍💻 <b>Code</b>\n\n"
                "Usage:\n"
                "/code &lt;describe what you need&gt;\n"
                "/code list — recent projects\n"
                "/code status NNNN — check project\n"
                "/code edit NNNN &lt;adjustments&gt; — revise plan\n\n"
                "Example: /code Sort my customer list alphabetically"
            )

        # Create new project — post to Code operator async
        try:
            payload = json.dumps({
                "action":   "create_project",
                "request":  args,
                "chat_id":  str(chat_id),
                "files":    {},
            }).encode()
            req = urllib.request.Request(
                f"{JR}/api/task", data=payload, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                json.loads(r.read())
            return ""  # Operator sends proposal directly to Telegram
        except Exception as exc:
            return f"❌ Code operator not reachable: {exc}"

    _OWNER_CHAT = "1535010257"
    _DIRECT_EP = {"x": "/api/x/post", "facebook": "/api/fb/post",
                  "instagram": "/api/ig/post"}
    _DIRECT_LABEL = {"x": "@beast_popovich", "facebook": "Facebook",
                     "instagram": "Instagram"}

    def _direct_post(self, platform: str, text: str, clear_after: bool = True,
                     image_urls: list | None = None) -> str:
        """Post immediately to a platform (no queue, no gate).

        Attaches any image(s) pending in the Telegram operator. Because Buffer
        deletes the files it receives, we pass COPIES (so a later /post platform
        still has them) and clear the originals after, unless clear_after=False.
        image_urls: pre-hosted, publicly fetchable URL(s) (e.g. Telegram CDN).
        When supplied (by /post's single re-host), the local pending fetch is
        skipped and the URL(s) are passed straight to social → social skips its
        own per-platform re-host (avoids 3× inject latency + 3× echo).
        Returns a reply string, or '' to signal 'no content' (caller shows usage).
        """
        import os as _os, uuid as _uuid, shutil
        text = (text or "").strip()
        image_urls = image_urls or []

        # Fetch pending images from the Telegram operator. Skipped when a
        # pre-hosted image_urls list is supplied (CHIEF /post re-hosts once).
        orig_paths = []
        if not image_urls:
            try:
                with urllib.request.urlopen(
                    f"http://localhost:9000/api/media/pending?chat_id={self._OWNER_CHAT}",
                    timeout=5) as r:
                    orig_paths = json.loads(r.read().decode()).get("image_paths", []) or []
            except Exception:
                pass

        if platform == "instagram" and not orig_paths and not image_urls and not text:
            return ("❌ Instagram needs a caption or an image.\nSend /ig <caption> "
                    "(image auto-generated), or send an image first then /ig.")
        if not text and not orig_paths and not image_urls:
            return ""  # nothing to post → caller shows usage
        if platform == "x" and text and len(text) > 280:
            return f"❌ Too long for X — {len(text)}/280. Trim and resend."

        # Per-platform copies so Buffer's delete doesn't starve other platforms.
        copies = []
        for src in orig_paths:
            try:
                dst = f"/tmp/zyrcon_media/{_uuid.uuid4().hex}{_os.path.splitext(src)[1]}"
                shutil.copy2(src, dst)
                copies.append(dst)
            except Exception as exc:
                log.warning("direct_post image copy failed %s: %s", src, exc)

        try:
            _bd = {"content": text, "image_paths": copies,
                   "source": "direct_command"}
            if image_urls:
                _bd["image_urls"] = image_urls
            body = json.dumps(_bd).encode()
            req = urllib.request.Request(
                f"http://localhost:8011{self._DIRECT_EP[platform]}", data=body,
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            for c in copies:
                try:
                    _os.remove(c)
                except Exception:
                    pass
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("error", "")
            except Exception:
                pass
            return f"❌ {platform.title()}: {detail[:80] or ('HTTP ' + str(e.code))}"
        except Exception as exc:
            for c in copies:
                try:
                    _os.remove(c)
                except Exception:
                    pass
            return f"❌ {platform.title()} error: {str(exc)[:70]}"

        if clear_after and orig_paths:
            try:
                cb = json.dumps({"chat_id": self._OWNER_CHAT}).encode()
                urllib.request.urlopen(urllib.request.Request(
                    "http://localhost:9000/api/media/clear", data=cb, method="POST",
                    headers={"Content-Type": "application/json"}), timeout=5)
            except Exception:
                pass

        if not res.get("success"):
            return f"❌ {platform.title()}: {str(res.get('error', 'failed'))[:80]}"
        tag = "⚠️ Posted (simulated)" if res.get("simulated") else "✅ Posted to"
        _nimg = len(copies) or len(image_urls)
        note = f" + {_nimg} image(s)" if _nimg else ""
        chars = f"{len(text)} chars" if text else "image only"
        return f"{tag} {self._DIRECT_LABEL[platform]}\n{chars}{note}"

    def _direct_post_all(self, text: str) -> str:
        """Post to X + Facebook + Instagram at once (/post). Image optional;
        Instagram is skipped with a note if no image is pending."""
        # Re-host the attached image ONCE on Telegram CDN (was 3× — social
        # re-hosted per platform, adding ~15-45s latency + 3× chat echo). Pass
        # the resulting URL(s) to all platforms; social skips its own re-host.
        image_urls: list = []
        try:
            with urllib.request.urlopen(
                f"http://localhost:9000/api/media/pending?chat_id={self._OWNER_CHAT}",
                timeout=5) as r:
                _paths = json.loads(r.read().decode()).get("image_paths", []) or []
        except Exception:
            _paths = []
        if _paths:
            _cdns: list = []
            for _p in _paths:
                try:
                    _inj = json.dumps({"chat_id": self._OWNER_CHAT,
                                       "file_path": _p}).encode()
                    with urllib.request.urlopen(urllib.request.Request(
                            "http://localhost:9000/api/media/inject", data=_inj,
                            method="POST",
                            headers={"Content-Type": "application/json"}),
                            timeout=15) as r:
                        _cdn = json.loads(r.read().decode()).get("cdn_url")
                except Exception as _exc:
                    log.warning("direct_post_all inject failed: %s", _exc)
                    _cdn = None
                if _cdn:
                    _cdns.append(_cdn)
                else:
                    _cdns = []        # any failure → fall back to per-platform path
                    break
            image_urls = _cdns
        lines = []
        for platform in ("x", "facebook", "instagram"):
            r = self._direct_post(platform, text, clear_after=False,
                                  image_urls=(image_urls or None))
            if r == "":
                r = "(no content)"
            lines.append(f"{self._DIRECT_LABEL[platform]}: {r.splitlines()[0]}")
        # Clear pending images once, after all platforms have their copies.
        try:
            cb = json.dumps({"chat_id": self._OWNER_CHAT}).encode()
            urllib.request.urlopen(urllib.request.Request(
                "http://localhost:9000/api/media/clear", data=cb, method="POST",
                headers={"Content-Type": "application/json"}), timeout=5)
        except Exception:
            pass
        return "📤 Multi-platform post\n" + "\n".join(lines)

    # Fallback ports for operators that register dynamically with CREW.
    # Used when BEACON can't find the operator (duplicate CREW instances,
    # registration lag, or CREW restart clearing in-memory registry).
    _OPERATOR_FALLBACK_PORTS: dict[str, tuple[int, str]] = {
        "recon":       (8002, "/api/task"),
        "quote_brief": (8006, "/api/task"),
        "scout":       (7002, "/api/run"),
        "email":       (8010, "/api/task"),
        "brief":       (8008, "/api/task"),
        "quote":       (8007, "/api/task"),
        "social":      (8011, "/api/task"),
    }

    def _dispatch_via_beacon(
        self, req: TaskRequest, target: str, task_id: str
    ) -> dict:
        message = {
            "task": req.task,
            "task_id": task_id,
            "source_channel": req.source_channel,
            "reply_channel": req.reply_channel,
            "sender": req.sender,
            "tenant_id": req.tenant_id,
            "metadata": req.metadata,
        }
        payload = {
            "sender": "chief",
            "message_type": "run.execute",
            "target": target,
            "message": message,
            "timeout": 60,
        }
        try:
            result = _http_post(f"{BEACON_URL}/route", payload, timeout=75)
            # BEACON wraps the operator response in forward_response
            if result.get("forwarded") is False:
                # BEACON couldn't find the operator port — fall back to direct dispatch
                self.runtime.logger.warning(
                    "CHIEF: BEACON could not forward to %s — trying direct dispatch", target
                )
                direct = self._dispatch_direct(target, message)
                if "error" not in direct:
                    return direct
                # direct failed — try wake+retry (operator may be sleeping/evicted from CREW)
                self.runtime.logger.info(
                    "CHIEF: %s not reachable — attempting wake via OM", target
                )
                if self._wake_and_wait(target):
                    retry = _http_post(f"{BEACON_URL}/route", payload, timeout=75)
                    if retry.get("forwarded") is not False:
                        return retry.get("forward_response") or retry
                    return self._dispatch_direct(target, message)
                return direct

            forward_resp = result.get("forward_response") or result
            # Target was found in CREW but its port returned 503 (sleeping process)
            if (
                result.get("forward_status") == 503
                and isinstance(forward_resp, dict)
                and "unreachable" in str(forward_resp.get("error", ""))
            ):
                self.runtime.logger.info(
                    "CHIEF: %s unreachable (503) — attempting wake via OM", target
                )
                if self._wake_and_wait(target):
                    retry = _http_post(f"{BEACON_URL}/route", payload, timeout=75)
                    if retry.get("forwarded") is not False:
                        return retry.get("forward_response") or retry
            return forward_resp
        except urllib.error.URLError as exc:
            self.runtime.logger.warning(
                "CHIEF: BEACON dispatch to %s failed: %s", target, exc
            )
            return {"error": f"worker timed out or unreachable: {exc.reason}", "target": target}
        except Exception as exc:
            self.runtime.logger.warning(
                "CHIEF: BEACON dispatch to %s error: %s", target, exc
            )
            return {"error": str(exc), "target": target}

    def _dispatch_direct(self, target: str, message: dict) -> dict:
        """Direct operator dispatch — used when BEACON can't find the operator port."""
        # Try CREW lookup first (handles dynamic registration)
        try:
            crew_data = _http_get(f"{CREW_URL}/crew", timeout=3)
            op = crew_data.get("operators", {}).get(target)
            if op and op.get("port"):
                port     = op["port"]
                task_hook = op.get("task_hook", "/api/task")
                self.runtime.logger.info(
                    "CHIEF direct dispatch: %s → port %d%s (via CREW)", target, port, task_hook
                )
                return _http_post(f"http://127.0.0.1:{port}{task_hook}", message, timeout=60)
        except Exception as exc:
            self.runtime.logger.warning("CHIEF: CREW lookup for %s failed: %s", target, exc)

        # Fall back to known static ports
        if target in self._OPERATOR_FALLBACK_PORTS:
            port, task_hook = self._OPERATOR_FALLBACK_PORTS[target]
            self.runtime.logger.info(
                "CHIEF direct dispatch: %s → port %d%s (fallback)", target, port, task_hook
            )
            try:
                return _http_post(f"http://127.0.0.1:{port}{task_hook}", message, timeout=60)
            except Exception as exc:
                return {"error": f"direct dispatch to {target} failed: {exc}"}

        return {"error": f"operator '{target}' not reachable — not in CREW and no fallback port"}

    # ------------------------------------------------------------------
    # Reply formatting
    # ------------------------------------------------------------------

    def _format_reply(self, target: str, result: dict) -> str:
        if "error" in result:
            err = str(result["error"]).lower()
            if any(x in err for x in (
                "unreachable", "connection refused", "did not wake",
                "not reachable", "timed out",
            )):
                return (
                    f"The {target} worker is starting up — this can take up to "
                    f"30 seconds on first use. Please try again in a moment."
                )
            return (
                f"Task could not be completed.\n"
                f"Worker: {target}\n"
                f"Reason: {result['error']}"
            )
        content = (
            result.get("result")
            or result.get("output")
            or result.get("summary")
            or result.get("text")
            or result.get("message")
            or result.get("data")
            or "Task completed."
        )
        if isinstance(content, dict):
            content = "\n".join(
                f"{k}: {v}" for k, v in list(content.items())[:5]
            )
        reply = str(content)
        return reply[:3500]

    # ------------------------------------------------------------------
    # Status command handlers
    # ------------------------------------------------------------------

    def _handle_status_command(self, command: str) -> str:
        if command == "/status":
            return self._status_summary()
        if command == "/missions":
            return self._missions_summary()
        if command == "/operators":
            return self._operators_summary()
        return self._help_text()

    def _status_summary(self) -> str:
        lines = ["Cascadia OS Status\n"]
        checks = [
            ("CREW",            CREW_URL + "/health"),
            ("BEACON",          BEACON_URL + "/health"),
            ("Mission Manager", MISSION_MANAGER_URL + "/healthz"),
            ("BELL",            BELL_URL + "/health"),
        ]
        for name, url in checks:
            try:
                _http_get(url, timeout=2)
                lines.append(f"  {name}: ready")
            except Exception:
                lines.append(f"  {name}: unreachable")
        try:
            crew_data = _http_get(CREW_URL + "/crew", timeout=2)
            n = crew_data.get("crew_size", "?")
            lines.append(f"  Operators registered: {n}")
        except Exception:
            pass

        stats = self._read_pipeline_stats()
        if stats:
            lines.append(
                "\n📋 OUTREACH PIPELINE\n"
                f"  Leads ready:    {stats['total']}\n"
                f"  Contacted:      {stats['contacted']}\n"
                f"  Pending:        {stats['pending']}\n"
                f"  Follow-ups due: {stats['followups_due']}\n"
                f"  Skipped:        {stats['skipped']}\n"
                f"  Exhausted:      {stats['exhausted']}"
            )
        else:
            lines.append("\n📋 Pipeline data unavailable")

        recon = self._read_recon_stats()
        icon, label = self._recon_icon_and_label(recon)
        lines.append(
            "\n🔍 RECON STATUS\n"
            f"  {icon} {label} — cycle {recon['cycle']}\n"
            f"  Task:    {recon['task']}\n"
            f"  Master:  {recon['master_rows']} leads total\n"
            f"  Current: {recon['task_rows']} rows / {recon['task_emails']} emails "
            f"({recon['email_rate']}% email rate)"
        )
        return "\n".join(lines)

    def _cmd_wizard(self) -> str:
        """Guided 3-step setup assistant: VAULT → credentials → recommendations."""
        lines: list[str] = ["🧙 <b>ZYRCON SETUP WIZARD</b>", ""]

        VAULT_URL  = "http://127.0.0.1:5101"
        OPERATORS_MAP = [
            ("EMAIL",    8010),
            ("SCOUT",    7002),
            ("DEMO",     8029),
            ("TELEGRAM", 9000),
            ("BUFFER",   9007),
            ("SOCIAL",   8011),
        ]

        # ── Step 1: VAULT ─────────────────────────────────────────────────────
        lines += ["━━━━━━━━━━━━━━━━━━━━", "Step 1 of 3 — VAULT", "━━━━━━━━━━━━━━━━━━━━"]
        vault_ok = False
        try:
            d = _http_get(f"{VAULT_URL}/health", timeout=3)
            vault_ok = bool(d.get("ok"))
        except Exception:
            vault_ok = False

        if vault_ok:
            lines.append("✅ VAULT is healthy")
        else:
            lines.append("❌ VAULT is offline")
            lines.append("Fix: restart via FLINT or check VAULT_ENCRYPTION_KEY in .env")

        # ── Step 2: Operator credentials ─────────────────────────────────────
        lines += ["", "━━━━━━━━━━━━━━━━━━━━", "Step 2 of 3 — Credentials", "━━━━━━━━━━━━━━━━━━━━"]
        issues: list[tuple[str, str, list[str]]] = []
        for name, port in OPERATORS_MAP:
            try:
                body = _http_get(f"http://127.0.0.1:{port}/api/ready", timeout=3)
                st   = body.get("readiness_status", "?")
                miss = body.get("missing_credentials", [])
                if st in ("ready", "healthy"):
                    lines.append(f"✅ {name}")
                elif st == "degraded":
                    m = miss[0] if miss else "unknown"
                    lines.append(f"⚠️ {name} — {m}")
                    issues.append((name, "degraded", miss))
                else:
                    m = ", ".join(miss) if miss else st
                    lines.append(f"❌ {name} — {m}")
                    issues.append((name, "blocked", miss))
            except Exception:
                lines.append(f"❓ {name} — unreachable")
                issues.append((name, "unreachable", []))

        # ── Step 3: Recommendations ──────────────────────────────────────────
        lines += ["", "━━━━━━━━━━━━━━━━━━━━", "Step 3 of 3 — Next Steps", "━━━━━━━━━━━━━━━━━━━━"]
        if not vault_ok:
            lines.append("1. Fix VAULT first — all credentials depend on it")
        elif not issues:
            lines += [
                "✅ Everything looks good!",
                "All operators have valid credentials.",
            ]
        else:
            lines.append(f"{len(issues)} item(s) need attention:\n")
            for name, status, miss in issues:
                if status == "unreachable":
                    lines.append(
                        f"• {name}: operator is not running\n"
                        f"  Check LaunchAgent: ai.zyrcon.{name.lower()}.plist"
                    )
                elif miss:
                    lines.append(
                        f"• {name}: missing {', '.join(miss)}\n"
                        f"  Run /check_credentials for details"
                    )
                else:
                    lines.append(
                        f"• {name}: {status}\n"
                        f"  Run /check_credentials --live"
                    )
            lines += [
                "",
                "💡 Use /check_credentials for full detail",
                "💡 Contact support@zyrcon.ai for setup help",
            ]

        return "\n".join(lines)

    def _read_pipeline_stats(self) -> dict | None:
        """Read outreach_ready.csv → pipeline KPIs. None on any error."""
        import csv
        from datetime import datetime, timezone
        csv_path = os.path.join(self._OUTPUT_DIR, "outreach_ready.csv")
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return None

        def _n(v):
            return sum(1 for r in rows if (r.get("contacted") or "").strip() == v)

        now = datetime.now(timezone.utc)
        due = 0
        for r in rows:
            d = (r.get("followup_due_at") or "").strip()
            if not d or (r.get("contacted") or "").strip() != "yes":
                continue
            try:
                if datetime.fromisoformat(d) <= now:
                    due += 1
            except ValueError:
                pass
        return {
            "total": len(rows), "contacted": _n("yes"), "pending": _n("pending"),
            "skipped": _n("skipped"), "exhausted": _n("exhausted"),
            "followups_due": due,
        }

    def _recon_icon_and_label(self, recon_stats: dict) -> tuple[str, str]:
        """Three-state RECON: 🟢 RUNNING / ⏸️ STANDBY / ❌ DOWN."""
        worker_alive = recon_stats.get("worker_process", False)
        if worker_alive:
            return "🟢", "RUNNING"
        dashboard_up = False
        try:
            urllib.request.urlopen("http://127.0.0.1:8002/api/health", timeout=2)
            dashboard_up = True
        except Exception:
            pass
        if dashboard_up:
            return "⏸️", "STANDBY"
        return "❌", "DOWN"

    def _read_recon_stats(self) -> dict:
        """RECON state for /status: task, status, cycle, master-CSV row count, and
        the current task folder's rows + email rate. Best-effort — each field stays
        at its default if a read fails."""
        import csv, glob, json
        result = {"task": "?", "status": "?", "cycle": 0,
                  "master_rows": 0, "task_rows": 0, "task_emails": 0, "email_rate": 0}
        output_dir = self._OUTPUT_DIR
        recon_dir  = os.path.dirname(output_dir)  # state.json lives in recon/, not output/

        try:
            with open(os.path.join(recon_dir, "state.json"), encoding="utf-8") as f:
                s = json.load(f)
            result["task"]  = s.get("task_name", "?")
            result["cycle"] = s.get("cycle", 0)
        except Exception:
            pass

        try:
            import subprocess as _sp
            proc = _sp.run(["pgrep", "-f", "recon_worker.py"],
                           capture_output=True, text=True)
            worker_alive = bool(proc.stdout.strip())
        except Exception:
            worker_alive = False
        result["status"]         = "running" if worker_alive else "stopped"
        result["worker_process"] = worker_alive

        try:
            with open(os.path.join(output_dir, "houston_contractors.csv"),
                      newline="", encoding="utf-8") as f:
                result["master_rows"] = max(sum(1 for _ in f) - 1, 0)
        except Exception:
            pass

        task = result.get("task", "")
        if task and task != "?":
            try:
                rows = []
                for fp in glob.glob(os.path.join(output_dir, task, "**", "*.csv"),
                                    recursive=True):
                    with open(fp, newline="", encoding="utf-8") as fh:
                        rows.extend(list(csv.DictReader(fh)))
                emails = [r for r in rows
                          if (r.get("email") or "").strip()
                          and "@" in r.get("email", "")
                          and "*" not in r.get("email", "")]
                result["task_rows"]   = len(rows)
                result["task_emails"] = len(emails)
                result["email_rate"]  = len(emails) * 100 // max(len(rows), 1)
            except Exception:
                pass
        return result

    def _missions_summary(self) -> str:
        try:
            data = _http_get(
                f"{MISSION_MANAGER_URL}/api/missions/runs?limit=5", timeout=5
            )
            runs = data.get("runs", [])
            if not runs:
                return "No recent mission runs."
            lines = ["Recent mission runs:\n"]
            for r in runs:
                lines.append(
                    f"  {r.get('id','?')[:8]}  {r.get('status','?')}  "
                    f"{r.get('started_at','?')[:16]}"
                )
            return "\n".join(lines)
        except Exception:
            return "Mission Manager unreachable."

    def _help_text(self) -> str:
        return (
            "Cascadia OS — Commands:\n\n"
            "/status     System health\n"
            "/missions   Recent mission runs\n"
            "/operators  Registered workers\n"
            "/help       This message\n\n"
            "Or describe your task naturally:\n"
            "  \"Find leads for HVAC contractors in Houston\"\n"
            "  \"Draft a proposal for warehouse installation\"\n"
            "  \"Research competitor pricing\""
        )

    def _operators_summary(self) -> str:
        try:
            data = _http_get(CREW_URL + "/crew", timeout=3)
            operators = data.get("operators") or {}
            if not operators:
                return "No operators currently registered."
            lines = [f"Registered operators ({data.get('crew_size', 0)}):\n"]
            for op_id, op in operators.items():
                caps = op.get("capabilities", [])[:3]
                cap_str = ", ".join(caps) if caps else "—"
                lines.append(f"  {op_id}: {cap_str}")
            summary = "\n".join(lines)
            return summary[:3500]
        except Exception:
            return "CREW unreachable — cannot list operators."

    # ------------------------------------------------------------------
    # Pipeline snapshot + outreach dispatch
    # ------------------------------------------------------------------

    # Resolve paths from the runtime user's home so this works on any host
    # (was hardcoded to /Users/andy, which does not exist on every node).
    _OUTPUT_DIR     = os.path.join(
        os.path.expanduser("~"),
        "Zyrcon", "operators", "cascadia-os-operators", "recon", "output",
    )
    _CSV_PATH       = os.path.join(_OUTPUT_DIR, "houston_contractors.csv")
    _REPLIES_PATH   = os.path.join(_OUTPUT_DIR, "replies.json")
    _APPROVALS_FILE = os.path.join(_OUTPUT_DIR, "pending_quotes.json")
    _OUTREACH_FILE  = os.path.join(_OUTPUT_DIR, "pending_outreach.json")
    _RECON_URL      = "http://127.0.0.1:8002"
    _approvals_lock = threading.Lock()
    _outreach_lock  = threading.Lock()

    def _pipeline_snapshot(self) -> str:
        """Read outreach_ready.csv and return a formatted pipeline snapshot."""
        import csv
        from pathlib import Path
        csv_path = Path(self._OUTPUT_DIR) / "outreach_ready.csv"
        if not csv_path.exists():
            return "📊 No lead data found — run /recon first."
        try:
            rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
        except Exception as exc:
            return f"📊 Could not read lead data: {exc}"

        def _s(r, k, default=""):
            v = r.get(k) or default
            return str(v).strip().lower()

        total          = len(rows)
        high           = sum(1 for r in rows if _s(r, "confidence") == "high")
        medium         = sum(1 for r in rows if _s(r, "confidence") == "medium")
        contacted      = sum(1 for r in rows if _s(r, "contacted") == "yes")
        not_interested = sum(1 for r in rows if _s(r, "contacted") == "not_interested")
        pending        = sum(1 for r in rows if _s(r, "contacted") == "pending")
        replied        = sum(1 for r in rows if _s(r, "reply_received") == "yes")
        quoted         = sum(1 for r in rows if _s(r, "deal_stage") == "quoted")
        uncontacted    = sum(
            1 for r in rows
            if _s(r, "contacted") not in ("yes", "not_interested", "pending")
        )
        top5 = [
            r for r in rows
            if _s(r, "confidence") == "high"
            and _s(r, "phone")
            and _s(r, "contacted") not in ("yes", "not_interested", "pending")
        ][:5]

        email_high     = sum(1 for r in rows if _s(r, "email_quality") == "high")
        email_medium   = sum(1 for r in rows if _s(r, "email_quality") == "medium")
        email_inferred = sum(1 for r in rows if _s(r, "email_quality") == "inferred")
        email_none     = total - email_high - email_medium - email_inferred

        lines = [
            "📊 Lead Pipeline — HVAC / Plumbing Outreach\n",
            f"Total: {total} | High confidence: {high} | Medium: {medium}\n",
            f"✅ Contacted:        {contacted}",
            f"❌ Not interested:   {not_interested}",
            f"⏳ Pending outreach: {pending}",
            f"📋 Uncontacted:      {uncontacted}",
            f"📩 Replied:          {replied}",
            f"📄 Quoted:           {quoted}\n",
            f"📧 Emails — named: {email_high} | generic: {email_medium} | inferred: {email_inferred} | none: {email_none}",
        ]
        if top5:
            lines.append("\nTop uncontacted (high confidence):")
            for i, r in enumerate(top5, 1):
                email_tag = f" | 📧 {r['email']}" if r.get("email") else ""
                lines.append(f"{i}. {r['business_name']} — {r.get('phone', 'no phone')}{email_tag}")
        lines.append("\nRun /outreach to brief these leads.")
        return "\n".join(lines)

    def _followups_snapshot(self) -> str:
        """Read outreach_ready.csv and list contacted leads whose follow-up is
        due now (contacted==yes AND followup_due_at <= now)."""
        import csv as _csv
        import os
        from datetime import datetime, timezone
        path = os.path.join(self._OUTPUT_DIR, "outreach_ready.csv")
        try:
            rows = list(_csv.DictReader(open(path, newline="", encoding="utf-8")))
        except Exception as exc:
            return f"📬 Could not read outreach_ready.csv: {exc}"

        now = datetime.now(timezone.utc)
        due: list[tuple[dict, str]] = []
        for r in rows:
            if (r.get("contacted") or "").strip().lower() != "yes":
                continue
            due_str = (r.get("followup_due_at") or "").strip()
            if not due_str:
                continue
            try:
                if datetime.fromisoformat(due_str) <= now:
                    due.append((r, due_str))
            except ValueError:
                continue

        # Dedup by business name (case-insensitive); keep earliest due_at
        seen: dict[str, tuple[dict, str]] = {}
        for r, due_str in due:
            key = (r.get("business_name") or "").strip().lower()
            if key not in seen or due_str < seen[key][1]:
                seen[key] = (r, due_str)
        due = list(seen.values())

        if not due:
            return "✅ No follow-ups due right now.\nPULSE checks daily at 9am."

        lines = ["⏰ FOLLOW-UPS DUE\n"]
        for r, due_str in due:
            lines.append(f"• {r.get('business_name','')} ({r.get('city','')})")
            lines.append(f"  Due: {due_str[:10]}")
        lines.append("──────────────────")
        lines.append("Run /outreach to queue follow-up drafts for approval.")
        return "\n".join(lines)

    def _replies_snapshot(self) -> str:
        """Read replies.json and return a formatted summary of recent replies."""
        from pathlib import Path
        path = Path(self._REPLIES_PATH)
        if not path.exists():
            return (
                "📭 No replies yet.\n"
                "Inbox is monitored every 5 minutes.\n"
                "Run /inbox_check to poll now."
            )
        try:
            records = json.loads(path.read_text())
        except Exception as exc:
            return f"📩 Could not load replies: {exc}"
        if not records:
            return (
                "📭 No replies yet.\n"
                "Inbox is monitored every 5 minutes.\n"
                "Run /inbox_check to poll now."
            )
        lines = [f"📬 REPLIES RECEIVED ({len(records)} total)\n"]
        for r in reversed(records[-10:]):
            ts      = (r.get("detected_at") or "")[:16].replace("T", " ")
            biz     = r.get("business_name", "?")
            addr    = r.get("from_email", "")
            subj    = (r.get("subject") or "")[:60]
            preview = (r.get("body_preview") or "")[:100].strip()
            idx     = r.get("row_idx", "")
            lines.append(f"• {biz}")
            lines.append(f"  From:     {addr}")
            lines.append(f"  Subject:  {subj}")
            lines.append(f"  Received: {ts} UTC")
            if preview:
                lines.append(f"  Preview:  {preview}{'...' if len(r.get('body_preview','')) > 100 else ''}")
            if idx != "":
                lines.append(f"  → /quote_{idx} to draft a proposal")
            lines.append("  ──────────────────")
        return "\n".join(lines).strip()

    def _inbox_check(self, n: int = 1) -> str:
        """Show last N emails from the log; trigger IMAP poll in the background."""
        from pathlib import Path
        from datetime import datetime, timedelta
        # Fire poll as daemon — never block the response waiting for IMAP
        def _trigger_poll() -> None:
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:8010/api/inbox/check",
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=15)
            except Exception:
                pass
        threading.Thread(target=_trigger_poll, daemon=True,
                         name="inbox-poll-trigger").start()

        email_log = Path(os.path.expanduser("~")) / \
            "Zyrcon/operators/cascadia-os-operators/email/data/email_log.json"
        try:
            emails = json.loads(email_log.read_text()) if email_log.exists() else []
        except Exception as exc:
            return f"🔍 Inbox check failed: {str(exc)[:100]}"

        if not emails:
            return (
                "🔍 Inbox checked — no emails on record.\n"
                "Monitor is active and polling every 5 minutes."
            )

        def to_ct(utc_str: str) -> str:
            try:
                dt = datetime.strptime(utc_str[:19], "%Y-%m-%dT%H:%M:%S")
                ct = dt - timedelta(hours=5)
                return ct.strftime("%-m/%-d %-I:%M %p CT")
            except Exception:
                return utc_str[:16]

        total  = len(emails)
        recent = emails[:n]   # newest first (log inserts at index 0)

        if n == 1:
            e       = recent[0]
            preview = (e.get("body_preview") or "")[:100]
            lines = [
                "🔍 Inbox checked",
                "",
                "Last message:",
                f"From:    {e.get('from', '?')}",
                f"Subject: {e.get('subject', '?')}",
                f"Time:    {to_ct(e.get('received_at', ''))}",
                f"Preview: {preview}",
                "",
                "──────────────────",
                f"Total emails on record: {total}",
                "Run /inbox_check 10 to see more.",
            ]
            return "\n".join(lines)

        lines = [f"🔍 Inbox — last {n} messages", ""]
        for i, e in enumerate(recent, 1):
            lines.append(f"{i}. {e.get('subject', '?')}")
            lines.append(f"   From: {e.get('from', '?')}")
            lines.append(f"   {to_ct(e.get('received_at', ''))}")
            if i < len(recent):
                lines.append("")
        lines += ["", "──────────────────", f"Total emails on record: {total}"]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Pending quote store
    # ------------------------------------------------------------------

    def _load_approvals(self) -> dict:
        from pathlib import Path
        path = Path(self._APPROVALS_FILE)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _save_approvals(self, data: dict) -> None:
        from pathlib import Path
        Path(self._APPROVALS_FILE).write_text(json.dumps(data, indent=2))

    def _store_pending_quote(self, row_id: str, entry: dict) -> None:
        with self._approvals_lock:
            data = self._load_approvals()
            data[row_id] = entry
            self._save_approvals(data)

    # ------------------------------------------------------------------
    # Pending outreach store (mirror of the quote store, separate file)
    # ------------------------------------------------------------------

    def _load_outreach_approvals(self) -> dict:
        from pathlib import Path
        path = Path(self._OUTREACH_FILE)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _save_outreach_approvals(self, data: dict) -> None:
        from pathlib import Path
        Path(self._OUTREACH_FILE).write_text(json.dumps(data, indent=2))

    def _is_outreach_actioned(self, row_id: str) -> bool:
        """True if the lead at row_id in outreach_ready.csv has a non-empty
        'contacted' value (yes/skipped/exhausted/pending) — i.e. it was already
        handled, so an approve/reject for it is a no-op rather than a quote."""
        import csv
        try:
            idx = int(row_id)
        except (TypeError, ValueError):
            return False
        path = os.path.join(self._OUTPUT_DIR, "outreach_ready.csv")
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return False
        if 0 <= idx < len(rows):
            return bool((rows[idx].get("contacted") or "").strip())
        return False

    def _handle_outreach_approval_request(
        self, payload: Dict[str, Any]
    ) -> tuple[int, Dict[str, Any]]:
        """Inbound from RECON: stage an outreach draft, post the approval
        prompt to Telegram. The actual email send happens later on /approve_N.
        """
        from datetime import datetime, timezone

        row_id = str(payload.get("row_id", "")).strip()
        if not row_id:
            return 400, {"ok": False, "error": "row_id required"}

        biz     = payload.get("business_name", f"lead #{row_id}")
        email   = payload.get("email", "")
        btype   = payload.get("business_type", "contractor")
        city    = payload.get("city", "")
        subject = payload.get("subject", "")
        body    = payload.get("body", "")
        chat_id = str(payload.get("chat_id", ""))
        # Originating operator gets the approve/reject callback. Defaults to
        # RECON for backward compatibility (first-touch outreach). PULSE passes
        # its own URL so follow-up bookkeeping runs in PULSE, not RECON.
        source_url = (payload.get("source_url") or self._RECON_URL).rstrip("/")
        kind       = payload.get("kind") or "outreach"

        from_email = str(payload.get("from_email") or "hello@zyrcon.ai")
        slot       = str(payload.get("slot") or "0")
        tg_header  = str(payload.get("tg_header") or "")

        entry = {
            "row_id":        row_id,
            "business_name": biz,
            "email":         email,
            "business_type": btype,
            "city":          city,
            "subject":       subject,
            "body":          body,
            "chat_id":       chat_id,
            "source_url":    source_url,
            "kind":          kind,
            "from_email":    from_email,
            "slot":          slot,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        with self._outreach_lock:
            data = self._load_outreach_approvals()
            data[row_id] = entry
            self._save_outreach_approvals(data)

        header = (
            "🔁 FOLLOW-UP DRAFT — approval needed" if kind == "followup"
            else "📋 OUTREACH DRAFT — approval needed"
        )
        tg_line = f"\n{tg_header}" if tg_header else ""
        msg = (
            f"{header}{tg_line}\n\n"
            f"🏢 {biz}\n"
            f"🔧 {btype} | {city}\n"
            f"📧 {email}\n\n"
            f"Subject: {subject}\n\n"
            f"{body}"
        )
        buttons = {"inline_keyboard": [[
            {"text": "✅ Send",  "callback_data": f"approve:{row_id}"},
            {"text": "❌ Skip",  "callback_data": f"reject:{row_id}"},
        ]]}
        # RC-2 silent staging (batch mode): entry is already persisted above;
        # skip the per-lead Telegram post so PULSE can send ONE consolidated
        # preview for the whole batch instead.
        if payload.get("silent"):
            return 200, {"ok": True, "queued": True, "row_id": row_id}
        if chat_id:
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": msg, "reply_markup": buttons},
                    timeout=5,
                )
            except Exception as exc:
                self.runtime.logger.warning(
                    "CHIEF outreach approval Telegram post failed: %s", exc
                )

        return 200, {"ok": True, "queued": True, "row_id": row_id}

    # ------------------------------------------------------------------
    # Quote generation + approval
    # ------------------------------------------------------------------

    def _generate_quote_for_lead(self, row_id: str, description: str, chat_id: str) -> None:
        """Background: read lead → call quote operator → store + preview via Telegram."""
        import csv
        from pathlib import Path
        from datetime import datetime, timezone

        def _tg(text: str) -> None:
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": text},
                    timeout=5,
                )
            except Exception:
                pass

        try:
            rows = list(csv.DictReader(Path(self._CSV_PATH).open(newline="", encoding="utf-8")))
        except Exception as exc:
            _tg(f"❌ Could not read lead data: {exc}")
            return

        try:
            idx = int(row_id)
        except ValueError:
            _tg(f"❌ Invalid row ID: {row_id!r}")
            return

        if idx < 0 or idx >= len(rows):
            _tg(f"❌ Lead #{row_id} not found (only {len(rows)} rows).")
            return

        lead  = rows[idx]
        biz   = lead.get("business_name", "").strip() or f"Lead #{row_id}"
        email = lead.get("email", "").strip()
        btype = (lead.get("business_type") or lead.get("notes") or "contractor").strip()[:60]
        city  = lead.get("city", "Houston").strip() or "Houston"
        phone = lead.get("phone", "").strip()
        scope = description or f"Pipeline management and lead generation for {btype} contractors in {city}"

        try:
            result = _http_post(
                "http://127.0.0.1:8007/api/task",
                {
                    "task": scope,
                    "context": {
                        "company_name":  biz,
                        "contact_email": email,
                        "contact_name":  "",
                        "opportunity":   scope,
                    },
                },
                timeout=30,
            )
        except Exception as exc:
            _tg(f"❌ Quote operator unreachable: {exc}")
            return

        proposal = result.get("result") or {}
        if not proposal:
            _tg("❌ Quote operator returned no proposal.")
            return

        budget   = proposal.get("budget", "TBD")
        sections = proposal.get("sections") or {}

        body_parts = ["Hi there,\n\nThank you for getting back to us.\n"]
        if sections.get("scope"):
            body_parts.append(str(sections["scope"]))
        if sections.get("approach"):
            body_parts.append(str(sections["approach"]))
        if budget and budget != "TBD":
            body_parts.append(f"Investment: {budget}")
        if sections.get("next_steps"):
            body_parts.append(str(sections["next_steps"]))
        else:
            body_parts.append("Worth a quick call to walk through the details?")
        body_parts.append("\nAndy | Zyrcon Labs")
        email_body    = "\n\n".join(body_parts)
        email_subject = f"Proposal — Zyrcon Labs for {biz}"

        entry = {
            "row_id":        row_id,
            "business_name": biz,
            "email":         email,
            "phone":         phone,
            "proposal_id":   proposal.get("id", ""),
            "subject":       email_subject,
            "body":          email_body,
            "budget":        str(budget),
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        self._store_pending_quote(row_id, entry)

        scope_preview = ""
        if sections.get("scope"):
            scope_preview = f"\n\n{str(sections['scope'])[:200]}"

        _tg(
            f"📄 Proposal ready — {biz}\n"
            f"📧 To: {email or '(no email)'}\n"
            f"💰 Budget: {budget}"
            f"{scope_preview}\n\n"
            f"Subject: {email_subject}\n\n"
            f"{email_body[:500]}\n\n"
            f"/approve_{row_id} ✅ Send  |  /reject_{row_id} ❌ Discard"
        )

    def _handle_outreach_decision(
        self, action: str, row_id: str, entry: dict, chat_id: str
    ) -> str:
        """Handle /approve_N or /reject_N for a staged OUTREACH draft.
        On approve: send via the email operator, then tell RECON to mark the
        lead contacted. On reject: tell RECON the lead was skipped.
        """
        biz   = entry.get("business_name", f"lead #{row_id}")
        email = entry.get("email", "")
        # Call back the originating operator (RECON for first-touch, PULSE for
        # follow-ups). Falls back to RECON for entries staged before this field.
        source_url = (entry.get("source_url") or self._RECON_URL).rstrip("/")

        def _drop() -> None:
            with self._outreach_lock:
                data = self._load_outreach_approvals()
                data.pop(row_id, None)
                self._save_outreach_approvals(data)

        if action == "reject":
            try:
                _http_post(
                    f"{source_url}/api/outreach/skipped",
                    {"row_id": row_id},
                    timeout=10,
                )
            except Exception as exc:
                self.runtime.logger.warning(
                    "CHIEF outreach skipped callback failed: %s", exc
                )
            _drop()
            return f"⏭ Skipped {biz}"

        # approve — send the email
        if not email:
            return f"❌ No email address for {biz} — cannot send."

        # ── SAFETY RE-CHECK (final gate before SMTP) ──────────────────────────
        # Block drafts that slipped into the queue before the promote/scrape
        # filters existed (chains, cross-attributed domains, masked/role emails).
        blocked = _outreach_safety_reason(biz, email)
        if blocked:
            self.runtime.logger.warning(
                "CHIEF send-safety BLOCKED %s (%s) — reason: %s", biz, email, blocked
            )
            # Tell the originating operator the lead was skipped, then drop it.
            try:
                _http_post(
                    f"{source_url}/api/outreach/skipped",
                    {"row_id": row_id},
                    timeout=10,
                )
            except Exception as exc:
                self.runtime.logger.warning(
                    "CHIEF safety-skip callback failed: %s", exc
                )
            _drop()
            return (
                f"⚠️ BLOCKED (safety check): {biz}\n"
                f"Reason: {blocked}\n"
                f"Email: {email}\n"
                f"Lead removed from queue — not sent."
            )
        # ── END SAFETY RE-CHECK ───────────────────────────────────────────────

        # ── BUSINESS HOURS GATE ───────────────────────────────────────────────
        if not _is_business_hours():
            next_window = _next_send_window()
            window_str  = next_window.strftime("%b %d %I:%M %p CT")

            def _deferred() -> None:
                while not _is_business_hours():
                    self.runtime.logger.info(
                        "CHIEF: outside business hours — holding send for %s until %s",
                        biz, window_str,
                    )
                    time.sleep(60)
                try:
                    res = _http_post(
                        "http://127.0.0.1:8010/api/task",
                        {"to":         email,
                         "subject":    entry.get("subject", ""),
                         "body":       entry.get("body", ""),
                         "reply_to":   entry.get("from_email", "hello@zyrcon.ai"),
                         "from_email": entry.get("from_email", "hello@zyrcon.ai"),
                         "slot":       entry.get("slot", "0")},
                        timeout=20,
                    )
                except Exception as exc:
                    self.runtime.logger.error("CHIEF deferred send failed: %s", exc)
                    return
                if res.get("ok"):
                    try:
                        _http_post(f"{source_url}/api/outreach/approved",
                                   {"row_id": row_id}, timeout=10)
                    except Exception as exc:
                        self.runtime.logger.warning(
                            "CHIEF approved callback (deferred) failed: %s", exc)
                    try:
                        _http_post(f"{TELEGRAM_URL}/send",
                                   {"chat_id": chat_id,
                                    "text": f"✅ Email sent to {biz} ({email})"},
                                   timeout=5)
                    except Exception:
                        pass
                else:
                    self.runtime.logger.error(
                        "CHIEF deferred send failed for %s: %s", biz, res)

            _drop()
            threading.Thread(target=_deferred, daemon=True,
                             name=f"deferred-send-{row_id}").start()
            return f"✅ Queued — sending at {window_str}"
        # ── END BUSINESS HOURS GATE ──────────────────────────────────────────

        try:
            send_result = _http_post(
                "http://127.0.0.1:8010/api/task",
                {
                    "to":         email,
                    "subject":    entry.get("subject", ""),
                    "body":       entry.get("body", ""),
                    "reply_to":   entry.get("from_email", "hello@zyrcon.ai"),
                    "from_email": entry.get("from_email", "hello@zyrcon.ai"),
                    "slot":       entry.get("slot", "0"),
                },
                timeout=20,
            )
        except Exception as exc:
            return f"❌ Email operator unreachable: {exc}"
        if not send_result.get("ok"):
            return f"❌ Email send failed: {send_result.get('error', 'unknown')}"

        # Write contacted_via + outreach_sent_at to CRM (non-fatal, threaded)
        def _crm_write() -> None:
            try:
                import urllib.parse as _up
                from datetime import datetime, timezone as _tz
                search_url = (
                    f"http://127.0.0.1:8015/api/contacts"
                    f"?search={_up.quote(email, safe='')}&limit=1"
                )
                req = urllib.request.Request(
                    search_url, method="GET",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=3) as _r:
                    contacts = json.loads(_r.read().decode()).get("contacts", [])
                if not contacts:
                    return
                cid = contacts[0]["id"]
                put_body = json.dumps({
                    "contacted_via":    entry.get("from_email", ""),
                    "outreach_slot":    str(entry.get("slot", "0")),
                    "outreach_sent_at": datetime.now(_tz.utc).isoformat(),
                }).encode()
                put_req = urllib.request.Request(
                    f"http://127.0.0.1:8015/api/contacts/{cid}",
                    data=put_body, method="PUT",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(put_req, timeout=3)
            except Exception:
                pass
        threading.Thread(target=_crm_write, daemon=True,
                         name=f"crm-write-{row_id}").start()

        try:
            _http_post(
                f"{source_url}/api/outreach/approved",
                {"row_id": row_id},
                timeout=10,
            )
        except Exception as exc:
            self.runtime.logger.warning(
                "CHIEF outreach approved callback failed: %s", exc
            )
        _drop()
        return f"✅ Email sent to {biz} ({email})"

    def _handle_approval(self, action: str, row_id: str, chat_id: str) -> str:
        """Handle /approve_N or /reject_N. Returns reply text.

        Outreach drafts and quote proposals share the row_id keyspace, so check
        the outreach store FIRST; if the row_id isn't there, fall through to the
        quote-approval logic below.
        """
        import csv
        from pathlib import Path
        from datetime import datetime, timezone

        with self._outreach_lock:
            outreach_entry = self._load_outreach_approvals().get(row_id)
        if outreach_entry is not None:
            return self._handle_outreach_decision(
                action, row_id, outreach_entry, chat_id
            )

        with self._approvals_lock:
            data  = self._load_approvals()
            entry = data.get(row_id)

        if not entry:
            if self._is_outreach_actioned(row_id):
                return (
                    f"ℹ️ Lead #{row_id} was already handled "
                    "(outreach previously approved or skipped)."
                )
            return (
                f"⚠️ No pending item for lead #{row_id}. "
                "It may have already been sent or discarded."
            )

        biz = entry.get("business_name", f"lead #{row_id}")

        if action == "reject":
            with self._approvals_lock:
                data = self._load_approvals()
                data.pop(row_id, None)
                self._save_approvals(data)
            return f"❌ Proposal discarded — {biz}."

        # approve — send email
        email   = entry.get("email", "")
        subject = entry.get("subject", "Proposal — Zyrcon Labs")
        body    = entry.get("body", "")

        if not email:
            return f"❌ No email address for {biz} — cannot send proposal."

        try:
            send_result = _http_post(
                "http://127.0.0.1:8010/api/task",
                {"to": email, "subject": subject, "body": body},
                timeout=20,
            )
        except Exception as exc:
            return f"❌ Email operator unreachable: {exc}"

        if not send_result.get("ok"):
            err = send_result.get("error", "unknown")
            return f"❌ Email send failed: {err}"

        # Mark deal_stage=quoted in CSV
        try:
            csv_path = Path(self._CSV_PATH)
            rows     = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
            idx      = int(row_id)
            if 0 <= idx < len(rows):
                fieldnames = list(rows[0].keys())
                if "quoted_at" not in fieldnames:
                    fieldnames.append("quoted_at")
                    for r in rows:
                        r.setdefault("quoted_at", "")
                rows[idx]["deal_stage"] = "quoted"
                rows[idx]["quoted_at"]  = datetime.now(timezone.utc).isoformat()
                with csv_path.open("w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rows)
        except Exception as exc:
            self.runtime.logger.error("CHIEF: mark_quoted CSV update failed: %s", exc)

        with self._approvals_lock:
            data = self._load_approvals()
            data.pop(row_id, None)
            self._save_approvals(data)

        return f"✅ Proposal sent to {biz} at {email}.\nDeal stage: quoted."

    def _mark_lead_contacted(self, row_id: str, status: str, chat_id: str) -> str:
        """Call RECON /api/contact and return a user-facing reply."""
        _LABELS = {
            "yes": "✅ Contacted",
            "not_interested": "❌ Not interested",
            "pending": "⏳ Pending",
        }
        try:
            result = _http_post(
                f"{self._RECON_URL}/api/contact",
                {"row_id": row_id, "status": status},
                timeout=10,
            )
            if result.get("ok"):
                biz   = result.get("business_name", f"lead {row_id}")
                label = _LABELS.get(status, status)
                return f"{label}: {biz} marked."
            return f"❌ Could not mark lead: {result.get('error', 'unknown error')}"
        except Exception as exc:
            self.runtime.logger.error("CHIEF: mark_contacted failed: %s", exc)
            return f"❌ Could not reach RECON to mark lead: {exc}"

    # ------------------------------------------------------------------
    # RECON worker control (/recon_start, /recon_stop)
    # Fast synchronous calls to RECON's /api/start and /api/stop. Same
    # response works from both Telegram and PRISM — no source branch needed.
    # ------------------------------------------------------------------

    def _recon_start(self) -> str:
        try:
            result = _http_post(f"{self._RECON_URL}/api/start", {}, timeout=10)
        except Exception as exc:
            self.runtime.logger.error("CHIEF recon_start failed: %s", exc)
            return (
                f"❌ Could not start RECON:\n{exc}\n"
                "Check logs at port 8002."
            )
        if not result.get("ok"):
            return (
                f"❌ Could not start RECON:\n{result.get('message', 'unknown error')}\n"
                "Check logs at port 8002."
            )
        if result.get("already_running"):
            return (
                "▶️ RECON is already running.\n"
                "Currently scraping leads.\n"
                "Use /status to check progress."
            )
        return (
            "▶️ RECON started.\n"
            "Scraping Houston HVAC and plumbing leads. Use /status to monitor."
        )

    def _recon_stop(self) -> str:
        try:
            result = _http_post(f"{self._RECON_URL}/api/stop", {}, timeout=10)
        except Exception as exc:
            self.runtime.logger.error("CHIEF recon_stop failed: %s", exc)
            return f"❌ Could not stop RECON:\n{exc}"
        if not result.get("ok"):
            return f"❌ Could not stop RECON:\n{result.get('message', 'unknown error')}"
        if result.get("already_stopped") or result.get("not_running"):
            return (
                "⏹ RECON is not running.\n"
                "Run /recon_start to begin scraping leads."
            )
        return (
            "⏹️ RECON stopped.\n"
            "Lead data is preserved.\n"
            "Run /recon_start to resume."
        )

    _PULSE_URL = "http://127.0.0.1:8016"

    def _archive_leads(self) -> str:
        """Trigger PULSE to move completed (exhausted/skipped) leads out of
        outreach_ready.csv into contacted_list.csv. Works from Telegram + PRISM."""
        try:
            result = _http_post(f"{self._PULSE_URL}/api/archive", {}, timeout=10)
        except Exception as exc:
            self.runtime.logger.error("CHIEF archive failed: %s", exc)
            return f"❌ Could not reach PULSE to archive:\n{exc}"
        n = result.get("archived", 0)
        if result.get("skipped_reason"):
            return (
                "📁 Archival deferred — approvals are still in flight.\n"
                "Try again once pending follow-ups are approved or rejected."
            )
        if not n:
            return (
                "📁 Nothing to archive.\n"
                "All leads still in active follow-up."
            )
        names = result.get("names") or []
        lines = [f"📁 ARCHIVED {n} completed lead(s):"]
        lines.extend(f"• {b}" for b in names)
        lines.append("")
        lines.append("Moved to contacted_list.csv.")
        lines.append(f"outreach_ready.csv has {result.get('remaining', '?')} leads remaining.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # PRISM synchronous variants (source=="prism")
    # Same work as the Telegram threaded handlers, but the full result is
    # returned to the caller as a string instead of pushed to Telegram.
    # ------------------------------------------------------------------

    def _preview_sync_for_prism(self) -> str:
        """Sync /preview for PRISM: GET a draft preview from RECON (read-only —
        stages nothing, marks nothing) and return it as text."""
        try:
            result = _http_post(f"{self._RECON_URL}/api/preview", {}, timeout=45)
        except Exception as exc:
            return f"❌ Preview unavailable: {exc}"
        if not result.get("ok"):
            return ("No leads available for preview. "
                    "RECON is finding more — check back soon.")
        return (
            "👁 OUTREACH PREVIEW\n\n"
            f"Business: {result.get('business_name','')}\n"
            f"Type: {result.get('business_type','')} | {result.get('city','')}\n"
            f"To: {result.get('email','')}\n\n"
            f"Subject: {result.get('subject','')}\n\n"
            f"{result.get('body','')}\n\n"
            "──────────────────────────\n"
            "This is a preview only.\n"
            "To queue for approval run /outreach"
        )

    def _outreach_sync_for_prism(self, limit: int = 20) -> str:
        """Sync /outreach for PRISM: trigger RECON to draft + stage up to `limit`
        outreach leads, wait for RECON's callbacks to land them in
        pending_outreach.json, then return a summary. RECON drafts asynchronously
        (one per few seconds), so we poll until `limit` new entries appear or the
        budget runs out — kept under the PRISM proxy's 30s request timeout."""
        with self._outreach_lock:
            before = set(self._load_outreach_approvals().keys())
        try:
            _http_post(
                f"{self._RECON_URL}/api/outreach",
                {"chat_id": "prism", "limit": limit},
                timeout=10,
            )
        except Exception as exc:
            self.runtime.logger.error("CHIEF prism outreach dispatch failed: %s", exc)
            return f"❌ Could not start outreach: {exc}"

        # Poll until `limit` drafts are staged or the budget runs out.
        deadline = time.time() + 25
        new_entries: list[dict] = []
        while time.time() < deadline:
            with self._outreach_lock:
                data = self._load_outreach_approvals()
            new_keys = sorted(set(data.keys()) - before)
            new_entries = [{**data[k], "row_id": k} for k in new_keys]
            if len(new_entries) >= limit:
                break
            time.sleep(1)

        if not new_entries:
            return (
                "📋 Outreach started, but no drafts were staged yet — RECON may "
                "still be drafting or no eligible leads were available. "
                "Try /preview or run /outreach again shortly."
            )

        lines = [f"📋 QUEUED {len(new_entries)} DRAFT(S) FOR APPROVAL\n"]
        for e in new_entries:
            lines.append(
                f"• {e.get('business_name','')} "
                f"({e.get('business_type','')} | {e.get('city','')})\n"
                f"  To: {e.get('email','')} — /reject_{e.get('row_id','')} to skip"
            )
        lines.append("\n──────────────────────────")
        lines.append("Type /approve_all to send all, or /reject_<id> to skip one.")
        return "\n".join(lines)

    def _approve_all_sync_for_prism(self) -> str:
        """Sync /approve_all for PRISM: approve every pending outreach draft, then
        every pending quote, synchronously. No thread spawn, no Telegram pushes —
        a summary string is returned. One failure is logged and skipped."""
        with self._outreach_lock:
            outreach = dict(self._load_outreach_approvals())
        with self._approvals_lock:
            quotes = dict(self._load_approvals())

        if not outreach and not quotes:
            return "✅ Nothing pending — queue is empty."

        approved: list[str] = []
        failed:   list[str] = []

        # Outreach first (each decision pops its own entry from the store).
        for row_id, entry in outreach.items():
            biz = entry.get("business_name", f"lead #{row_id}")
            try:
                res = self._handle_outreach_decision("approve", row_id, entry, "prism")
                if res.startswith("✅"):
                    approved.append(biz)
                else:
                    failed.append(f"{biz}: {res}")
            except Exception as exc:
                self.runtime.logger.error(
                    "prism approve_all outreach %s failed: %s", row_id, exc
                )
                failed.append(f"{biz}: {exc}")

        # Quotes next (outreach store now drained, so _handle_approval routes
        # these to the quote branch).
        for row_id, entry in quotes.items():
            biz = entry.get("business_name", f"quote #{row_id}")
            try:
                res = self._handle_approval("approve", row_id, "prism")
                if res.startswith("✅"):
                    approved.append(biz)
                else:
                    failed.append(f"{biz}: {res}")
            except Exception as exc:
                self.runtime.logger.error(
                    "prism approve_all quote %s failed: %s", row_id, exc
                )
                failed.append(f"{biz}: {exc}")

        lines: list[str] = []
        if approved:
            display = approved[:3]
            more    = len(approved) - len(display)
            name_str = ", ".join(display) + (f" +{more} more" if more else "")
            lines.append(f"✅ Sent {len(approved)}: {name_str}")
        else:
            lines.append("⚠️ Nothing was approved.")
        if failed:
            lines.append(f"⚠️ {len(failed)} failed: " +
                         ", ".join(f.split(":")[0] for f in failed[:3]))
        return "\n".join(lines)

    def _preview_and_notify(self, chat_id: str) -> None:
        """Background: GET a draft preview from RECON and post it to Telegram.
        Read-only — RECON's /api/preview stages nothing and marks nothing."""
        def _tg(text: str) -> None:
            try:
                _http_post(f"{TELEGRAM_URL}/send",
                           {"chat_id": chat_id, "text": text}, timeout=5)
            except Exception:
                pass
        try:
            result = _http_post(f"{self._RECON_URL}/api/preview", {}, timeout=45)
        except Exception as exc:
            _tg(f"❌ Preview unavailable: {exc}")
            return
        if not result.get("ok"):
            _tg("No leads available for preview. "
                "RECON is finding more — check back soon.")
            return
        _tg(
            "👁 OUTREACH PREVIEW\n\n"
            f"Business: {result.get('business_name','')}\n"
            f"Type: {result.get('business_type','')} | {result.get('city','')}\n"
            f"To: {result.get('email','')}\n\n"
            f"Subject: {result.get('subject','')}\n\n"
            f"{result.get('body','')}\n\n"
            "──────────────────────────\n"
            "This is a preview only — nothing has been queued.\n"
            "To queue this lead for approval run /outreach"
        )

    def _approve_all_and_notify(self, chat_id: str) -> None:
        """Background: approve every pending outreach draft (then every pending
        quote), posting one Telegram message per item plus a final summary.
        One failure is logged and skipped — the batch continues."""
        import time as _time

        def _tg(text: str) -> None:
            try:
                _http_post(f"{TELEGRAM_URL}/send",
                           {"chat_id": chat_id, "text": text}, timeout=5)
            except Exception:
                pass

        approved: list[str] = []

        # Outreach first (snapshot keys; each decision pops its own entry).
        with self._outreach_lock:
            outreach = dict(self._load_outreach_approvals())
        for row_id, entry in outreach.items():
            biz = entry.get("business_name", f"lead #{row_id}")
            try:
                self._handle_outreach_decision("approve", row_id, entry, chat_id)
                approved.append(biz)
            except Exception as exc:
                self.runtime.logger.error("approve_all outreach %s failed: %s", row_id, exc)
                _tg(f"❌ Failed to approve {biz}: {exc}")
            _time.sleep(0.5)

        # Quotes next (outreach store now drained, so _handle_approval routes
        # these to the quote branch).
        with self._approvals_lock:
            quotes = dict(self._load_approvals())
        for row_id, entry in quotes.items():
            biz = entry.get("business_name", f"quote #{row_id}")
            try:
                self._handle_approval("approve", row_id, chat_id)
                approved.append(biz)
            except Exception as exc:
                self.runtime.logger.error("approve_all quote %s failed: %s", row_id, exc)
                _tg(f"❌ Failed to approve {biz}: {exc}")
            _time.sleep(0.5)

        if approved:
            display  = approved[:3]
            more     = len(approved) - len(display)
            name_str = ", ".join(display) + (f" +{more} more" if more else "")
            _tg(f"✅ Sent {len(approved)}: {name_str}")
        else:
            _tg("✅ Nothing pending — queue is empty.")

    def _batch_approve_followups(self, chat_id: str) -> None:
        """RC-2: approve ONLY the pending follow-up entries (kind=='followup').
        Each goes through _handle_outreach_decision, so the per-lead safety
        re-check + business-hours gate still apply. 0.5s spacing between sends."""
        import time as _time
        with self._outreach_lock:
            store = dict(self._load_outreach_approvals())
        followups = {
            k: v for k, v in store.items()
            if isinstance(v, dict) and v.get("kind") == "followup"
        }
        if not followups:
            _tg_send(chat_id, "ℹ️ No pending follow-ups in queue.")
            return
        sent = failed = 0
        for row_id, entry in followups.items():
            try:
                self._handle_outreach_decision("approve", row_id, entry, chat_id)
                sent += 1
            except Exception as exc:
                failed += 1
                self.runtime.logger.error(
                    "batch followup approve %s failed: %s", row_id, exc
                )
            _time.sleep(0.5)
        _tg_send(
            chat_id,
            f"✅ <b>Follow-ups approved: {sent}</b>"
            f"{f'  ({failed} failed)' if failed else ''}\n"
            f"Leads advance to next touch or archive when done.",
            parse_mode="HTML",
        )

    def _cancel_followup_batch(self, chat_id: str) -> None:
        """RC-2: cancel staged follow-ups. Remove kind=='followup' entries from
        the approval store, then tell PULSE to clear followup_pending via
        /api/outreach/unqueue — leads stay DUE (NEVER exhausted)."""
        import urllib.request as _ur
        with self._outreach_lock:
            store = self._load_outreach_approvals()
            followup_keys = [
                k for k, v in store.items()
                if isinstance(v, dict) and v.get("kind") == "followup"
            ]
            if not followup_keys:
                _tg_send(chat_id, "ℹ️ No pending follow-ups to cancel.")
                return
            for k in followup_keys:
                store.pop(k, None)
            self._save_outreach_approvals(store)

        cleared = len(followup_keys)
        try:
            data = json.dumps({"row_ids": followup_keys}).encode()
            req = _ur.Request(
                f"{self._PULSE_URL}/api/outreach/unqueue",
                data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with _ur.urlopen(req, timeout=10) as r:
                result = json.loads(r.read().decode())
            cleared = result.get("cleared", cleared)
        except Exception as exc:
            self.runtime.logger.warning("PULSE unqueue failed: %s", exc)

        _tg_send(
            chat_id,
            f"🗑 <b>Follow-up batch cancelled.</b>\n"
            f"{cleared} lead(s) remain due for the next run.",
            parse_mode="HTML",
        )

    def _run_outreach_and_notify(self, chat_id: str, limit: int = 20) -> None:
        """Background: POST to RECON /api/outreach with chat_id and lead limit."""
        try:
            result = _http_post(
                f"{self._RECON_URL}/api/outreach",
                {"chat_id": chat_id, "limit": limit},
                timeout=10,
            )
            self.runtime.logger.info("CHIEF outreach started: %s", result)
        except Exception as exc:
            self.runtime.logger.error("CHIEF outreach dispatch failed: %s", exc)
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": f"❌ Could not start outreach: {exc}"},
                    timeout=5,
                )
            except Exception:
                pass

    def _run_send_outreach_and_notify(self, chat_id: str) -> None:
        """Background: POST to RECON /api/send_outreach — drafts and sends emails."""
        try:
            result = _http_post(
                f"{self._RECON_URL}/api/send_outreach",
                {"chat_id": chat_id, "limit": 5},
                timeout=10,
            )
            self.runtime.logger.info("CHIEF send_outreach started: %s", result)
        except Exception as exc:
            self.runtime.logger.error("CHIEF send_outreach dispatch failed: %s", exc)
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": f"❌ Could not start send_outreach: {exc}"},
                    timeout=5,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # RAM / swap status
    # ------------------------------------------------------------------

    def _social_start(self, topic: str, chat_id: str | None) -> str:
        try:
            payload = json.dumps({"topic": topic, "campaign_duration": "daily"})
            req = urllib.request.Request(
                "http://127.0.0.1:8011/start",
                data=payload.encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            session_id = data.get("session_id", "?")
            return (
                f"📱 Social campaign started\n\n"
                f"Topic: {topic}\n"
                f"Session: {session_id}\n"
                f"Status: generating — check /session/{session_id} for results."
            )
        except Exception as exc:
            return f"❌ Social operator unreachable: {exc}"

    def _ram_status(self) -> str:
        import subprocess
        import re
        try:
            swap_out = subprocess.run(
                ["sysctl", "vm.swapusage"],
                capture_output=True, text=True,
            ).stdout.strip()

            total_m = re.search(r'total = ([\d.]+)M', swap_out)
            used_m  = re.search(r'used = ([\d.]+)M',  swap_out)
            free_m  = re.search(r'free = ([\d.]+)M',  swap_out)

            total_mb = float(total_m.group(1)) if total_m else 0
            used_mb  = float(used_m.group(1))  if used_m  else 0
            free_mb  = float(free_m.group(1))  if free_m  else 0
            pct      = (used_mb / total_mb * 100) if total_mb > 0 else 0

            vm = subprocess.run(
                ["vm_stat"], capture_output=True, text=True,
            ).stdout

            page_size = 16384  # 16 KB pages on Apple Silicon

            def pages(label: str) -> int:
                m = re.search(label + r'[^:]*:\s+([\d]+)', vm)
                return int(m.group(1)) if m else 0

            free_gb       = pages("Pages free")       * page_size / 1e9
            inactive_gb   = pages("Pages inactive")   * page_size / 1e9
            spec_gb       = pages("Pages speculative") * page_size / 1e9
            available_gb  = free_gb + inactive_gb + spec_gb
            active_gb     = pages("Pages active")     * page_size / 1e9
            wired_gb      = pages("Pages wired down") * page_size / 1e9
            compressed_gb = pages("Pages occupied by compressor") * page_size / 1e9

            if used_mb < 500:
                swap_icon, swap_status = "✅", "healthy"
            elif used_mb < 800:
                swap_icon, swap_status = "⚠️", "elevated"
            else:
                swap_icon, swap_status = "🔴", "CRITICAL"

            if used_mb >= 800:
                note = "🔴 SWAP CRITICAL — restart recommended"
            elif used_mb >= 500:
                note = "⚠️ Swap elevated — monitor closely"
            else:
                note = "✅ System healthy"

            return (
                f"🖥️ RAM STATUS\n\n"
                f"SWAP {swap_icon} {swap_status}\n"
                f"  Used:  {used_mb:.0f} MB / {total_mb:.0f} MB ({pct:.0f}%)\n"
                f"  Free:  {free_mb:.0f} MB\n\n"
                f"MEMORY\n"
                f"  Available:  {available_gb:.1f} GB\n"
                f"  Active:     {active_gb:.1f} GB\n"
                f"  Wired:      {wired_gb:.1f} GB\n"
                f"  Compressed: {compressed_gb:.1f} GB\n\n"
                f"{note}"
            )
        except Exception as exc:
            return f"❌ RAM check failed: {exc}"

    # ------------------------------------------------------------------
    # Menu system + callback handler
    # ------------------------------------------------------------------

    def _handle_menu(self, chat_id: str | None) -> str:
        """Send the main menu as an inline keyboard. Returns ack text for non-Telegram callers."""
        counts  = _get_menu_counts()
        markup  = _main_menu_keyboard(counts["pending"])
        if chat_id:
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {
                        "chat_id":      chat_id,
                        "text":         _MENU_TEXT,
                        "parse_mode":   "HTML",
                        "reply_markup": markup,
                    },
                    timeout=8,
                )
                return "Menu sent."
            except Exception as exc:
                self.runtime.logger.warning("CHIEF /menu send failed: %s", exc)
                return "❌ Could not send menu."
        return "Use /menu from Telegram to see the interactive button menu."

    def _trigger_mission(self, mission_id: str, workflow_id: str, chat_id: str | None) -> str:
        """POST to MissionManager to trigger a workflow. Returns status text."""
        url = f"{MISSION_MANAGER_URL}/api/missions/{mission_id}/run/{workflow_id}"
        try:
            data = _http_post(url, {}, timeout=15)
            run_id = data.get("mission_run_id") or data.get("run_id") or ""
            status = data.get("status") or "started"
            label  = workflow_id.replace("_", " ").title()
            return (
                f"✅ {label} started\n"
                f"Status: {status}\n"
                f"Run: {run_id[:8]}..." if run_id else f"✅ {label} started\nStatus: {status}"
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if hasattr(exc, "read") else str(exc)
            return f"❌ {workflow_id}: HTTP {exc.code} — {body[:120]}"
        except Exception as exc:
            return f"❌ Could not start {workflow_id}: {exc}"

    def _handle_callback_query(self, payload: dict) -> str:
        """Route an inline button callback_data string to the right action."""
        data       = payload.get("data", "")
        chat_id    = str(payload.get("chat_id", ""))
        message_id = payload.get("message_id")

        def _edit(text: str, markup: dict | None = None) -> None:
            tg_payload: dict = {
                "chat_id":    chat_id,
                "message_id": message_id,
                "text":       text,
                "parse_mode": "HTML",
            }
            if markup:
                tg_payload["reply_markup"] = markup
            try:
                _http_post(f"{TELEGRAM_URL}/edit_message", tg_payload, timeout=8)
            except Exception as exc:
                self.runtime.logger.warning("CHIEF edit_message failed: %s", exc)

        def _send(text: str) -> None:
            try:
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": chat_id, "text": text},
                    timeout=8,
                )
            except Exception as exc:
                self.runtime.logger.warning("CHIEF send after callback failed: %s", exc)

        # ── Navigation ────────────────────────────────────────────────
        if data in ("menu_main", "back_to_menu"):
            _edit(_MENU_TEXT, _main_menu_keyboard(_get_menu_counts()["pending"]))
            return "ok"
        if data == "menu_sales":
            _edit("💼 <b>Sales</b>\nSelect an area:", _sales_menu_keyboard())
            return "ok"
        if data == "menu_finances":
            _edit("💰 <b>Finances</b>\nSelect an area:", _finances_menu_keyboard())
            return "ok"
        if data == "menu_marketing":
            _edit("📣 <b>Marketing</b>\nSelect a platform:", _marketing_menu_keyboard())
            return "ok"
        if data == "menu_management":
            _edit("🏃 <b>Management</b>\nSelect an area:", _management_menu_keyboard())
            return "ok"
        if data == "menu_recon":
            _edit("📡 <b>RECON</b>\nLead generation:", _recon_menu_keyboard())
            return "ok"
        if data == "menu_scout":
            _edit("🎯 <b>SCOUT</b>\nQualification:", _scout_menu_keyboard())
            return "ok"
        if data == "menu_email":
            _edit("📧 <b>EMAIL</b>\nOutreach &amp; inbox:", _email_menu_keyboard())
            return "ok"
        if data == "menu_crm":
            _edit("👥 <b>CRM</b>\nCustomer relationships:", _crm_menu_keyboard())
            return "ok"
        if data == "menu_demo":
            _edit("🎪 <b>DEMO</b>", _demo_menu_keyboard())
            return "ok"
        if data == "menu_quote":
            _edit("📋 <b>QUOTE</b>\nProposals &amp; billing:", _quote_menu_keyboard())
            return "ok"
        if data == "menu_x":
            _edit("𝕏 <b>X (Twitter)</b>", _x_menu_keyboard())
            return "ok"
        if data == "menu_facebook":
            _edit("📘 <b>Facebook</b>", _facebook_menu_keyboard())
            return "ok"
        if data == "menu_instagram":
            _edit("📸 <b>Instagram</b>", _instagram_menu_keyboard())
            return "ok"
        if data == "menu_campaigns":
            _edit("🚀 <b>Campaigns</b>", _campaigns_menu_keyboard())
            return "ok"
        if data == "menu_daily_ops":
            _edit("📅 <b>Daily Ops</b>", _daily_ops_menu_keyboard())
            return "ok"
        if data == "menu_orchestration":
            _edit("⚙️ <b>Orchestration</b>", _orchestration_menu_keyboard())
            return "ok"
        if data == "menu_reports":
            _edit("📊 <b>Reports</b>", _reports_menu_keyboard())
            return "ok"
        if data == "menu_system":
            _edit("⚙️ <b>System</b>", _system_menu_keyboard())
            return "ok"
        if data == "menu_approve":
            data = "do_approve_all"
        if data == "menu_inbox":
            data = "do_inbox"

        # ── Find Work actions ─────────────────────────────────────────
        if data == "do_new_leads":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("find_work", "new_lead_search", chat_id)),
                daemon=True, name="chief-cb-recon",
            ).start()
            _edit("🔍 Searching for new leads — stand by...")
            return "ok"
        if data == "do_inbound":
            _edit("📥 *Inbound Lead*\nSend a lead description or paste a URL to qualify with /scout.")
            return "ok"
        if data == "do_outreach":
            if not chat_id:
                return "no chat_id"
            threading.Thread(
                target=self._run_outreach_and_notify, args=(chat_id, 20),
                daemon=True, name="chief-cb-outreach",
            ).start()
            _edit("📧 Pulling top 20 leads for outreach — stand by...")
            return "ok"
        if data == "do_followups":
            _edit(self._followups_snapshot())
            return "ok"
        if data == "do_replies":
            _edit(self._replies_snapshot())
            return "ok"
        if data == "do_reactivate":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("find_work", "lead_reactivation", chat_id)),
                daemon=True, name="chief-cb-reactivate",
            ).start()
            _edit("♻️ Triggering lead reactivation — stand by...")
            return "ok"
        if data == "do_recon_start":
            threading.Thread(
                target=lambda: _tg_send(chat_id, self._recon_start()),
                daemon=True, name="chief-cb-recon-start",
            ).start()
            _edit("🚀 Starting RECON scraper — stand by...")
            return "ok"

        # ── Win Work actions ──────────────────────────────────────────
        if data == "do_quote":
            _edit("📄 *Quote Builder*\nType: /quote [description] or /quote_N to draft for a specific lead.")
            return "ok"
        if data == "do_quote_fu":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("win_work", "quote_followup", chat_id)),
                daemon=True, name="chief-cb-quote-fu",
            ).start()
            _edit("🔄 Triggering quote follow-up — stand by...")
            return "ok"
        if data == "do_close":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("win_work", "close_the_job", chat_id)),
                daemon=True, name="chief-cb-close",
            ).start()
            _edit("✅ Triggering close job workflow — stand by...")
            return "ok"
        if data == "do_invoice":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("win_work", "get_paid", chat_id)),
                daemon=True, name="chief-cb-invoice",
            ).start()
            _edit("💰 Triggering invoice follow-up — stand by...")
            return "ok"
        if data == "do_funnel":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("win_work", "sales_funnel", chat_id)),
                daemon=True, name="chief-cb-funnel",
            ).start()
            _edit("🎯 Triggering sales funnel — stand by...")
            return "ok"

        # ── Run Work actions ──────────────────────────────────────────
        if data == "do_brief":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "morning_brief", chat_id)),
                daemon=True, name="chief-cb-brief",
            ).start()
            _edit("🌅 Triggering morning brief — stand by...")
            return "ok"
        if data == "do_schedule":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "schedule_check", chat_id)),
                daemon=True, name="chief-cb-schedule",
            ).start()
            _edit("📅 Triggering schedule check — stand by...")
            return "ok"
        if data == "do_blockers":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "blocker_watch", chat_id)),
                daemon=True, name="chief-cb-blockers",
            ).start()
            _edit("🚧 Triggering blocker watch — stand by...")
            return "ok"
        if data == "do_eod":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "end_of_day_report", chat_id)),
                daemon=True, name="chief-cb-eod",
            ).start()
            _edit("🌙 Triggering end of day report — stand by...")
            return "ok"
        if data == "do_review":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "review_request", chat_id)),
                daemon=True, name="chief-cb-review",
            ).start()
            _edit("⭐ Triggering review request — stand by...")
            return "ok"
        if data == "do_weekly":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "weekly_summary", chat_id)),
                daemon=True, name="chief-cb-weekly",
            ).start()
            _edit("📊 Triggering weekly summary — stand by...")
            return "ok"

        # ── Cross-mission ─────────────────────────────────────────────
        if data == "do_status":
            _edit(self._status_summary())
            return "ok"
        if data == "do_approve_all":
            if not chat_id:
                return "no chat_id"
            n_out = len(self._load_outreach_approvals())
            n_q   = len(self._load_approvals())
            if n_out + n_q == 0:
                _edit("✅ Nothing pending — queue is empty.")
                return "ok"
            threading.Thread(
                target=self._approve_all_and_notify, args=(chat_id,),
                daemon=True, name="chief-cb-approveall",
            ).start()
            _edit(f"⚙️ Approving {n_out + n_q} pending item(s)... Stand by.")
            return "ok"
        if data == "do_inbox":
            _send(self._inbox_check(1))
            return "ok"
        if data == "do_help":
            from cascadia.chief.commands import build_help_text as _bht
            _send(_bht())
            return "ok"

        # ── Social approval buttons ────────────────────────────────────
        if data.startswith("xfb_approve"):
            # format: "xfb_approve_<x_id>_<fb_id>"
            parts = data.split("_")
            x_id  = int(parts[2]) if len(parts) == 4 and parts[2].isdigit() else None
            fb_id = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else None
            _edit("⏳ Posting to X + Facebook...")
            try:
                res_x = _http_post("http://localhost:8011/api/x/approve",
                                   {"post_id": x_id} if x_id else {}, timeout=30)
            except Exception as exc:
                res_x = {"success": False, "error": str(exc)[:60]}
            try:
                res_fb = _http_post("http://localhost:8011/api/fb/approve",
                                    {"post_id": fb_id} if fb_id else {}, timeout=30)
            except Exception as exc:
                res_fb = {"success": False, "error": str(exc)[:60]}
            x_ok  = res_x.get("success")
            fb_ok = res_fb.get("success")
            if x_ok and fb_ok:
                _edit("✅ Posted to X + Facebook")
            elif x_ok:
                _edit(f"✅ X posted · ❌ FB failed: {res_fb.get('error','unknown')[:40]}")
            elif fb_ok:
                _edit(f"❌ X failed · ✅ FB posted: {res_x.get('error','unknown')[:40]}")
            else:
                _edit("❌ Both failed")
            return "ok"
        if data.startswith("xfb_skip"):
            # format: "xfb_skip_<x_id>_<fb_id>"
            parts = data.split("_")
            x_id  = int(parts[2]) if len(parts) == 4 and parts[2].isdigit() else None
            fb_id = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else None
            try:
                _http_post("http://localhost:8011/api/x/skip",
                           {"post_id": x_id} if x_id else {}, timeout=10)
            except Exception:
                pass
            try:
                _http_post("http://localhost:8011/api/fb/skip",
                           {"post_id": fb_id} if fb_id else {}, timeout=10)
            except Exception:
                pass
            _edit("⏭ Both skipped")
            return "ok"
        if data.startswith("x_approve"):
            parts = data.split("_")
            post_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
            _edit("⏳ Posting to X...")
            _edit(self._x_command("approve", post_id=post_id))
            return "ok"
        if data.startswith("x_skip"):
            parts = data.split("_")
            post_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
            _edit(self._x_command("skip", post_id=post_id))
            return "ok"
        if data.startswith("fb_approve"):
            parts = data.split("_")
            post_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
            _edit("⏳ Posting to Facebook...")
            _edit(self._fb_command("approve", post_id=post_id))
            return "ok"
        if data.startswith("fb_skip"):
            parts = data.split("_")
            post_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
            _edit(self._fb_command("skip", post_id=post_id))
            return "ok"
        if data.startswith("x_gen_image"):
            # x_gen_image_{id} → ['x','gen','image','{id}']
            parts = data.split("_")
            post_id = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else None
            if not _gen_acquire(data):
                _edit("⏳ Already generating that image — hold on…")
                return "ok"
            _edit("🎨 On it — preview coming shortly…")
            def _run_xgen():
                try:
                    _edit(self._x_gen_image_command(post_id))
                finally:
                    _gen_release(data)
            threading.Thread(target=_run_xgen, daemon=True, name="chief-cb-xgen").start()
            return "ok"
        if data.startswith("fb_gen_image"):
            parts = data.split("_")
            post_id = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else None
            if not _gen_acquire(data):
                _edit("⏳ Already generating that image — hold on…")
                return "ok"
            _edit("🎨 On it — preview coming shortly…")
            def _run_fbgen():
                try:
                    _edit(self._fb_gen_image_command(post_id))
                finally:
                    _gen_release(data)
            threading.Thread(target=_run_fbgen, daemon=True, name="chief-cb-fbgen").start()
            return "ok"
        if data == "ig_approve":
            _edit("⏳ Posting to Instagram...")
            _edit(self._ig_command("approve"))
            return "ok"
        if data == "ig_skip":
            _edit(self._ig_command("skip"))
            return "ok"
        if data == "ig_gen_image":
            if not _gen_acquire(data):
                _edit("⏳ Already generating that image — hold on…")
                return "ok"
            _edit("🎨 On it — preview coming shortly…")
            def _run_iggen():
                try:
                    _edit(self._ig_gen_image_command())
                finally:
                    _gen_release(data)
            threading.Thread(target=_run_iggen, daemon=True, name="chief-cb-iggen").start()
            return "ok"
        if data == "ig_regen":
            if not _gen_acquire(data):
                _edit("⏳ Already regenerating — hold on…")
                return "ok"
            _edit("🔄 On it — a fresh image is coming…")
            def _run_igregen():
                try:
                    _edit(self._ig_regen_command())
                finally:
                    _gen_release(data)
            threading.Thread(target=_run_igregen, daemon=True, name="chief-cb-igregen").start()
            return "ok"

        # ── Outreach/followup approval buttons ─────────────────────────
        if data.startswith("approve:") or data.startswith("reject:"):
            action, row_id = data.split(":", 1)
            _edit(self._handle_approval(action, row_id, chat_id))
            return "ok"

        # ── Performance operator buttons ──────────────────────────────
        # ── Daily Ops menu buttons ────────────────────────────────────
        if data == "cmd_brief":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "morning_brief", chat_id)),
                daemon=True, name="chief-cb-cmd-brief",
            ).start()
            _edit("🌅 Triggering morning brief — stand by...")
            return "ok"
        if data == "cmd_schedule":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "schedule_check", chat_id)),
                daemon=True, name="chief-cb-cmd-schedule",
            ).start()
            _edit("📅 Triggering schedule check — stand by...")
            return "ok"
        if data == "cmd_blockers":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "blocker_watch", chat_id)),
                daemon=True, name="chief-cb-cmd-blockers",
            ).start()
            _edit("🚧 Triggering blocker watch — stand by...")
            return "ok"
        if data == "cmd_eod":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "end_of_day_report", chat_id)),
                daemon=True, name="chief-cb-cmd-eod",
            ).start()
            _edit("🌙 Triggering end of day report — stand by...")
            return "ok"
        if data == "cmd_weekly":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "weekly_summary", chat_id)),
                daemon=True, name="chief-cb-cmd-weekly",
            ).start()
            _edit("📆 Triggering weekly summary — stand by...")
            return "ok"
        if data == "cmd_missions":
            _edit(self._missions_summary())
            return "ok"

        if data == "fu_approve_all":
            threading.Thread(
                target=self._batch_approve_followups, args=(chat_id,),
                daemon=True, name="chief-cb-fu-approve-all",
            ).start()
            _edit("⚙️ Approving all follow-ups — stand by...")
            return "ok"
        if data == "fu_cancel":
            threading.Thread(
                target=self._cancel_followup_batch, args=(chat_id,),
                daemon=True, name="chief-cb-fu-cancel",
            ).start()
            _edit("🗑 Cancelling follow-up batch...")
            return "ok"

        if data == "cmd_update_cancel":
            _edit("❌ Update cancelled.")
            return "ok"
        if data == "cmd_update_confirm":
            # Owner-only guard (defence in depth — the command already gated).
            if str(chat_id) != str(self._OWNER_CHAT):
                _edit("🔒 /update is owner-only.")
                return "ok"
            threading.Thread(
                target=self._run_update_and_notify, args=(chat_id,),
                daemon=True, name="chief-cb-update",
            ).start()
            _edit("🔄 Update started… this may take 2-3 minutes. I'll report when it finishes.")
            return "ok"

        if data == "cmd_performance":
            _edit(self._performance_command("morning"))
            return "ok"
        if data == "cmd_performance_kpis":
            _edit(self._performance_command("snapshot"))
            return "ok"
        if data == "cmd_performance_noon":
            _edit(self._performance_command("noon"))
            return "ok"
        if data == "cmd_performance_history":
            _edit(self._performance_command("history"))
            return "ok"

        # ── Code operator menu buttons ─────────────────────────────────
        if data == "cmd_code":
            _edit(self._code_command("", chat_id=chat_id))
            return "ok"
        if data == "cmd_code_list":
            _edit(self._code_command("list", chat_id=chat_id))
            return "ok"

        # ── Code operator proposal buttons ────────────────────────────
        if data.startswith("code_approve_"):
            project_num = data[len("code_approve_"):]
            try:
                req = urllib.request.Request(
                    f"http://localhost:8004/api/project/{project_num}/approve",
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    json.loads(r.read())
                _edit(f"⚙️ Project {project_num} approved — executing now...")
            except Exception as exc:
                _edit(f"❌ Approval failed: {str(exc)[:80]}")
            return "ok"

        if data.startswith("code_edit_"):
            project_num = data[len("code_edit_"):]
            _edit(
                f"✏️ <b>Edit Project {project_num}</b>\n\n"
                f"Type your adjustments as:\n"
                f"/code edit {project_num} &lt;what to change&gt;"
            )
            return "ok"

        if data.startswith("code_cancel_"):
            project_num = data[len("code_cancel_"):]
            try:
                req = urllib.request.Request(
                    f"http://localhost:8004/api/project/{project_num}/cancel",
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    pass
            except Exception:
                pass
            _edit(f"🗑 Project {project_num} cancelled.")
            return "ok"

        # ── RECON / FIND WORK ─────────────────────────────────────────
        if data == "cmd_recon":
            _edit("📡 <b>RECON</b>\nType /recon &lt;trade&gt; &lt;city&gt; to search for leads.\nExample: /recon HVAC Houston TX")
            return "ok"
        if data == "cmd_leads":
            _edit(self._pipeline_snapshot())
            return "ok"
        if data == "cmd_pipeline":
            _edit(self._pipeline_snapshot())
            return "ok"
        if data == "cmd_outreach":
            if not chat_id:
                return "no chat_id"
            threading.Thread(
                target=self._run_outreach_and_notify, args=(chat_id, 20),
                daemon=True, name="chief-cb-cmd-outreach",
            ).start()
            _edit("📤 Pulling top 20 leads for outreach — stand by...")
            return "ok"
        if data == "cmd_followups":
            _edit(self._followups_snapshot())
            return "ok"
        if data == "cmd_replies":
            _edit(self._replies_snapshot())
            return "ok"
        if data == "cmd_reactivate":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("find_work", "lead_reactivation", chat_id)),
                daemon=True, name="chief-cb-cmd-reactivate",
            ).start()
            _edit("♻️ Triggering lead reactivation — stand by...")
            return "ok"
        if data == "cmd_recon_start":
            threading.Thread(
                target=lambda: _send(self._recon_start()),
                daemon=True, name="chief-cb-cmd-recon-start",
            ).start()
            _edit("🚀 Starting RECON scraper — stand by...")
            return "ok"
        if data == "cmd_recon_stop":
            _edit(self._recon_stop())
            return "ok"
        if data == "cmd_approve_all":
            if not chat_id:
                return "no chat_id"
            n_out = len(self._load_outreach_approvals())
            n_q   = len(self._load_approvals())
            if n_out + n_q == 0:
                _edit("✅ Nothing pending — queue is empty.")
                return "ok"
            threading.Thread(
                target=self._approve_all_and_notify, args=(chat_id,),
                daemon=True, name="chief-cb-cmd-approve-all",
            ).start()
            _edit(f"⚙️ Approving {n_out + n_q} pending item(s)... Stand by.")
            return "ok"

        # ── SCOUT / QUALIFIER ──────────────────────────────────────────
        if data == "cmd_scout":
            _edit("🎯 <b>SCOUT</b>\nType /scout &lt;lead description&gt; to qualify a lead.\nExample: /scout John Smith HVAC contractor reply")
            return "ok"
        if data == "cmd_funnel":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("win_work", "sales_funnel", chat_id)),
                daemon=True, name="chief-cb-cmd-funnel",
            ).start()
            _edit("🎯 Triggering sales funnel — stand by...")
            return "ok"

        # ── EMAIL ──────────────────────────────────────────────────────
        if data == "cmd_email_approve":
            _edit(self._email_approve_command())
            return "ok"
        if data == "cmd_email_skip":
            _edit(self._email_skip_command())
            return "ok"
        if data == "cmd_inbox_check":
            threading.Thread(
                target=lambda: _send(self._inbox_check(1)),
                daemon=True, name="chief-cb-cmd-inbox",
            ).start()
            _edit("📥 Checking inbox — stand by...")
            return "ok"
        if data == "cmd_email_status":
            _edit(self._email_status())
            return "ok"

        # ── CRM ────────────────────────────────────────────────────────
        if data == "cmd_crm":
            _edit(self._crm_command(""))
            return "ok"
        if data == "cmd_crm_sleep":
            _edit(self._crm_command("sleep"))
            return "ok"
        if data == "cmd_crm_wake":
            _edit(self._crm_command("wake"))
            return "ok"

        # ── DEMO ───────────────────────────────────────────────────────
        if data == "cmd_demo_status":
            _edit(self._demo_command("status"))
            return "ok"

        # ── WIN WORK / QUOTE ───────────────────────────────────────────
        if data == "cmd_quote":
            _edit("📋 <b>Quote Builder</b>\nType /quote [description] or /quote_N to draft for a specific lead.")
            return "ok"
        if data == "cmd_close":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("win_work", "close_the_job", chat_id)),
                daemon=True, name="chief-cb-cmd-close",
            ).start()
            _edit("✅ Triggering close job workflow — stand by...")
            return "ok"
        if data == "cmd_invoice":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("win_work", "get_paid", chat_id)),
                daemon=True, name="chief-cb-cmd-invoice",
            ).start()
            _edit("💰 Triggering invoice follow-up — stand by...")
            return "ok"
        if data == "cmd_review":
            threading.Thread(
                target=lambda: _send(self._trigger_mission("run_work", "review_request", chat_id)),
                daemon=True, name="chief-cb-cmd-review",
            ).start()
            _edit("⭐ Triggering review request — stand by...")
            return "ok"

        # ── X / TWITTER ────────────────────────────────────────────────
        if data == "cmd_x_approve":
            _edit(self._x_command("approve"))
            return "ok"
        if data == "cmd_x_skip":
            _edit(self._x_command("skip"))
            return "ok"
        if data == "cmd_approve_all_x":
            _edit(self._approve_all_platform("x"))
            return "ok"
        if data == "cmd_x_post_now":
            _edit(self._post_now_command("x"))
            return "ok"
        if data == "cmd_x_status":
            _edit(self._x_command("status"))
            return "ok"

        # ── FACEBOOK ───────────────────────────────────────────────────
        if data == "cmd_fb_approve":
            _edit(self._fb_command("approve"))
            return "ok"
        if data == "cmd_fb_skip":
            _edit(self._fb_command("skip"))
            return "ok"
        if data == "cmd_approve_all_fb":
            _edit(self._approve_all_platform("facebook"))
            return "ok"
        if data == "cmd_fb_post_now":
            _edit(self._post_now_command("fb"))
            return "ok"
        if data == "cmd_fb_status":
            _edit(self._fb_command("status"))
            return "ok"

        # ── INSTAGRAM ──────────────────────────────────────────────────
        if data == "cmd_ig_approve":
            _edit(self._ig_command("approve"))
            return "ok"
        if data == "cmd_ig_skip":
            _edit(self._ig_command("skip"))
            return "ok"
        if data == "cmd_approve_all_ig":
            _edit(self._approve_all_platform("instagram"))
            return "ok"
        if data == "cmd_ig_post_now":
            _edit(self._post_now_command("ig"))
            return "ok"
        if data == "cmd_ig_gen_image":
            threading.Thread(
                target=lambda: _send(self._ig_gen_image_command()),
                daemon=True, name="chief-cb-cmd-ig-gen",
            ).start()
            _edit("🖼 Generating image — stand by (~30s)...")
            return "ok"
        if data == "cmd_ig_regen":
            threading.Thread(
                target=lambda: _send(self._ig_regen_command()),
                daemon=True, name="chief-cb-cmd-ig-regen",
            ).start()
            _edit("🔄 Regenerating image — stand by (~30s)...")
            return "ok"
        if data == "cmd_ig_status":
            _edit(self._ig_command("status"))
            return "ok"
        if data == "cmd_clear_image":
            _edit(self._clear_image_command())
            return "ok"

        # ── CAMPAIGNS ──────────────────────────────────────────────────
        if data == "cmd_social":
            threading.Thread(
                target=lambda: _send(self._social_start("daily social campaign", chat_id)),
                daemon=True, name="chief-cb-cmd-social",
            ).start()
            _edit("🚀 Starting social campaign — stand by...")
            return "ok"
        if data == "cmd_social_generate":
            threading.Thread(
                target=lambda: _send(self._social_generate_command()),
                daemon=True, name="chief-cb-cmd-social-gen",
            ).start()
            _edit("🚀 Generating social content — stand by...")
            return "ok"

        # ── ORCHESTRATION ──────────────────────────────────────────────
        if data == "cmd_status":
            _edit(self._status_summary())
            return "ok"
        if data == "cmd_operators":
            _edit(build_operators_text(OPERATOR_CATALOG))
            return "ok"
        if data == "cmd_version":
            _edit(self._version_info())
            return "ok"

        # ── REPORTS ────────────────────────────────────────────────────
        if data == "cmd_startup_report":
            threading.Thread(
                target=lambda: _send(self._build_startup_report()),
                daemon=True, name="chief-cb-cmd-startup",
            ).start()
            _edit("📊 Running startup report — stand by...")
            return "ok"
        if data == "cmd_ram":
            _edit(self._ram_status())
            return "ok"
        if data == "cmd_token":
            _edit(self._meter_command("default"))
            return "ok"
        if data == "cmd_token_week":
            _edit(self._meter_command("week"))
            return "ok"
        if data == "cmd_token_month":
            _edit(self._meter_command("month"))
            return "ok"

        # ── SYSTEM ─────────────────────────────────────────────────────
        if data == "cmd_wizard":
            _edit(self._cmd_wizard())
            return "ok"
        if data == "cmd_help":
            _edit(build_help_text())
            return "ok"
        if data == "cmd_check_credentials":
            _edit(self._check_credentials_report(False))
            return "ok"
        if data == "cmd_check_credentials_live":
            threading.Thread(
                target=lambda: _send(self._check_credentials_report(True)),
                daemon=True, name="chief-cb-cmd-creds-live",
            ).start()
            _edit("🔑 Checking credentials live — stand by...")
            return "ok"

        self.runtime.logger.warning("CHIEF unknown callback_data: %s", data)
        return "unknown"

    def handle_callback(self, payload: dict) -> tuple[int, dict]:
        """POST /callback — receives inline keyboard taps from Telegram connector."""
        chat_id = str(payload.get("chat_id", ""))
        owner   = "1535010257"
        if chat_id != owner:
            self.runtime.logger.warning("CHIEF callback: unauthorized chat_id=%s", chat_id)
            return 403, {"ok": False, "error": "unauthorized"}
        result = self._handle_callback_query(payload)
        return 200, {"ok": True, "result": result}

    # ------------------------------------------------------------------
    # CREW self-registration
    # ------------------------------------------------------------------

    def _try_register_with_crew(self, retries: int = 3) -> None:
        payload = json.dumps({
            "operator_id": "chief",
            "name": "chief",
            "display_name": "CHIEF",
            "description": (
                "Zyrcon orchestrator — task routing and worker assignment"
            ),
            "port": CHIEF_PORT,
            "type": "system",
            "autonomy_level": "autonomous",
            "capabilities": [
                "task.orchestrate",
                "run.execute",
                "mission.select",
                "operator.assign",
                "report.request",
            ],
            "health_hook": "/health",
        }).encode()
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    f"{CREW_URL}/register",
                    data=payload,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3)
                self.runtime.logger.info("CHIEF registered with CREW")
                return
            except Exception as exc:
                self.runtime.logger.warning(
                    "CHIEF: CREW registration attempt %d/%d failed: %s",
                    attempt + 1, retries, exc,
                )
                if attempt < retries - 1:
                    time.sleep(2)
        self.runtime.logger.warning(
            "CHIEF: CREW registration failed after %d attempts", retries
        )

    # ------------------------------------------------------------------
    # Chat handlers (absorbed from BELL)
    # ------------------------------------------------------------------

    def _build_chat_workflow_definitions(self) -> dict:
        """Build built-in workflow definitions. Replaces _StitchShim from BELL."""
        lead = WorkflowDefinition(
            workflow_id='lead_follow_up',
            name='Lead Follow-Up',
            description='Parse lead, enrich, draft email, approval gate, send, log CRM.',
            steps=[
                WorkflowStep('parse_lead',     'main_operator',  'parse_lead'),
                WorkflowStep('enrich_company', 'main_operator',  'enrich_company'),
                WorkflowStep('draft_email',    'main_operator',  'draft_email'),
                WorkflowStep('send_email',     'gmail_operator', 'email.send', on_failure='stop'),
                WorkflowStep('log_crm',        'main_operator',  'crm.write'),
            ],
        )
        return {'lead_follow_up': lead}

    def chat_start_session(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /session/start — open a new chat session."""
        session_id = f'bell_{uuid.uuid4().hex[:10]}'
        tenant_id = payload.get('tenant_id', 'default')
        with self._chat_lock:
            session = ChatSession(session_id, tenant_id)
            self._chat_sessions[session_id] = session
        self.runtime.logger.info('CHIEF chat session started: %s', session_id)
        return 201, {'session_id': session_id, 'tenant_id': tenant_id}

    def chat_receive_message(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /message — receive a human message and run a workflow."""
        session_id = payload.get('session_id', '')
        content = payload.get('content', '')
        if not session_id or not content:
            return 400, {'error': 'session_id and content required'}
        with self._chat_lock:
            session = self._chat_sessions.get(session_id)
            if session is None:
                return 404, {'error': 'session not found'}
            session.add_message('user', content, payload.get('metadata'))

        if content.startswith('/settings'):
            try:
                from cascadia.settings.chat_assistant import SettingsChatAssistant
                context = {
                    'operator': payload.get('operator_id', session_id),
                    'business_type': payload.get('business_type', 'general'),
                }
                result = SettingsChatAssistant().handle(content, context)
                return 200, result
            except Exception as exc:
                self.runtime.logger.warning('SettingsChatAssistant error: %s', exc)

        workflow_id = payload.get('workflow_id', 'lead_follow_up')
        definition = self._chat_wf_definitions.get(workflow_id)
        if definition is None:
            return 400, {'error': f'unknown workflow: {workflow_id}'}

        try:
            result = self._chat_wf_runtime.execute(workflow_id, definition, {
                'session_id': session_id,
                'content': content,
                'tenant_id': payload.get('tenant_id', session.tenant_id),
                'goal': payload.get('goal', f'Lead follow-up from chat session {session_id}'),
                'sender': 'chief',
            })
        except Exception as exc:
            self.runtime.logger.error('CHIEF chat workflow execution failed: %s', exc)
            return 500, {'error': str(exc)}

        result_dict = result.to_dict()
        run_id = result_dict['run_id']
        with self._chat_lock:
            if run_id not in session.linked_run_ids:
                session.linked_run_ids.append(run_id)
            approval_id = result_dict.get('pending_approval_id')
            if approval_id is not None and approval_id not in session.pending_approvals:
                session.pending_approvals.append(approval_id)
            assistant_msg = result_dict.get('assistant_message') or result_dict.get('draft_preview', '')
            if assistant_msg:
                session.add_message('assistant', assistant_msg)

        self.runtime.logger.info(
            'CHIEF chat run %s — state: %s step: %s',
            run_id, result_dict['run_state'], result_dict['current_step'],
        )
        return 202, result_dict

    def chat_receive_approval(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /approve — record an approval decision and resume the run."""
        session_id  = payload.get('session_id', '')
        approval_id = payload.get('approval_id')
        decision    = payload.get('decision', '')
        reason      = payload.get('reason', '')
        actor       = payload.get('actor', 'operator')
        run_id      = payload.get('run_id', '')

        if decision not in ('approved', 'denied'):
            return 400, {'error': 'decision must be approved or denied'}
        if approval_id is None:
            return 400, {'error': 'approval_id required'}

        try:
            self._chat_wf_runtime.approvals.record_decision(
                int(approval_id), decision, actor, reason
            )
        except Exception as exc:
            self.runtime.logger.error('CHIEF chat approval record failed: %s', exc)
            return 500, {'error': f'failed to record decision: {exc}'}

        with self._chat_lock:
            session = self._chat_sessions.get(session_id)
            if session and approval_id in session.pending_approvals:
                session.pending_approvals.remove(approval_id)

        resume_result = None
        if decision == 'approved':
            effective_run_id = run_id
            if not effective_run_id:
                with self._chat_lock:
                    if session and session.linked_run_ids:
                        effective_run_id = session.linked_run_ids[-1]
            if effective_run_id:
                definition = self._chat_wf_definitions.get('lead_follow_up')
                if definition:
                    try:
                        result = self._chat_wf_runtime.execute(
                            'lead_follow_up', definition, {'run_id': effective_run_id}
                        )
                        resume_result = result.to_dict()
                        with self._chat_lock:
                            if session:
                                msg = resume_result.get('assistant_message') or resume_result.get('draft_preview', '')
                                if msg:
                                    session.add_message('assistant', msg)
                        self.runtime.logger.info(
                            'CHIEF chat run %s resumed — state: %s',
                            effective_run_id, resume_result['run_state'],
                        )
                    except Exception as exc:
                        self.runtime.logger.error('CHIEF chat resume failed: %s', exc)

        return 200, {
            'approval_id': approval_id,
            'decision': decision,
            'reason': reason,
            'recorded': True,
            'resume_result': resume_result,
        }

    def chat_edit_and_approve(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /approve/edit — store owner edits alongside approval."""
        approval_id = payload.get('approval_id')
        content     = payload.get('content', '')
        summary     = payload.get('summary', '')
        actor       = payload.get('actor', 'operator')

        if approval_id is None:
            return 400, {'error': 'approval_id required'}
        if not content:
            return 400, {'error': 'content required'}

        try:
            self._chat_wf_runtime.approvals.edit_and_approve(
                int(approval_id), actor, content, summary
            )
        except Exception as exc:
            self.runtime.logger.error('CHIEF chat edit_and_approve failed: %s', exc)
            return 500, {'error': str(exc)}

        return 200, {'approval_id': approval_id, 'decision': 'approved', 'edited': True}

    def chat_list_sessions(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /sessions — list all chat sessions."""
        with self._chat_lock:
            sessions = [s.to_dict() for s in self._chat_sessions.values()]
        return 200, {'sessions': sessions, 'count': len(sessions)}

    def chat_get_history(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /session/history — get message history for a session."""
        session_id = payload.get('session_id', '')
        with self._chat_lock:
            session = self._chat_sessions.get(session_id)
        if session is None:
            return 404, {'error': 'session not found'}
        return 200, {'session_id': session_id, 'messages': session.messages}

    # ------------------------------------------------------------------
    # /check_credentials command (v3.4 Phase 2)
    # ------------------------------------------------------------------

    def _check_credentials_report(self, live: bool = False) -> str:
        """Poll all 6 operators for current readiness. /check_credentials command."""
        OPERATORS = [
            ("EMAIL",    8010),
            ("SCOUT",    7002),
            ("DEMO",     8029),
            ("TELEGRAM", 9000),
            ("BUFFER",   9007),
            ("SOCIAL",   8011),
        ]

        vault_ok = False
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:5101/health", timeout=3
            ) as r:
                vault_ok = json.loads(r.read()).get("ok", False)
        except Exception:
            pass

        results = {}
        for name, port in OPERATORS:
            info: dict = {"status": "unreachable", "missing": []}
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/ready", timeout=4
                ) as r:
                    body = json.loads(r.read())
                    info["status"]  = body.get("readiness_status", "unknown")
                    info["missing"] = body.get("missing_credentials", [])
            except Exception:
                pass
            results[name] = info

        mode = " (live)" if live else ""
        lines = [
            f"\U0001f510 Credential Status{mode}",
            "━" * 27,
            ("✅ VAULT          ready"
             if vault_ok else "❌ VAULT          unavailable"),
            "",
        ]
        healthy = degraded = blocked = 0
        for name, info in results.items():
            st      = info["status"]
            missing = info["missing"]
            if st == "ready":
                lines.append(f"✅ {name:<12} ready")
                healthy += 1
            elif st == "degraded":
                ms = f" — {missing[0]}" if missing else ""
                lines.append(f"⚠️  {name:<12} degraded{ms}")
                degraded += 1
            elif st == "unreachable":
                lines.append(f"❓ {name:<12} unreachable")
                blocked += 1
            else:
                ms = f" — {', '.join(missing)}" if missing else ""
                lines.append(f"❌ {name:<12} {st}{ms}")
                blocked += 1

        lines += [
            "",
            f"Operators: {healthy} healthy  {degraded} degraded  {blocked} blocked",
        ]
        if not live:
            lines += ["", "/check_credentials --live for fresh sweep"]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Startup readiness summary (v3.4 Phase 1)
    # ------------------------------------------------------------------

    def _send_readiness_summary(self) -> None:
        """Fires 30 s after startup. Polls /api/ready on all 6 operators.
        Sends ONE Telegram alert with mission impact only when issues found.
        Silence on clean healthy boot. Deduplicates same issue for 4 h."""
        def _run() -> None:
            time.sleep(30)
            try:
                # Detect boot type: uptime < 300 s = power recovery
                uptime_s = 9999
                try:
                    import subprocess as _sp, re as _re
                    r = _sp.run(["sysctl", "kern.boottime"],
                                capture_output=True, text=True)
                    m = _re.search(r"sec = (\d+)", r.stdout)
                    if m:
                        uptime_s = int(time.time()) - int(m.group(1))
                except Exception:
                    pass

                # VAULT root dependency
                vault_ok = False
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:5101/health", timeout=3
                    ) as r:
                        vault_ok = json.loads(r.read()).get("ok", False)
                except Exception:
                    pass

                # Poll /api/ready on each operator
                PATCHED = [
                    ("EMAIL",    "email",    8010),
                    ("SCOUT",    "scout",    7002),
                    ("DEMO",     "demo",     8029),
                    ("TELEGRAM", "telegram", 9000),
                    ("BUFFER",   "buffer",   9007),
                    ("SOCIAL",   "social",   8011),
                ]
                issues: list[str] = []
                op_lines: list[str] = []
                op_readiness: dict = {}
                for display, key, port in PATCHED:
                    op_readiness[key] = {"readiness_status": "unreachable", "missing": []}
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/ready",
                            timeout=3,
                        ) as r:
                            code = r.getcode()
                            body = json.loads(r.read())
                            st      = body.get("readiness_status", "unknown")
                            missing = body.get("missing_credentials", [])
                            op_readiness[key] = {"readiness_status": st, "missing": missing}
                            if code != 200:
                                for item in (missing or [st]):
                                    op_lines.append(f"❌ {display:<12} {item}")
                                    issues.append(item)
                            elif st == "degraded":
                                for item in (missing or ["degraded"]):
                                    op_lines.append(f"⚠️ {display:<12} {item}")
                                    issues.append(item)
                    except Exception:
                        op_lines.append(f"❓ {display:<12} unreachable")
                        issues.append(f"{display} unreachable")

                if not vault_ok:
                    issues.insert(0, "VAULT unavailable")

                # Silence on healthy boot
                if not issues:
                    self.runtime.logger.info(
                        "Startup readiness: all healthy — no alert sent"
                    )
                    _save_dedup({})  # reset dedup on clean boot
                    return

                # Deduplication — suppress repeat alerts for 4 h
                issue_key = "|".join(sorted(issues))
                if not _should_alert(issue_key):
                    self.runtime.logger.info(
                        "Startup readiness alert suppressed (dedup, same %d issue(s))",
                        len(issues),
                    )
                    return

                # Build message with mission impact section
                boot_label = (
                    f"⚡ Power recovery (uptime {uptime_s}s)"
                    if uptime_s < 300 else "🔄 Restart"
                )
                n_ok = len(PATCHED) - sum(
                    1 for l in op_lines if l.startswith(("❌", "⚠️", "❓"))
                )

                # Mission readiness section
                missions = _compute_all_missions(op_readiness)
                mission_lines: list[str] = []
                ICONS = {"ready": "✅", "degraded": "⚠️", "blocked": "❌"}
                for _mid, (mst, mreason, mname) in missions.items():
                    if mst != "ready":
                        rs = f" — {mreason}" if mreason else ""
                        mission_lines.append(
                            f"{ICONS.get(mst, '❓')} {mname}{rs}"
                        )

                msg_parts = [
                    boot_label,
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    ("✅ VAULT ready" if vault_ok
                     else "❌ VAULT unavailable — credentials blocked"),
                ]
                if mission_lines:
                    msg_parts += ["", "Missions affected:"] + mission_lines
                msg_parts += [
                    "",
                    f"Operators: {n_ok}/{len(PATCHED)} healthy",
                    "",
                    *op_lines,
                    "",
                    "/check_credentials for details",
                ]
                msg = "\n".join(msg_parts)
                _http_post(
                    f"{TELEGRAM_URL}/send",
                    {"chat_id": "1535010257", "text": msg},
                    timeout=10,
                )
                self.runtime.logger.info(
                    "Startup readiness alert sent (%d issue(s))",
                    len(issues),
                )
            except Exception as exc:
                self.runtime.logger.warning(
                    "Startup readiness summary error: %s", exc
                )

        threading.Thread(
            target=_run, daemon=True, name="startup-readiness"
        ).start()

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(
            target=self._try_register_with_crew, daemon=True, name="chief-crew-reg"
        ).start()
        self._send_readiness_summary()
        self.runtime.logger.info(
            "CHIEF orchestrator active — port %d | crew=%s | beacon=%s",
            CHIEF_PORT, CREW_URL, BEACON_URL,
        )
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description="CHIEF - Cascadia OS task orchestrator")
    p.add_argument("--config", required=True)
    p.add_argument("--name", required=True)
    a = p.parse_args()
    ChiefService(a.config, a.name).start()


if __name__ == "__main__":
    main()
