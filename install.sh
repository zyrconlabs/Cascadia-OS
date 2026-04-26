#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Cascadia OS — One-Click Installer  (Mac & Linux)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zyrconlabs/cascadia-os/main/install.sh | bash
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="zyrconlabs/cascadia-os"   # ← replace with your GitHub username/repo
BRANCH="main"
INSTALL_DIR="$HOME/cascadia-os"
VERSION=$(grep -m1 '^version' "$(dirname "$0")/pyproject.toml" 2>/dev/null | cut -d'"' -f2 || echo "0.44.0")
VENV_DIR="$INSTALL_DIR/.venv"
MIN_PYTHON="3.11"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[cascadia]${NC} $*"; }
success() { echo -e "${GREEN}[cascadia]${NC} $*"; }
warn()    { echo -e "${YELLOW}[cascadia]${NC} $*"; }
die()     { echo -e "${RED}[cascadia] ERROR:${NC} $*" >&2; exit 1; }


# ── Zyrcon AI logo ────────────────────────────────────────────────────────────
echo ""
echo -e "\033[1;31m  ███████╗██╗   ██╗██████╗  ██████╗ ██████╗ ███╗   ██╗\033[0m"
echo -e "\033[1;31m     ███╔╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔═══██╗████╗  ██║\033[0m"
echo -e "\033[1;31m    ███╔╝  ╚████╔╝ ██████╔╝██║     ██║   ██║██╔██╗ ██║\033[0m"
echo -e "\033[1;31m   ███╔╝    ╚██╔╝  ██╔══██╗██║     ██║   ██║██║╚██╗██║\033[0m"
echo -e "\033[1;31m  ███████╗   ██║   ██║  ██║╚██████╗╚██████╔╝██║ ╚████║\033[0m"
echo -e "\033[1;31m  ╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝\033[0m"
echo -e "\033[0;90m                      A I   P L A T F O R M\033[0m"
echo ""

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║       Cascadia OS v${VERSION} Installer    ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── Pre-install disclosure ─────────────────────────────────────────────────────
echo ""
echo "  ┌─────────────────────────────────────────────────────────────────┐"
echo "  │                   BEFORE YOU CONTINUE                          │"
echo "  ├─────────────────────────────────────────────────────────────────┤"
echo "  │                                                                 │"
echo "  │  This installer will automatically:                            │"
echo "  │                                                                 │"
echo "  │  ● Install Homebrew          (if not present, macOS only)      │"
echo "  │  ● Install SwiftBar          (menu bar controller, macOS only) │"
echo "  │  ● Install Python 3.11+      (if not present)                  │"
echo "  │  ● Install Python packages   (flask, cryptography, and others) │"
echo "  │  ● Download an AI model      (1–4 GB depending on selection)   │"
echo "  │  ● Register a login agent    (auto-starts Cascadia at boot)    │"
echo "  │                                                                 │"
echo "  │  Files are written to:                                         │"
echo "  │  ● ~/cascadia-os/                  (application)               │"
echo "  │  ● ~/Library/LaunchAgents/         (login agent, macOS only)   │"
echo "  │                                                                 │"
echo "  │  Nothing else on your system is modified.                      │"
echo "  │                                                                 │"
echo "  │  To uninstall at any time:                                     │"
echo "  │  https://github.com/zyrconlabs/Cascadia-OS/blob/main/UNINSTALL.md  │"
echo "  │                                                                 │"
echo "  │  By continuing you agree to the terms in LICENSE.              │"
echo "  │                                                                 │"
echo "  └─────────────────────────────────────────────────────────────────┘"
echo ""
read -r -p "  Continue with installation? [y/N]  " _confirm
echo ""
[[ "$_confirm" =~ ^[Yy]$ ]] || { echo "  Installation cancelled."; echo ""; exit 0; }
echo "  ✓ Starting installation..."
echo ""

