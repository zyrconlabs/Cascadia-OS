#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Cascadia OS — One-Click Installer  (macOS, zero admin required)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/zyrconlabs/cascadia-os/main/install.sh | bash
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
BRANCH="main"
INSTALL_DIR="$HOME/cascadia-os"
VENV_DIR="$INSTALL_DIR/.venv"
BIN_DIR="$HOME/.local/bin"
MODEL_DIR="$INSTALL_DIR/models"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[cascadia]${NC} $*"; }
ok()      { echo -e "${GREEN}[cascadia] ✓${NC} $*"; }
warn()    { echo -e "${YELLOW}[cascadia] ⚠${NC} $*"; }
die()     { echo -e "${RED}[cascadia] ✗${NC} $*" >&2; exit 1; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "\033[1;31m  ███████╗██╗   ██╗██████╗  ██████╗ ██████╗ ███╗   ██╗\033[0m"
echo -e "\033[1;31m     ███╔╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔═══██╗████╗  ██║\033[0m"
echo -e "\033[1;31m    ███╔╝  ╚████╔╝ ██████╔╝██║     ██║   ██║██╔██╗ ██║\033[0m"
echo -e "\033[1;31m   ███╔╝    ╚██╔╝  ██╔══██╗██║     ██║   ██║██║╚██╗██║\033[0m"
echo -e "\033[1;31m  ███████╗   ██║   ██║  ██║╚██████╗╚██████╔╝██║ ╚████║\033[0m"
echo -e "\033[1;31m  ╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝\033[0m"
echo -e "\033[0;90m                      A I   P L A T F O R M\033[0m"
echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║        Cascadia OS — Installer           ║"
echo "  ║        AI Business Operating System      ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

# ── Preflight ─────────────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || die "Cascadia OS requires macOS."

# Chip detection — used for binary downloads
ARCH=$(uname -m)   # arm64 or x86_64
[[ "$ARCH" == "arm64" ]] && LLAMA_ARCH="arm64" || LLAMA_ARCH="x64"
[[ "$ARCH" == "arm64" ]] && NATS_ARCH="arm64"  || NATS_ARCH="amd64"

# Disk space (5 GB minimum)
FREE_GB=$(df -g "$HOME" | tail -1 | awk '{print $4}')
[[ "$FREE_GB" -ge 5 ]] || die "Need at least 5 GB free disk space. Have ${FREE_GB} GB."

# curl is always present on macOS
command -v curl &>/dev/null || die "curl not found — this should not happen on macOS."
ok "curl ready"

# ── ~/bin setup ───────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
export PATH="$BIN_DIR:$PATH"
for _profile in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [[ -f "$_profile" ]] && ! grep -q "$BIN_DIR" "$_profile" 2>/dev/null; then
        echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$_profile"
    fi
done

# ── Disclosure + confirmation ─────────────────────────────────────────────────
echo ""
echo "  ┌─────────────────────────────────────────────────────────────────┐"
echo "  │                   BEFORE YOU CONTINUE                          │"
echo "  ├─────────────────────────────────────────────────────────────────┤"
echo "  │                                                                 │"
echo "  │  This installer will automatically:                            │"
echo "  │                                                                 │"
echo "  │  ● Install Python 3.12      (user space, no admin needed)      │"
echo "  │  ● Install Python packages  (flask, cryptography, and others)  │"
echo "  │  ● Download NATS server     (~10 MB binary)                    │"
echo "  │  ● Download AI runtime      (llama.cpp binary)                 │"
echo "  │  ● Download an AI model     (1–4 GB depending on selection)    │"
echo "  │  ● Register a login agent   (auto-starts Cascadia at boot)     │"
echo "  │                                                                 │"
echo "  │  Files are written to:                                         │"
echo "  │  ● ~/cascadia-os/           (application)                      │"
echo "  │  ● ~/.local/bin/            (nats-server, llama-server)        │"
echo "  │  ● ~/Library/LaunchAgents/  (login agent)                      │"
echo "  │                                                                 │"
echo "  │  No administrator password is required.                        │"
echo "  │  Nothing outside your home folder is modified.                 │"
echo "  │                                                                 │"
echo "  │  To uninstall:                                                  │"
echo "  │  https://github.com/zyrconlabs/Cascadia-OS/blob/main/UNINSTALL.md │"
echo "  │                                                                 │"
echo "  │  By continuing you agree to the terms in LICENSE.              │"
echo "  │                                                                 │"
echo "  └─────────────────────────────────────────────────────────────────┘"
echo ""
read -r -p "  Continue with installation? [y/N]  " _confirm </dev/tty
echo ""
[[ "$_confirm" =~ ^[Yy]$ ]] || { echo "  Installation cancelled."; echo ""; exit 0; }
echo "  ✓ Starting installation..."
echo ""

# ── Python via uv (user-space, no admin, no brew) ─────────────────────────────
# uv manages Python versions in ~/.local/share/uv — no sudo ever
info "Setting up Python..."
UV_BIN="$BIN_DIR/uv"
if ! command -v uv &>/dev/null; then
    info "Installing uv (Python manager)..."
    curl -fsSL https://astral.sh/uv/install.sh | sh
    # uv installs itself to ~/.local/bin — now in PATH
fi
command -v uv &>/dev/null || die "uv install failed. Check your internet connection."
ok "uv $(uv --version | awk '{print $2}')"

# ── Download repo (curl + unzip — no git/Xcode required) ─────────────────────
info "Downloading Cascadia OS..."
if [[ -d "$INSTALL_DIR" ]]; then
    warn "Existing install found — downloading latest..."
    rm -rf "$INSTALL_DIR"
fi
curl -fsSL "https://github.com/zyrconlabs/cascadia-os/archive/refs/heads/${BRANCH}.zip" \
    -o /tmp/cascadia_src.zip
unzip -q /tmp/cascadia_src.zip -d /tmp/cascadia_dl
mv "/tmp/cascadia_dl/cascadia-os-${BRANCH}" "$INSTALL_DIR"
rm -rf /tmp/cascadia_src.zip /tmp/cascadia_dl
ok "Cascadia OS downloaded"

# ── Virtual environment + packages ────────────────────────────────────────────
info "Installing Python packages..."
cd "$INSTALL_DIR"
# uv creates the venv and installs deps — Python 3.12 auto-downloaded if needed
uv venv "$VENV_DIR" --python 3.12 --quiet
uv pip install --python "$VENV_DIR" -e ".[operators]" --quiet
ok "Python packages installed"

# ── Silent first-time setup ───────────────────────────────────────────────────
info "Running first-time setup..."
if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
    cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
fi
"$VENV_DIR/bin/python" -m cascadia.installer.once \
    --dir "$INSTALL_DIR" --config config.json --no-browser
ok "Setup complete"

# ── NATS server binary ────────────────────────────────────────────────────────
info "Installing NATS server..."
if ! command -v nats-server &>/dev/null; then
    NATS_TAG=$(curl -fsSL "https://api.github.com/repos/nats-io/nats-server/releases/latest" \
        | "$VENV_DIR/bin/python" -c \
          "import json,sys; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null \
        || echo "v2.14.0")
    NATS_URL="https://github.com/nats-io/nats-server/releases/download/${NATS_TAG}/nats-server-${NATS_TAG}-darwin-${NATS_ARCH}.tar.gz"
    info "Downloading NATS ${NATS_TAG}..."
    curl -fsSL "$NATS_URL" | tar -xz -C /tmp
    mv "/tmp/nats-server-${NATS_TAG}-darwin-${NATS_ARCH}/nats-server" "$BIN_DIR/nats-server"
    chmod +x "$BIN_DIR/nats-server"
    rm -rf "/tmp/nats-server-${NATS_TAG}-darwin-${NATS_ARCH}"
fi
ok "NATS $(nats-server --version)"

# ── llama.cpp binary ──────────────────────────────────────────────────────────
info "Installing AI runtime (llama.cpp)..."
LLAMA_BIN=""
for _candidate in \
    "$BIN_DIR/llama-server" \
    "/usr/local/bin/llama-server" \
    "$HOME/Zyrcon/llama.cpp/build/bin/llama-server"; do
    [[ -f "$_candidate" ]] && { LLAMA_BIN="$_candidate"; break; }
done

if [[ -z "$LLAMA_BIN" ]]; then
    LLAMA_TAG=$(curl -fsSL "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest" \
        | "$VENV_DIR/bin/python" -c \
          "import json,sys; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null \
        || echo "")
    if [[ -n "$LLAMA_TAG" ]]; then
        LLAMA_ARCHIVE="llama-${LLAMA_TAG}-bin-macos-${LLAMA_ARCH}.tar.gz"
        LLAMA_URL="https://github.com/ggerganov/llama.cpp/releases/download/${LLAMA_TAG}/${LLAMA_ARCHIVE}"
        info "Downloading llama.cpp ${LLAMA_TAG}..."
        if curl -fsSL "$LLAMA_URL" -o /tmp/llama.tar.gz 2>/dev/null; then
            mkdir -p /tmp/llama_extract
            tar -xzf /tmp/llama.tar.gz -C /tmp/llama_extract
            find /tmp/llama_extract -name "llama-server" -exec mv {} "$BIN_DIR/llama-server" \; 2>/dev/null || true
            rm -rf /tmp/llama.tar.gz /tmp/llama_extract
            [[ -f "$BIN_DIR/llama-server" ]] && chmod +x "$BIN_DIR/llama-server"
            LLAMA_BIN="$BIN_DIR/llama-server"
        else
            warn "llama.cpp download failed — AI features require llama-server in PATH"
        fi
    else
        warn "Could not fetch llama.cpp release info — AI features require llama-server in PATH"
    fi
fi

[[ -n "$LLAMA_BIN" ]] && ok "AI runtime at $LLAMA_BIN" || \
    warn "AI runtime not installed — limited mode until llama-server is available"

# ── Config ────────────────────────────────────────────────────────────────────
info "Updating config paths..."
mkdir -p "$INSTALL_DIR/data/logs"
mkdir -p "$INSTALL_DIR/data/runtime/pids"
mkdir -p "$MODEL_DIR"

"$VENV_DIR/bin/python" - <<PYEOF
import json, os
from pathlib import Path

p = Path("$INSTALL_DIR/config.json")
c = json.loads(p.read_text())

home = os.environ["HOME"]

def fix(v):
    if isinstance(v, str):
        return v.replace("/Users/andy", home).replace("/Users/zyrcon", home)
    if isinstance(v, dict): return {k: fix(x) for k, x in v.items()}
    if isinstance(v, list): return [fix(x) for x in v]
    return v

c = fix(c)

llm = c.setdefault("llm", {})
llm["models_dir"] = "$MODEL_DIR"
if "$LLAMA_BIN":
    llm["llama_bin"] = "$LLAMA_BIN"

p.write_text(json.dumps(c, indent=2))
PYEOF
ok "Config updated"

# ── AI model ──────────────────────────────────────────────────────────────────
echo ""
if ls "$MODEL_DIR"/*.gguf 2>/dev/null | head -1 | grep -q gguf; then
    ok "AI model already present"
else
    info "Select AI model:"
    echo ""
    echo "  1) Qwen 2.5 1.5B  — ~1 GB  (fastest, lighter tasks)"
    echo "  2) Qwen 2.5 3B    — ~2 GB  (recommended)"
    echo "  3) Skip           — configure later in PRISM Settings"
    echo ""
    read -r -p "  Choice [1/2/3]: " _model_choice </dev/tty
    echo ""
    case "$_model_choice" in
        1)
            _model_file="qwen2.5-1.5b-instruct-q4_k_m.gguf"
            _model_url="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/${_model_file}"
            ;;
        2)
            _model_file="qwen2.5-3b-instruct-q4_k_m.gguf"
            _model_url="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/${_model_file}"
            ;;
        *)
            _model_file=""
            warn "Skipped — open PRISM → Settings to download a model later"
            ;;
    esac

    if [[ -n "${_model_file:-}" ]]; then
        info "Downloading ${_model_file} (this takes a few minutes)..."
        curl -fsSL --progress-bar "$_model_url" -o "$MODEL_DIR/$_model_file" && \
            ok "AI model ready" || \
            warn "Download failed — retry in PRISM → Settings"

        # Update config with selected model
        "$VENV_DIR/bin/python" - <<PYEOF
import json
from pathlib import Path
p = Path("$INSTALL_DIR/config.json")
c = json.loads(p.read_text())
c.setdefault("llm", {})["model"] = "$_model_file"
p.write_text(json.dumps(c, indent=2))
PYEOF
    fi
fi

# ── Launcher script ───────────────────────────────────────────────────────────
LAUNCHER="$BIN_DIR/cascadia"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
cd "$INSTALL_DIR"
exec bash "$INSTALL_DIR/start.sh"
EOF
chmod +x "$LAUNCHER"

# ── Startup wrapper (activates venv for launchd) ──────────────────────────────
STARTUP_WRAPPER="$INSTALL_DIR/run.sh"
cat > "$STARTUP_WRAPPER" <<EOF
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
cd "$INSTALL_DIR"
exec bash "$INSTALL_DIR/start.sh"
EOF
chmod +x "$STARTUP_WRAPPER"

# ── Flint / SwiftBar plugin ───────────────────────────────────────────────────
FLINT_SRC="$INSTALL_DIR/cascadia/flint/cascadia.5s.sh"
SWIFTBAR_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
if [[ -f "$FLINT_SRC" ]] && [[ -d "$SWIFTBAR_DIR" ]]; then
    chmod +x "$FLINT_SRC"
    ln -sf "$FLINT_SRC" "$SWIFTBAR_DIR/cascadia.5s.sh"
    ok "Flint plugin linked to SwiftBar"
elif [[ ! -d "$SWIFTBAR_DIR" ]]; then
    warn "SwiftBar not installed — menu bar controller disabled"
    warn "Install manually: https://swiftbar.app"
fi

# ── Login agent (no sudo — ~/Library is user-owned) ───────────────────────────
info "Registering auto-start login agent..."
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/ai.cascadia.os.plist"
mkdir -p "$PLIST_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.cascadia.os</string>
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
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/data/logs/startup.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/data/logs/startup.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load  "$PLIST_PATH" 2>/dev/null && \
    ok "Login agent registered — Cascadia starts automatically at boot" || \
    warn "launchctl load failed — start manually: bash ~/cascadia-os/start.sh"

# ── PATH reminder ─────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "Run: source ~/.zshrc   (to get 'cascadia' command in this session)"
fi

# ── Vault encryption key ──────────────────────────────────────────────────────
# Generates ONCE on fresh install — NEVER overwrites existing key
info "Configuring vault..."
_ENV_FILE="$INSTALL_DIR/.env"
touch "$_ENV_FILE" 2>/dev/null || true
chmod 600 "$_ENV_FILE" 2>/dev/null || true
if ! grep -q "VAULT_ENCRYPTION_KEY" "$_ENV_FILE" 2>/dev/null; then
    _VKEY=$("$VENV_DIR/bin/python3" -c "
import secrets, base64
print(base64.b64encode(secrets.token_bytes(32)).decode())
")
    echo "VAULT_ENCRYPTION_KEY=$_VKEY" >> "$_ENV_FILE"
    ok "Vault key generated"
else
    ok "Vault key already present"
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── First boot ────────────────────────────────────────────────────────────────
echo ""
info "Starting Cascadia OS..."
pkill -f "cascadia.kernel" 2>/dev/null || true
sleep 1
mkdir -p "$INSTALL_DIR/data/logs"
bash "$INSTALL_DIR/run.sh" >> "$INSTALL_DIR/data/logs/startup.log" 2>&1 &
info "Waiting for services (25s)..."
sleep 25

# ── Health checks ─────────────────────────────────────────────────────────────
echo ""
info "Health checks..."
_pass=0; _fail=0
for _ep in "6300/health:PRISM" "5100/health:CREW" "6200/health:BEACON"; do
    _url="http://127.0.0.1:${_ep%%:*}"
    _name="${_ep##*:}"
    if curl -sf "$_url" >/dev/null 2>&1; then
        ok "$_name"; ((_pass++)) || true
    else
        warn "$_name not responding yet"; ((_fail++)) || true
    fi
done

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
# SEAM-1: mint a ONE-TIME activation bootstrap credential. Only its sha256+expiry
# reach disk (0600); the plaintext is captured into a shell var (never written,
# never echoed) and rides the URL FRAGMENT to the local /activate page, which
# exchanges it for a durable paired token. set -x is off, so `open "$URL"` never
# traces the credential arg.
_BOOT_DIR="$INSTALL_DIR/data/runtime"
mkdir -p "$_BOOT_DIR"
_BOOT_CRED=$("$VENV_DIR/bin/python3" -c "
import secrets, hashlib, json, time, os
c = secrets.token_urlsafe(32)
p = os.path.join('$_BOOT_DIR', '.activation_bootstrap')
with open(p, 'w') as f:
    f.write(json.dumps({'sha256': hashlib.sha256(c.encode()).hexdigest(), 'expires_at': int(time.time()) + 600}))
os.chmod(p, 0o600)
print(c)
") || _BOOT_CRED=""
if [[ ! -f "$INSTALL_DIR/.setup_complete" ]]; then
    [[ "$(uname)" == "Darwin" ]] && \
        open "http://127.0.0.1:6300/activate#bootstrap=${_BOOT_CRED}" 2>/dev/null || true
else
    [[ "$(uname)" == "Darwin" ]] && \
        open "http://localhost:6300" 2>/dev/null || true
fi
_BOOT_CRED=""
touch "$INSTALL_DIR/.setup_complete"

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
if [[ "${_fail}" -eq 0 ]]; then
echo "  ║  ✓ All services healthy                      ║"
else
echo "  ║  ⚠ Some services still starting — see logs  ║"
fi
echo "  ║                                              ║"
echo "  ║  Dashboard:  http://localhost:6300           ║"
echo "  ║  Logs:       ~/cascadia-os/data/logs/        ║"
echo "  ║                                              ║"
echo "  ║  To stop:    bash ~/cascadia-os/stop.sh      ║"
echo "  ║  To start:   bash ~/cascadia-os/start.sh     ║"
echo "  ║  Or just:    cascadia  (after source ~/.zshrc) ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
