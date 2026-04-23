#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Cascadia OS — Uninstaller
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zyrconlabs/Cascadia-OS/main/uninstall.sh | bash
# ─────────────────────────────────────────────────────────────────────────────

INSTALL_DIR="$HOME/cascadia-os"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }
info() { echo -e "  $*"; }

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║        Cascadia OS — Uninstaller          ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
echo "  This will remove Cascadia OS from your system."
echo "  The following will be deleted:"
echo ""
echo "    ~/cascadia-os/                      (application)"
echo "    ~/Library/LaunchAgents/com.zyrconlabs.*  (login agents, macOS)"
echo "    ~/.local/bin/cascadia               (launcher command)"
echo ""
echo "  Homebrew, SwiftBar, and Python are NOT removed."
echo ""
read -r -p "  Continue? [y/N]  " _confirm
echo ""
[[ "$_confirm" =~ ^[Yy]$ ]] || { echo "  Uninstall cancelled."; echo ""; exit 0; }

# 1. Stop services
info "Stopping Cascadia OS..."
bash "$INSTALL_DIR/stop.sh" 2>/dev/null || true
pkill -f "cascadia\." 2>/dev/null || true
pkill -f "llama-server" 2>/dev/null || true
ok "Services stopped"

# 2. Remove login agents (macOS)
if [[ "$(uname)" == "Darwin" ]]; then
    launchctl unload "$HOME/Library/LaunchAgents/com.zyrconlabs.cascadia.plist" 2>/dev/null || true
    launchctl unload "$HOME/Library/LaunchAgents/com.zyrconlabs.cascadia.llama.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.zyrconlabs.cascadia.plist"
    rm -f "$HOME/Library/LaunchAgents/com.zyrconlabs.cascadia.llama.plist"
    ok "Login agents removed"
fi

# 3. Remove SwiftBar / Argos plugin
rm -f "$HOME/Library/Application Support/SwiftBar/Plugins/cascadia.5s.sh" 2>/dev/null || true
rm -f "$HOME/Library/Application Support/xbar/plugins/cascadia.5s.sh" 2>/dev/null || true
rm -f "$HOME/.config/argos/cascadia.5s.sh" 2>/dev/null || true
ok "Menu bar plugin removed"

# 4. Remove launcher
rm -f "$HOME/.local/bin/cascadia"
ok "Launcher removed"

# 5. Remove application
if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    ok "Application removed"
else
    fail "~/cascadia-os not found — may already be removed"
fi

echo ""
echo "  ✓ Cascadia OS has been fully removed."
echo ""