# ── 0. Mac prerequisites — Homebrew and SwiftBar ──────────────────────────────
if [[ "$(uname)" == "Darwin" ]]; then
    # Install Homebrew if not present
    if ! command -v brew &>/dev/null; then
        info "Homebrew not found — installing..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to PATH for Apple Silicon and Intel
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f "/usr/local/bin/brew" ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        success "Homebrew installed"
    else
        success "Homebrew found: $(brew --version | head -1)"
    fi

    # Install SwiftBar if not present
    if [[ ! -d "/Applications/SwiftBar.app" ]] && [[ ! -d "$HOME/Applications/SwiftBar.app" ]]; then
        info "SwiftBar not found — installing via Homebrew..."
        brew install --cask swiftbar
        success "SwiftBar installed"
    else
        success "SwiftBar found"
    fi

    # Open SwiftBar so it registers and creates its plugin folder
    if [[ ! -d "$HOME/Library/Application Support/SwiftBar/Plugins" ]]; then
        info "Launching SwiftBar to initialise plugin folder..."
        open -a SwiftBar
        # Wait up to 15s for SwiftBar to create the Plugins directory
        SWIFTBAR_WAIT=0
        while [[ ! -d "$HOME/Library/Application Support/SwiftBar/Plugins" ]] && [[ $SWIFTBAR_WAIT -lt 15 ]]; do
            sleep 1
            SWIFTBAR_WAIT=$((SWIFTBAR_WAIT + 1))
        done
        if [[ -d "$HOME/Library/Application Support/SwiftBar/Plugins" ]]; then
            success "SwiftBar plugin folder ready"
        else
            warn "SwiftBar plugin folder not created yet — plugin will be linked if SwiftBar is opened manually"
        fi
    fi
fi

