#!/usr/bin/env bash
# Cascadia OS — Zero-Touch Deployment
# Bootstraps a fresh server into a Cascadia OS node.
# Usage: curl -fsSL https://install.zyrcon.ai/deploy | bash
#   or:  bash zero_touch_deploy.sh [--config <url>] [--node-id <id>]
set -euo pipefail

CASCADIA_REPO="${CASCADIA_REPO:-https://github.com/zyrcon/cascadia-os}"
INSTALL_DIR="${INSTALL_DIR:-/opt/cascadia}"
NODE_ID="${NODE_ID:-$(hostname -s)}"
CONFIG_URL="${CONFIG_URL:-}"
PRISM_PORT="${PRISM_PORT:-6300}"
PYTHON_MIN="3.11"

log()  { echo "[cascadia] $*"; }
fail() { echo "[ERROR] $*" >&2; exit 1; }

# ── Prerequisites ─────────────────────────────────────────────────────────────

log "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || fail "python3 not found — install Python $PYTHON_MIN+"
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" \
  || fail "Python $PYTHON_MIN+ required, found $PY_VER"

command -v git >/dev/null 2>&1 || fail "git not found"
command -v pip3 >/dev/null 2>&1 || fail "pip3 not found"

log "Python $PY_VER OK"

# ── Clone or update ───────────────────────────────────────────────────────────

if [ -d "$INSTALL_DIR/.git" ]; then
  log "Updating existing installation at $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  log "Cloning Cascadia OS to $INSTALL_DIR..."
  git clone "$CASCADIA_REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── Python dependencies ───────────────────────────────────────────────────────

log "Installing Python dependencies..."
pip3 install -r requirements.txt --quiet

# ── Config ────────────────────────────────────────────────────────────────────

if [ -n "$CONFIG_URL" ]; then
  log "Fetching config from $CONFIG_URL..."
  curl -fsSL "$CONFIG_URL" -o config.json
elif [ ! -f config.json ] && [ -f config.example.json ]; then
  cp config.example.json config.json
  log "Created config.json from example"
fi

# Write node identity into config
if command -v jq >/dev/null 2>&1 && [ -f config.json ]; then
  jq --arg id "$NODE_ID" '.node_id = $id' config.json > config.json.tmp && mv config.json.tmp config.json
  log "Node ID set to: $NODE_ID"
fi

# ── Data directories ──────────────────────────────────────────────────────────

mkdir -p data/runtime data/logs data/backups

# ── Systemd service (Linux) ───────────────────────────────────────────────────

if command -v systemctl >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
  log "Installing systemd service..."
  cat > /etc/systemd/system/cascadia.service <<EOF
[Unit]
Description=Cascadia OS
After=network.target

[Service]
Type=simple
User=$(logname 2>/dev/null || echo root)
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 -m cascadia.kernel.flint --config $INSTALL_DIR/config.json --name flint
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable cascadia
  systemctl start cascadia
  log "Cascadia OS service started"
else
  log "Starting Cascadia OS (foreground)..."
  bash "$INSTALL_DIR/start.sh" &
  sleep 3
fi

# ── Health check ──────────────────────────────────────────────────────────────

PRISM_HEALTHY=false
log "Waiting for PRISM to come up on port $PRISM_PORT..."
for i in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:$PRISM_PORT/api/prism/health-check" >/dev/null 2>&1; then
    log "PRISM is healthy"
    PRISM_HEALTHY=true
    break
  fi
  sleep 2
done

if [ "$PRISM_HEALTHY" = false ]; then
  log "WARNING: PRISM did not respond within 30s — check logs at $INSTALL_DIR/data/logs/"
fi

# ── Production readiness check ────────────────────────────────────────────────

if [ "$PRISM_HEALTHY" = true ] && command -v jq >/dev/null 2>&1; then
  log "Checking production readiness..."
  PROD_STATUS=$(curl -sf "http://127.0.0.1:$PRISM_PORT/api/prism/production" 2>/dev/null || echo '{}')
  PROD_READY=$(echo "$PROD_STATUS" | jq -r '.production_ready // false')
  if [ "$PROD_READY" = "true" ]; then
    log "Production readiness: PASS"
  else
    log "Production readiness: FAIL — configuration issues detected:"
    echo "$PROD_STATUS" | jq -r '.issues[]? | "  ✗ " + .' 2>/dev/null || true
    log "Edit $INSTALL_DIR/config.json to resolve these before serving production traffic."
  fi
fi

log "Zero-touch deployment complete."
log "Dashboard: http://$(hostname -I | awk '{print $1}' 2>/dev/null || echo 'localhost'):$PRISM_PORT"
