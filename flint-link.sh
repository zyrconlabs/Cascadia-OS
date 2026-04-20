#!/bin/bash
# flint-link.sh — Wire the Flint menu bar plugin to SwiftBar/xbar/Argos
# Run this once after cloning, or after installing SwiftBar.
# Uses a symlink — the repo file is the single source of truth.
# Changes to cascadia/flint/cascadia.5s.sh take effect immediately in SwiftBar.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SRC="$REPO_DIR/cascadia/flint/cascadia.5s.sh"
SWIFTBAR_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
XBAR_DIR="$HOME/Library/Application Support/xbar/plugins"
ARGOS_DIR="$HOME/.config/argos"

GREEN='\033[0;32m'
AMBER='\033[0;33m'
RESET='\033[0m'

chmod +x "$PLUGIN_SRC"

linked=false

if [[ "$(uname)" == "Darwin" ]]; then
    # Create SwiftBar plugins dir if SwiftBar is installed but dir missing
    if [[ ! -d "$SWIFTBAR_DIR" ]] && [[ -d "/Applications/SwiftBar.app" || -d "$HOME/Applications/SwiftBar.app" ]]; then
        mkdir -p "$SWIFTBAR_DIR"
        echo "Created SwiftBar plugins directory"
    fi
    if [[ -d "$SWIFTBAR_DIR" ]]; then
        # Remove any old copy first
        rm -f "$SWIFTBAR_DIR/cascadia.5s.sh"
        ln -sf "$PLUGIN_SRC" "$SWIFTBAR_DIR/cascadia.5s.sh"
        echo -e "${GREEN}✓${RESET} Linked → SwiftBar"
        echo -e "  ${SWIFTBAR_DIR}/cascadia.5s.sh → ${PLUGIN_SRC}"
        linked=true
    elif [[ -d "$XBAR_DIR" ]]; then
        rm -f "$XBAR_DIR/cascadia.5s.sh"
        ln -sf "$PLUGIN_SRC" "$XBAR_DIR/cascadia.5s.sh"
        echo -e "${GREEN}✓${RESET} Linked → xbar"
        linked=true
    fi
elif [[ "$(uname)" == "Linux" ]]; then
    if [[ -d "$ARGOS_DIR" ]]; then
        rm -f "$ARGOS_DIR/cascadia.5s.sh"
        ln -sf "$PLUGIN_SRC" "$ARGOS_DIR/cascadia.5s.sh"
        echo -e "${GREEN}✓${RESET} Linked → Argos"
        linked=true
    fi
fi

if [[ "$linked" = false ]]; then
    echo -e "${AMBER}SwiftBar/xbar/Argos not found.${RESET}"
    echo ""
    echo "Install SwiftBar:  brew install swiftbar"
    echo "Then run this script again."
    echo ""
    echo "Or link manually:"
    echo "  mkdir -p \"$SWIFTBAR_DIR\""
    echo "  ln -sf \"$PLUGIN_SRC\" \"$SWIFTBAR_DIR/cascadia.5s.sh\""
    echo ""
    echo "Or use the Python tray (no SwiftBar needed):"
    echo "  python -m cascadia.flint.tray"
    exit 1
fi

echo ""
echo "SwiftBar will now read directly from the repo."
echo "No re-linking needed after git pull."