# ── 1. Check Python ───────────────────────────────────────────────────────────
info "Checking Python version..."
PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=${ver%%.*}; minor=${ver##*.}
        if [[ $major -ge 3 && $minor -ge 11 ]]; then
            PYTHON="$cmd"
            success "Found Python $ver at $(command -v $cmd)"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    die "Python $MIN_PYTHON+ is required. Install it from https://python.org and re-run this script."
fi

# ── 2. Check Git ──────────────────────────────────────────────────────────────
info "Checking git..."
command -v git &>/dev/null || die "git is required. Install it and re-run."
success "git found."

# ── 3. Clone or update ────────────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Existing installation found at $INSTALL_DIR — pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
    info "Cloning Cascadia OS into $INSTALL_DIR..."
    git clone --branch "$BRANCH" --depth 1 "https://github.com/$REPO.git" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── 4. Virtual environment ────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
success "Virtual environment ready."

# ── 5. Install package ────────────────────────────────────────────────────────
info "Installing Cascadia OS..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[operators]"
success "Package installed."

# ── 6. Config ─────────────────────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
    cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
    warn "config.json created from example. Edit it before starting."
else
    info "config.json already exists — skipping."
fi

# ── 7. Silent first-time setup (dirs, db, config) ────────────────────────────
# AI mode is chosen in PRISM Settings surface — no separate browser wizard needed
info "Running silent setup (directories, database, config defaults)..."
"$VENV_DIR/bin/python" -m cascadia.installer.once --dir "$INSTALL_DIR" --config config.json --no-browser
success "Setup complete."

# ── 9. Launcher script ───────────────────────────────────────────────────────
LAUNCHER="$HOME/.local/bin/cascadia"
mkdir -p "$HOME/.local/bin"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
cd "$INSTALL_DIR"
exec bash "$INSTALL_DIR/start.sh"
EOF
chmod +x "$LAUNCHER"

# ── 10. Create startup wrapper (used by launchd + manual runs) ────────────────
# This wrapper ensures llama.cpp + Cascadia start in the right order at boot
STARTUP_WRAPPER="$INSTALL_DIR/run.sh"
cat > "$STARTUP_WRAPPER" <<EOF
#!/usr/bin/env bash
# Cascadia OS startup wrapper — used by launchd and manual runs
# Starts llama.cpp then Cascadia OS watchdog in the correct order
cd "$INSTALL_DIR"
source "$VENV_DIR/bin/activate"
exec bash "$INSTALL_DIR/start.sh"
EOF
chmod +x "$STARTUP_WRAPPER"
success "Startup wrapper created: run.sh" 


# ── 11. Flint menu bar controller ─────────────────────────────────────────────
PLUGIN_SRC="$INSTALL_DIR/cascadia/flint/cascadia.5s.sh"
chmod +x "$PLUGIN_SRC"

SWIFTBAR_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
XBAR_DIR="$HOME/Library/Application Support/xbar/plugins"
ARGOS_DIR="$HOME/.config/argos"
INSTALLED_FLINT=false

if [[ "$(uname)" == "Darwin" ]]; then
    if [[ -d "$SWIFTBAR_DIR" ]]; then
        # Symlink — one source of truth, repo changes reflect instantly in SwiftBar
        ln -sf "$PLUGIN_SRC" "$SWIFTBAR_DIR/cascadia.5s.sh"
        chmod +x "$PLUGIN_SRC"
        success "Flint plugin linked → SwiftBar (symlink — no copy needed)"
        INSTALLED_FLINT=true
    elif [[ -d "$XBAR_DIR" ]]; then
        ln -sf "$PLUGIN_SRC" "$XBAR_DIR/cascadia.5s.sh"
        chmod +x "$PLUGIN_SRC"
        success "Flint plugin linked → xbar (symlink)"
        INSTALLED_FLINT=true
    fi
    if [[ "$INSTALLED_FLINT" = false ]]; then
        echo ""
        info "Menu bar controller (Flint) not auto-installed."
        echo "  Install SwiftBar: brew install swiftbar"
        echo "  Then run: bash install.sh   (it will auto-link on next run)"
        echo "  Or link manually:"
        echo "    mkdir -p \"$SWIFTBAR_DIR\""
        echo "    ln -sf \"$PLUGIN_SRC\" \"$SWIFTBAR_DIR/cascadia.5s.sh\""
        echo "  Or run without SwiftBar: python -m cascadia.flint.tray"
    fi
elif [[ "$(uname)" == "Linux" ]]; then
    if [[ -d "$ARGOS_DIR" ]]; then
        ln -sf "$PLUGIN_SRC" "$ARGOS_DIR/cascadia.5s.sh"
        chmod +x "$PLUGIN_SRC"
        success "Flint plugin linked → Argos (symlink)"
        INSTALLED_FLINT=true
    fi
    if [[ "$INSTALLED_FLINT" = false ]]; then
        echo ""
        info "Menu bar controller not auto-installed."
        echo "  Install Argos (GNOME) or link manually:"
        echo "    ln -sf \"$PLUGIN_SRC\" ~/.config/argos/cascadia.5s.sh"
        echo "  Or run: python -m cascadia.flint.tray"
    fi
fi


# ── 12. Auto-start on login (launchd + SwiftBar login item) ───────────────────
if [[ "$(uname)" == "Darwin" ]]; then
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_PATH="$PLIST_DIR/com.zyrconlabs.cascadia.plist"
    PYTHON_BIN="$VENV_DIR/bin/python"
    mkdir -p "$PLIST_DIR"

    cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.zyrconlabs.cascadia</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${INSTALL_DIR}/run.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/data/logs/startup.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/data/logs/startup.log</string>
</dict>
</plist>
PLIST

    # Unload old agent if present, load new one
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load "$PLIST_PATH" 2>/dev/null && \
        success "Cascadia registered as login agent — starts automatically at boot" || \
        info "launchctl load failed — run manually: launchctl load $PLIST_PATH"

    # ── Separate launchd plist for llama.cpp — auto-restarts on crash ─────────
    LLAMA_PLIST_PATH="$PLIST_DIR/com.zyrconlabs.cascadia.llama.plist"
    # Find llama-server — priority: brew → Zyrcon production → fallback
    LLAMA_BIN_PATH=""
    for candidate in \
        "/opt/homebrew/bin/llama-server" \
        "/usr/local/bin/llama-server" \
        "$HOME/Zyrcon/llama.cpp/build/bin/llama-server" \
        "$HOME/llama.cpp/build/bin/llama-server"; do
        if [[ -f "$candidate" ]]; then
            LLAMA_BIN_PATH="$candidate"
            break
        fi
    done

    # Auto-build if not found anywhere
    if [[ -z "$LLAMA_BIN_PATH" ]]; then
        info "llama.cpp not found — building from source into ~/Zyrcon/llama.cpp"
        info "This takes 5-10 minutes on first install..."
        LLAMA_SRC="$HOME/Zyrcon/llama.cpp"
        if [[ -d "$LLAMA_SRC/.git" ]]; then
            git -C "$LLAMA_SRC" pull --ff-only
        else
            git clone https://github.com/ggerganov/llama.cpp "$LLAMA_SRC" --depth 1
        fi
        cmake "$LLAMA_SRC" -B "$LLAMA_SRC/build" -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release
        cmake --build "$LLAMA_SRC/build" --config Release -j$(sysctl -n hw.ncpu)
        LLAMA_BIN_PATH="$LLAMA_SRC/build/bin/llama-server"
        if [[ -f "$LLAMA_BIN_PATH" ]]; then
            success "llama.cpp built successfully"
        else
            die "llama.cpp build failed. Install manually: brew install llama.cpp then re-run installer."
        fi
    fi

    cat > "$LLAMA_PLIST_PATH" << LLAMA_PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.zyrconlabs.cascadia.llama</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>
MODEL=\$(python3 -c "import json; c=json.load(open('${INSTALL_DIR}/config.json')); l=c.get('llm',{}); print(l.get('models_dir','${INSTALL_DIR}/models').rstrip('/')+'/'+l.get('model',''))" 2>/dev/null);
BIN=\$(python3 -c "import json; c=json.load(open('${INSTALL_DIR}/config.json')); print(c.get('llm',{}).get('llama_bin','${LLAMA_BIN_PATH}'))" 2>/dev/null || echo "${LLAMA_BIN_PATH}");
GPU=\$(python3 -c "import json; c=json.load(open('${INSTALL_DIR}/config.json')); print(c.get('llm',{}).get('n_gpu_layers',99))" 2>/dev/null || echo "99");
PROVIDER=\$(python3 -c "import json; c=json.load(open('${INSTALL_DIR}/config.json')); print(c.get('llm',{}).get('provider',''))" 2>/dev/null || echo "");
[[ "\$PROVIDER" != "llamacpp" ]] && exit 0;
[[ ! -f "\$MODEL" ]] && exit 0;
[[ ! -f "\$BIN" ]] && exit 0;
exec "\$BIN" --model "\$MODEL" --host 127.0.0.1 --port 8080 --ctx-size 4096 --n-gpu-layers "\$GPU"
        </string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/data/logs/llamacpp.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/data/logs/llamacpp.log</string>
</dict>
</plist>
LLAMA_PLIST

    launchctl unload "$LLAMA_PLIST_PATH" 2>/dev/null || true
    launchctl load "$LLAMA_PLIST_PATH" 2>/dev/null && \
        success "llama.cpp registered as login agent — auto-restarts on crash" || \
        info "llama.cpp launchd registration failed — will start manually via start.sh"

    # Add SwiftBar to Login Items so it auto-launches at boot
    # Find SwiftBar — check common locations
    SWIFTBAR_APP=""
    for candidate in "/Applications/SwiftBar.app" "$HOME/Applications/SwiftBar.app"; do
        if [[ -d "$candidate" ]]; then
            SWIFTBAR_APP="$candidate"
            break
        fi
    done
    if [[ -z "$SWIFTBAR_APP" ]]; then
        SWIFTBAR_APP=$(mdfind "kMDItemCFBundleIdentifier == 'com.ameba.SwiftBar'" 2>/dev/null | head -1)
    fi

    if [[ -n "$SWIFTBAR_APP" ]]; then
        osascript << APPLESCRIPT 2>/dev/null && \
            success "SwiftBar added to Login Items — launches automatically at boot" || \
            info "Could not add SwiftBar to Login Items — add manually in System Settings → General → Login Items"
tell application "System Events"
    if not (exists login item "SwiftBar") then
        make new login item at end of login items with properties ¬
            {name:"SwiftBar", path:"${SWIFTBAR_APP}", hidden:false}
    end if
end tell
APPLESCRIPT
    else
        info "SwiftBar not found — install with: brew install swiftbar"
        info "Then run install.sh again to register it as a login item"
    fi
fi

# ── 13. Start Cascadia and verify ────────────────────────────────────────────
echo ""
info "Starting Cascadia OS full stack (llama.cpp + all services)..."

# Unload any old launchd agent first — prevents it respawning the old instance
launchctl unload "$HOME/Library/LaunchAgents/com.zyrconlabs.cascadia.plist" 2>/dev/null || true
sleep 1

# Kill any stale instances
pkill -f "cascadia.kernel" 2>/dev/null || true
pkill -f "llama-server" 2>/dev/null || true
sleep 2

# Load the new launchd agent (points to new run.sh)
launchctl load "$HOME/Library/LaunchAgents/com.zyrconlabs.cascadia.plist" 2>/dev/null || true

# start.sh handles llama.cpp → Cascadia OS in correct order
mkdir -p "$INSTALL_DIR/data/logs"
cd "$INSTALL_DIR" && bash start.sh


# ── Open dashboard on first install ──────────────────────────────────────────
echo ""
if [[ ! -f "$INSTALL_DIR/.setup_complete" ]]; then
    info "Opening PRISM dashboard — choose your AI mode in Settings..."
    [[ "$(uname)" == "Darwin" ]] && open "http://localhost:6300/#settings" 2>/dev/null || true
    touch "$INSTALL_DIR/.setup_complete"
else
    info "Opening PRISM dashboard..."
    [[ "$(uname)" == "Darwin" ]] && open "http://localhost:6300" 2>/dev/null || true
fi

[[ "$(uname)" == "Darwin" ]] && open -g "swiftbar://refreshAllPlugins" 2>/dev/null || true

echo ""

# ── Cascadia OS by Zyrcon logo ────────────────────────────────────────────────
echo ""
echo -e "\033[1;35m   ██████╗ █████╗ ███████╗ ██████╗ █████╗ ██████╗ ██╗ █████╗ \033[0m"
echo -e "\033[1;35m  ██╔════╝██╔══██╗██╔════╝██╔════╝██╔══██╗██╔══██╗██║██╔══██╗\033[0m"
echo -e "\033[1;35m  ██║     ███████║███████╗██║     ███████║██║  ██║██║███████║\033[0m"
echo -e "\033[1;35m  ██║     ██╔══██║╚════██║██║     ██╔══██║██║  ██║██║██╔══██║\033[0m"
echo -e "\033[1;35m  ╚██████╗██║  ██║███████║╚██████╗██║  ██║██████╔╝██║██║  ██║\033[0m"
echo -e "\033[1;35m   ╚═════╝╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚═════╝ ╚═╝╚═╝  ╚═╝\033[0m"
echo -e "\033[0;90m                    O S   ·   b y   Z y r c o n\033[0m"
echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  Cascadia OS is running.                     ║"
echo "  ║                                              ║"
echo "  ║  → PRISM dashboard opened in your browser    ║"
echo "  ║  → Go to Settings to choose your AI mode     ║"
echo "  ║  → SwiftBar shows live system status         ║"
echo "  ║  → Starts automatically at every boot        ║"
echo "  ╚══════════════════════════════════════════════╝"
echo "" 

# ── PATH setup ────────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    for profile in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile"; do
        if [[ -f "$profile" ]] && ! grep -q ".local/bin" "$profile"; then
            echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$profile"
            info "Added ~/.local/bin to $profile"
            break
        fi
    done
    export PATH="$HOME/.local/bin:$PATH"
fi
