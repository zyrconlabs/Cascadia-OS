"""
cascadia/chief/commands.py
Slash command parser for CHIEF.
Commands bypass the LLM and keyword selector — 100% routing accuracy.
"""
from __future__ import annotations
import re as _re

COMMANDS: dict[str, dict] = {
    # Onboarding
    "/start":         {"operator": None,         "description": "Show welcome + persistent keyboard"},
    # Menu
    "/menu":          {"operator": None,         "description": "Interactive button menu"},
    # Find Work
    "/recon":         {"operator": "recon",       "description": "Search for new leads (trade + city)"},
    "/scan":          {"operator": "recon",       "description": "Alias for /recon"},
    "/leads":         {"operator": "recon",       "description": "Show lead report"},
    "/recon_start":   {"operator": None,          "description": "Start RECON lead-scraping worker"},
    "/recon_stop":    {"operator": None,          "description": "Stop RECON lead-scraping worker"},
    "/scout":         {"operator": "scout",       "description": "Qualify an inbound lead"},
    "/outreach":      {"operator": None,          "description": "[N] Queue N outreach drafts for approval (default 20, max 50)"},
    "/followups":     {"operator": None,          "description": "Show follow-ups due today"},
    "/replies":       {"operator": None,          "description": "Show recent lead replies"},
    "/reactivate":    {"operator": None,          "description": "Reactivate cold leads"},
    # Win Work
    "/quote":         {"operator": "quote_brief", "description": "Draft a proposal or quote"},
    "/close":         {"operator": None,          "description": "Close a won job"},
    "/invoice":       {"operator": None,          "description": "Invoice follow-up — get paid"},
    "/funnel":        {"operator": None,          "description": "Run sales funnel workflow"},
    # Run Work
    "/brief":         {"operator": None,          "description": "Morning brief"},
    "/schedule":      {"operator": None,          "description": "Today's schedule check"},
    "/blockers":      {"operator": None,          "description": "Active blocker watch"},
    "/eod":           {"operator": None,          "description": "End of day report"},
    "/weekly":        {"operator": None,          "description": "Weekly summary report"},
    "/review":        {"operator": None,          "description": "Request a review from a customer"},
    # Approvals
    "/preview":       {"operator": None,          "description": "Preview the next outreach draft (no send, no queue)"},
    "/send_outreach": {"operator": None,          "description": "Draft AND send outreach emails to top leads"},
    "/approve_all":   {"operator": None,          "description": "Approve all pending outreach drafts and quotes at once"},
    # Utility
    "/inbox_check":   {"operator": None,          "description": "Trigger immediate IMAP inbox poll"},
    "/archive":       {"operator": None,          "description": "Archive completed (exhausted/skipped) leads"},
    # System
    "/pipeline":      {"operator": None,          "description": "Lead pipeline snapshot"},
    "/status":        {"operator": None,          "description": "System health"},
    "/missions":      {"operator": None,          "description": "Recent mission runs"},
    "/operators":     {"operator": None,          "description": "List available operators"},
    # Advanced
    "/startup_report": {"operator": None,         "description": "Full system health report"},
    "/ram":            {"operator": None,         "description": "RAM and swap usage"},
    "/crm":            {"operator": None,         "description": "CRM status (use /crm_sleep or /crm_wake to toggle)"},
    "/crm_sleep":      {"operator": None,         "description": "Put CRM to sleep (release :8015)"},
    "/crm_wake":       {"operator": None,         "description": "Wake CRM (:8015)"},
    "/email_status":   {"operator": None,         "description": "Email stats: sent/failed, per-account, outreach vs followup"},
    "/version":        {"operator": None,         "description": "Show running Cascadia OS version and operator count"},
    "/social":              {"operator": None, "description": "Start a social media campaign"},
    "/campaign":            {"operator": None, "description": "Alias for /social"},
    "/social_generate":     {"operator": None, "description": "Generate a fresh batch of social posts (X + FB + IG) on demand"},
    "/x":              {"operator": None,         "description": "Post to X: /x <text> to post now (/x_status for queue + recent)"},
    "/fb":             {"operator": None,         "description": "Post directly to Facebook: /fb <text>"},
    "/ig":             {"operator": None,         "description": "Post directly to Instagram: /ig <caption> (send image first)"},
    "/post":           {"operator": None,         "description": "Post to X, Facebook, and Instagram at once: /post <text>"},
    "/x_status":       {"operator": None,         "description": "X queue depth + recent posts"},
    "/x_post_now":     {"operator": None,         "description": "Force-draft next pending X post to Telegram NOW (bypass scheduler)"},
    "/x_approve":      {"operator": None,         "description": "Approve the scheduled X draft and send it"},
    "/x_skip":         {"operator": None,         "description": "Skip the scheduled X draft, move to next"},
    "/fb_status":      {"operator": None,         "description": "Facebook queue depth + recent posts"},
    "/fb_post_now":    {"operator": None,         "description": "Force-draft next pending FB post to Telegram NOW (bypass scheduler)"},
    "/fb_approve":     {"operator": None,         "description": "Approve the scheduled Facebook draft and send it"},
    "/fb_skip":        {"operator": None,         "description": "Skip the scheduled Facebook draft, move to next"},
    "/ig_status":      {"operator": None,         "description": "Instagram queue depth (image required to post)"},
    "/ig_post_now":    {"operator": None,         "description": "Force-draft next pending IG post to Telegram NOW (bypass scheduler)"},
    "/ig_approve":     {"operator": None,         "description": "Approve the Instagram draft — send image first"},
    "/ig_skip":        {"operator": None,         "description": "Skip the scheduled Instagram draft, move to next"},
    "/ig_gen_image":   {"operator": None,         "description": "AI-generate image for pending Instagram post (Pollinations FLUX)"},
    "/ig_generate":    {"operator": None,         "description": "Alias for /ig_gen_image"},
    "/ig_regen":       {"operator": None,         "description": "Regenerate Instagram image with a new Pollinations seed"},
    "/approve_all_x":  {"operator": None,         "description": "Bulk-approve all queued X (Twitter) posts"},
    "/approve_all_fb": {"operator": None,         "description": "Bulk-approve all queued Facebook posts"},
    "/approve_all_ig": {"operator": None,         "description": "Bulk-approve all queued Instagram posts"},
    "/clear_image":    {"operator": None,         "description": "Discard pending images sent to the bot"},
    "/email_approve":  {"operator": None,         "description": "Approve oldest Scout email reply draft and send it"},
    "/email_skip":     {"operator": None,         "description": "Skip oldest Scout email reply draft (discard without sending)"},
    "/wizard":         {"operator": None,         "description": "Guided setup assistant — VAULT health + all 6 operator credentials"},
    "/help":           {"operator": None,         "description": "Show all commands"},
    "/demo_status":    {"operator": None,         "description": "Demo bot status"},
    "/token":          {"operator": None,         "description": "Token usage today"},
    "/token_week":     {"operator": None,         "description": "Token usage this week"},
    "/token_month":    {"operator": None,         "description": "Token usage this month"},
    "/token_all":      {"operator": None,         "description": "Token usage all time"},
    # Code
    "/code":                {"operator": None, "description": "Start a code project"},
    # Grid
    "/node_sync":           {"operator": None, "description": "Sync Grid nodes"},
    "/node_sync_status":    {"operator": None, "description": "Grid node sync status"},
    # Performance
    "/performance":         {"operator": "performance", "description": "Trigger morning performance report now"},
    "/performance_noon":    {"operator": "performance", "description": "Trigger noon KPI check now"},
    "/performance_kpis":    {"operator": "performance", "description": "Show current KPI snapshot"},
    "/performance_history": {"operator": "performance", "description": "Show 7-day KPI trend"},
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
    lines = [
        "📋 *CHIEF Commands*\n",

        "🏠 MENU",
        "/menu         Interactive button menu — tap to navigate\n",

        "🔍 FIND WORK",
        "/recon        Search for new leads (trade + city)",
        "/scout        Qualify an inbound lead",
        "/outreach [N] Queue N outreach drafts for approval",
        "/followups    Follow-ups due today",
        "/replies      Recent lead replies",
        "/reactivate   Reactivate cold leads\n",

        "💼 WIN WORK",
        "/quote        Draft a proposal or quote",
        "/close        Close a won job",
        "/invoice      Invoice follow-up — get paid",
        "/funnel       Run sales funnel workflow\n",

        "⚙️ RUN WORK",
        "/brief        Morning brief",
        "/schedule     Today's schedule check",
        "/blockers     Active blocker watch",
        "/eod          End of day report",
        "/weekly       Weekly summary report",
        "/review       Request a review from a customer\n",

        "✅ APPROVALS",
        "/approve_all  Approve all pending items",
        "/approve_N    Approve item N",
        "/reject_N     Reject item N\n",

        "📊 SYSTEM",
        "/pipeline     Lead pipeline snapshot",
        "/missions     Recent mission runs",
        "/status       System health",
        "/inbox_check  Trigger IMAP inbox poll",
        "/ram          RAM and swap usage",
        "/crm          CRM status   /crm_sleep  Sleep   /crm_wake  Wake",
        "/email_status Email stats: sent/failed, per-account, outreach vs followup\n",

        "🔧 ADVANCED",
        "/recon_start  Start RECON worker",
        "/recon_stop   Stop RECON worker",
        "/archive      Archive exhausted leads",
        "/startup_report  Full system health report",
        "/preview      Preview next outreach draft",
        "/social       Start social media campaign",
        "/x            Post to X (/x <text>)   /x_status  Queue + recent",
        "/x_approve    Approve scheduled X draft   /x_skip  Skip it",
        "/fb_status    Facebook queue   /fb_approve  Approve   /fb_skip  Skip\n",

        "Or just type naturally — CHIEF understands plain English.",
        "Examples:",
        '  "Find HVAC contractors in Houston"',
        '  "Draft a proposal for a warehouse mezzanine job"',
        '  "How many leads do we have?"',
        '  "Do it again"',
    ]
    return "\n".join(lines)


def build_operators_text(operator_catalog: dict) -> str:
    lines = ["🤖 Available Operators\n"]
    for op_id, op in operator_catalog.items():
        icon = "✅" if op.get("status") == "available" else "🔜"
        lines.append(f"{icon} {op['display_name']} — {op['description']}")
    lines.append("\nType /menu for interactive navigation or /help for all commands.")
    return "\n".join(lines)
