"""
cascadia/chief/commands.py
Slash command parser for CHIEF.
Commands bypass the LLM and keyword selector — 100% routing accuracy.
"""
from __future__ import annotations

COMMANDS: dict[str, dict] = {
    "/recon":     {"operator": "recon",       "description": "Run a RECON lead scan"},
    "/scan":      {"operator": "recon",       "description": "Alias for /recon"},
    "/leads":     {"operator": "recon",       "description": "Show lead report"},
    "/quote":     {"operator": "quote_brief", "description": "Draft a proposal or quote"},
    "/scout":     {"operator": "scout",       "description": "Qualify an inbound lead"},
    "/outreach":  {"operator": None,          "description": "Brief top 5 uncontacted leads for outreach"},
    "/pipeline":  {"operator": None,          "description": "Show lead pipeline snapshot"},
    "/status":    {"operator": None,          "description": "Show system status"},
    "/missions":  {"operator": None,          "description": "Recent mission runs"},
    "/operators": {"operator": None,          "description": "List available operators"},
    "/help":      {"operator": None,          "description": "Show all commands"},
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
