"""
scripts/register_telegram_commands.py
Register slash commands with BotFather so Telegram shows autocomplete
when users type "/" in @ZyrconBot.

Run once:
  python3 scripts/register_telegram_commands.py

Token is read from the Telegram connector config at:
  ../operators/cascadia-os-operators/telegram/telegram.config.json
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_PATH = (
    Path(__file__).parent.parent.parent
    / "operators" / "cascadia-os-operators" / "telegram" / "telegram.config.json"
)

COMMANDS = [
    # Onboarding
    {"command": "start",          "description": "Show welcome and persistent keyboard"},
    # Menu
    {"command": "menu",           "description": "Interactive button menu"},
    # Find Work
    {"command": "recon",          "description": "Search for new leads"},
    {"command": "scout",          "description": "Qualify an inbound lead"},
    {"command": "outreach",       "description": "Queue outreach drafts for approval"},
    {"command": "followups",      "description": "Follow-ups due today"},
    {"command": "replies",        "description": "Recent lead replies"},
    {"command": "reactivate",     "description": "Reactivate cold leads"},
    # Win Work
    {"command": "quote",          "description": "Draft a proposal or quote"},
    {"command": "close",          "description": "Close a won job"},
    {"command": "invoice",        "description": "Invoice follow-up — get paid"},
    {"command": "funnel",         "description": "Run sales funnel workflow"},
    # Run Work
    {"command": "brief",          "description": "Morning brief"},
    {"command": "schedule",       "description": "Today's schedule check"},
    {"command": "blockers",       "description": "Active blocker watch"},
    {"command": "eod",            "description": "End of day report"},
    {"command": "weekly",         "description": "Weekly summary report"},
    {"command": "review",         "description": "Request a review from a customer"},
    # Approvals
    {"command": "approve_all",    "description": "Approve all pending items"},
    # System
    {"command": "pipeline",       "description": "Lead pipeline snapshot"},
    {"command": "missions",       "description": "Recent mission runs"},
    {"command": "status",         "description": "System health"},
    {"command": "inbox_check",    "description": "Trigger inbox poll"},
    {"command": "ram",            "description": "RAM and swap usage"},
    # Advanced
    {"command": "recon_start",    "description": "Start RECON worker"},
    {"command": "recon_stop",     "description": "Stop RECON worker"},
    {"command": "archive",        "description": "Archive exhausted leads"},
    {"command": "startup_report", "description": "Full system health report"},
    {"command": "social",         "description": "Start social campaign"},
    {"command": "operators",      "description": "List available operators"},
    {"command": "help",           "description": "Show all commands"},
]


def _load_token() -> str:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Telegram config not found: {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text())
    token = cfg.get("bot_token", "")
    if not token:
        raise ValueError("bot_token is empty in telegram.config.json")
    return token


def register(token: str) -> dict:
    url  = f"https://api.telegram.org/bot{token}/setMyCommands"
    body = json.dumps({"commands": COMMANDS}).encode()
    req  = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main() -> None:
    print("Registering Telegram bot commands…")
    try:
        token = _load_token()
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌  {exc}")
        return

    try:
        result = register(token)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        print(f"❌  Telegram API error {exc.code}: {body}")
        return
    except Exception as exc:
        print(f"❌  Request failed: {exc}")
        return

    if result.get("ok"):
        print(f"✅  Registered {len(COMMANDS)} commands with @ZyrconBot:")
        for cmd in COMMANDS:
            print(f"    /{cmd['command']:<12} — {cmd['description']}")
        print("\nUsers will see autocomplete when they type '/' in the chat.")
    else:
        print(f"❌  Telegram returned ok=false: {result}")


if __name__ == "__main__":
    main()
