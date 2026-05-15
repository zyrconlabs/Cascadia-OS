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
    {"command": "recon",     "description": "Run a RECON lead scan"},
    {"command": "scan",      "description": "Alias for /recon — start a lead scan"},
    {"command": "leads",     "description": "Show lead report"},
    {"command": "quote",     "description": "Draft a proposal or quote"},
    {"command": "scout",     "description": "Qualify an inbound lead"},
    {"command": "status",    "description": "Show system status"},
    {"command": "operators", "description": "List available operators"},
    {"command": "help",      "description": "Show all commands"},
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
