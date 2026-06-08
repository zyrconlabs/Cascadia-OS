"""
cascadia/chief/commands.py
Slash command parser for CHIEF.
Commands bypass the LLM and keyword selector — 100% routing accuracy.
"""
from __future__ import annotations
import re as _re

COMMANDS: dict[str, dict] = {
    "/recon":     {"operator": "recon",       "description": "Run a RECON lead scan"},
    "/scan":      {"operator": "recon",       "description": "Alias for /recon"},
    "/leads":     {"operator": "recon",       "description": "Show lead report"},
    "/recon_start": {"operator": None,        "description": "Start the RECON lead-scraping worker"},
    "/recon_stop":  {"operator": None,        "description": "Stop the RECON lead-scraping worker"},
    "/quote":     {"operator": "quote_brief", "description": "Draft a proposal or quote"},
    "/scout":     {"operator": "scout",       "description": "Qualify an inbound lead"},
    "/preview":       {"operator": None, "description": "Preview the next outreach draft (no send, no queue)"},
    "/outreach":      {"operator": None, "description": "[N] queue N outreach drafts for approval (default 3, max 10)"},
    "/send_outreach": {"operator": None, "description": "Draft AND send outreach emails to top leads"},
    "/approve_all":   {"operator": None, "description": "Approve all pending outreach drafts and quotes at once"},
    "/followups":     {"operator": None, "description": "Show pending follow-ups due today"},
    "/replies":       {"operator": None, "description": "Show recent lead replies from inbox"},
    "/inbox_check":   {"operator": None, "description": "Trigger immediate IMAP inbox poll"},
    "/archive":       {"operator": None, "description": "Archive completed (exhausted/skipped) leads to contacted_list"},
    "/pipeline":  {"operator": None,          "description": "Show lead pipeline snapshot"},
    "/status":    {"operator": None,          "description": "Show system status"},
    "/missions":  {"operator": None,          "description": "Recent mission runs"},
    "/operators": {"operator": None,          "description": "List available operators"},
    "/help":      {"operator": None,          "description": "Show all commands"},
    "/startup_report": {"operator": None,    "description": "Full system health report → Telegram"},
    "/ram":            {"operator": None,    "description": "Show RAM and swap usage"},
    "/social":         {"operator": None,    "description": "Start a social media campaign"},
    "/campaign":       {"operator": None,    "description": "Alias for /social"},
}


def parse_command(text: str) -> dict | None:
    """
    Returns a parsed command dict or None if text is not a slash command.

    Known command:
      {"command": "/recon", "operator": "recon", "args": "houston HVAC", "unknown": False}
    Unknown command:
      {"command": "/xyz",   "operator": None,    "args": "",              "unknown": True}
    Not a command:
      None
    """
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    cmd  = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    if cmd not in COMMANDS:
        return {"command": cmd, "operator": None, "args": args, "unknown": True}
    return {
        "command":  cmd,
        "operator": COMMANDS[cmd]["operator"],
        "args":     args,
        "unknown":  False,
    }


def parse_contact_command(text: str) -> dict | None:
    """
    Match /contact_N, /contact_N_yes, /contact_N_no, /contact_N_pending.
    Returns {"row_id": "42", "status": "yes"} or None if no match.
    """
    m = _re.match(r'^/contact_(\d+)(?:_(yes|no|pending))?$', text.strip(), _re.IGNORECASE)
    if not m:
        return None
    raw = (m.group(2) or "").lower()
    status_map = {"": "yes", "yes": "yes", "no": "not_interested", "pending": "pending"}
    return {"row_id": m.group(1), "status": status_map[raw]}


def parse_quote_command(text: str) -> dict | None:
    """
    Match /quote_N [optional description].
    Returns {"row_id": "31", "description": "..."} or None if no match.
    """
    m = _re.match(r'^/quote_(\d+)(?:\s+(.+))?$', text.strip(), _re.IGNORECASE)
    if not m:
        return None
    return {"row_id": m.group(1), "description": (m.group(2) or "").strip()}


def parse_approval_command(text: str) -> dict | None:
    """
    Match /approve_N or /reject_N.
    Returns {"action": "approve", "row_id": "31"} or None if no match.
    """
    m = _re.match(r'^/(approve|reject)_(\d+)$', text.strip(), _re.IGNORECASE)
    if not m:
        return None
    return {"action": m.group(1).lower(), "row_id": m.group(2)}


def build_help_text() -> str:
    lines = ["📋 CHIEF Commands\n"]
    for cmd, info in COMMANDS.items():
        lines.append(f"{cmd:<12}  {info['description']}")
    lines.append(
        "\nOr just type naturally — CHIEF understands plain English.\n"
        "Examples:\n"
        '  "Find HVAC contractors in Houston"\n'
        '  "Draft a proposal for a warehouse mezzanine job"\n'
        '  "How many leads do we have?"\n'
        '  "Do it again"'
    )
    return "\n".join(lines)


def build_operators_text(operator_catalog: dict) -> str:
    lines = ["🤖 Available Operators\n"]
    for op_id, op in operator_catalog.items():
        icon = "✅" if op.get("status") == "available" else "🔜"
        lines.append(f"{icon} {op['display_name']} — {op['description']}")
    lines.append("\nType /help to see slash commands.")
    return "\n".join(lines)
