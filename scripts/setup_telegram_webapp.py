"""
scripts/setup_telegram_webapp.py
Registers the Telegram WebApp menu button on ZyrconBot.

Usage:
  # Step 1: Get a public HTTPS URL pointing to PRISM (port 6300).
  #   Option A — Tailscale Funnel (recommended, already installed):
  #     tailscale funnel 6300
  #   Option B — ngrok (easiest for quick testing):
  #     ngrok http 6300
  #
  # Step 2: Run this script with your public URL:
  #   python3 scripts/setup_telegram_webapp.py https://your-public-url.example.com
  #
  # After running, open @ZyrconBot in Telegram.
  # A "⚡ Dashboard" button appears above the chat bar.
  # Tap it to open the Command Center.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "operators"
    / "cascadia-os-operators"
    / "telegram"
    / "telegram.config.json"
)


def _bot_post(token: str, method: str, payload: dict) -> dict:
    url  = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    public_url = sys.argv[1].rstrip("/")
    webapp_url = f"{public_url}/webapp"

    try:
        cfg   = json.loads(CONFIG_PATH.read_text())
        token = cfg.get("bot_token", "")
    except FileNotFoundError:
        print(f"ERROR: config not found at {CONFIG_PATH}")
        sys.exit(1)

    if not token or token.startswith("YOUR"):
        print("ERROR: bot_token not set in telegram.config.json")
        sys.exit(1)

    print(f"Bot token: {token[:10]}...")
    print(f"WebApp URL: {webapp_url}\n")

    # Verify the webapp URL is reachable before registering
    try:
        urllib.request.urlopen(webapp_url, timeout=5)
        print("✅ WebApp URL is reachable")
    except Exception as exc:
        print(f"⚠️  WebApp URL returned an error: {exc}")
        print("   Continuing anyway — Telegram will validate HTTPS independently.\n")

    # Set menu button for all chats (default)
    result = _bot_post(token, "setChatMenuButton", {
        "menu_button": {
            "type":    "web_app",
            "text":    "⚡ Dashboard",
            "web_app": {"url": webapp_url},
        }
    })
    print("setChatMenuButton:", json.dumps(result, indent=2))

    if result.get("ok"):
        print(f"\n✅ Menu button registered!")
        print(f"   Open @ZyrconBot in Telegram — tap '⚡ Dashboard' above the chat bar.")
    else:
        print(f"\n❌ Registration failed: {result.get('description', 'unknown error')}")
        if "HTTPS" in str(result.get("description", "")):
            print("   Telegram requires HTTPS. Make sure your public URL uses https://")
        sys.exit(1)


if __name__ == "__main__":
    main()
