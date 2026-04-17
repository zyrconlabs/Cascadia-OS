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
VENV_DIR="$INSTALL_DIR/.venv"
MIN_PYTHON="3.11"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[cascadia]${NC} $*"; }
success() { echo -e "${GREEN}[cascadia]${NC} $*"; }
warn()    { echo -e "${YELLOW}[cascadia]${NC} $*"; }
die()     { echo -e "${RED}[cascadia] ERROR:${NC} $*" >&2; exit 1; }

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║       Cascadia OS v0.21 Installer     ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

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
pip install --quiet -e .
success "Package installed."

# ── 6. Config ─────────────────────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
    cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
    warn "config.json created from example. Edit it before starting."
else
    info "config.json already exists — skipping."
fi

# ── 7. First-time setup ───────────────────────────────────────────────────────
info "Running first-time setup (cascadia.installer.once)..."
"$VENV_DIR/bin/python" -m cascadia.installer.once --dir "$INSTALL_DIR"
success "Setup complete."

# ── 8. Launcher script ───────────────────────────────────────────────────────
LAUNCHER="$HOME/.local/bin/cascadia"
mkdir -p "$HOME/.local/bin"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
exec "$VENV_DIR/bin/python" -m cascadia.kernel.watchdog --config "$INSTALL_DIR/config.json" "\$@"
EOF
chmod +x "$LAUNCHER"

# ── 9. Open PRISM after start hint ──────────────────────────────────────────
info "Starting Cascadia OS..."
source "$VENV_DIR/bin/activate"
nohup "$VENV_DIR/bin/python" -m cascadia.kernel.watchdog --config "$INSTALL_DIR/config.json" > "$INSTALL_DIR/data/logs/watchdog.log" 2>&1 &
sleep 4
if [[ "$(uname)" == "Darwin" ]]; then open "http://127.0.0.1:18810" 2>/dev/null || true; fi

# ── 10. Done ──────────────────────────────────────────────────────────────────
echo ""
success "════════════════════════════════════════"
success " Cascadia OS v0.21 installed successfully"
success "════════════════════════════════════════"
echo ""
echo "  Start:   cascadia"
echo "  Config:  $INSTALL_DIR/config.json"
echo "  Logs:    $INSTALL_DIR/logs/"
echo ""
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
