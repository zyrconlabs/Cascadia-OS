#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Cascadia OS — full stack startup
# Starts: llama.cpp + Cascadia OS (13 components)
# ═══════════════════════════════════════════════════════════════════════════
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# ── Operators directory ──────────────────────────────────────────────────
CASCADIA_OPERATORS_DIR="${CASCADIA_OPERATORS_DIR:-$HOME/Zyrcon/operators/cascadia-os-operators}"
export CASCADIA_OPERATORS_DIR
# ─────────────────────────────────────────────────────────────────────────

# Load environment variables (VAULT_ENCRYPTION_KEY, etc.) so all child processes inherit them.
[[ -f "$REPO/.env" ]] && set -a && source "$REPO/.env" && set +a

# Fail-fast after env is loaded — exits non-zero on any unhandled failure
# so LaunchAgent retries rather than accepting a silent partial start.
set -e

# Clear intentional-stop flag so LaunchAgent resumes normal KeepAlive
rm -f "$REPO/data/runtime/cascadia.stopped"

# ── Already-running guard (verify against REAL state, not a PID file) ──────
# Primary guard: if a HEALTHY kernel from THIS install dir is already
# answering on :4011, there is nothing to do — exit cleanly instead of
# launching a duplicate stack that would fight over ports and can tear down a
# working llama-server. We do NOT trust a PID lock alone here: start.sh
# forks-and-exits in the manual/deploy path, and its EXIT trap removes any
# lock while the child watchdog keeps running — so a later run would find no
# lock and wrongly start a second full stack (the confirmed duplicate bug).
mkdir -p "$REPO/data/runtime"
if curl -sf http://127.0.0.1:4011/health >/dev/null 2>&1; then
    _RUN_PID=$(pgrep -f "cascadia.kernel.watchdog" | head -1)
    if [ -n "$_RUN_PID" ] && ps -p "$_RUN_PID" -o command= 2>/dev/null | grep -qF "$REPO"; then
        echo "[cascadia] Already running and healthy (kernel PID $_RUN_PID, port 4011) — nothing to do"
        exit 0
    fi
    echo "[cascadia] Port 4011 up but not owned by $REPO — will reconcile below"
fi

# Secondary guard: block two concurrent start.sh runs racing during a cold
# start (before :4011 is up). A live-PID lock is sufficient for concurrency;
# a strictly-later run is intentionally allowed through, since every section
# below is idempotent (checks its own port/health before starting).
_LOCK="$REPO/data/runtime/cascadia.start.lock"
if [ -f "$_LOCK" ]; then
    _PID=$(cat "$_LOCK" 2>/dev/null || echo "")
    if [ -n "$_PID" ] && kill -0 "$_PID" 2>/dev/null; then
        echo "[cascadia] Another start.sh is already running (PID $_PID) — exiting"
        exit 0
    else
        echo "[cascadia] Clearing stale lock"
        rm -f "$_LOCK"
    fi
fi
echo $$ > "$_LOCK"
trap 'rm -f "$_LOCK"' EXIT INT TERM HUP
# ─────────────────────────────────────────────────────────────────────────

# Find llama-server — priority: brew → Zyrcon → fallback
LLAMA_BIN=""
for _candidate in \
    "/opt/homebrew/bin/llama-server" \
    "/usr/local/bin/llama-server" \
    "$HOME/Zyrcon/llama.cpp/build/bin/llama-server" \
    "$HOME/llama.cpp/build/bin/llama-server"; do
    if [[ -f "$_candidate" ]]; then
        LLAMA_BIN="$_candidate"
        break
    fi
done
if [[ -z "$LLAMA_BIN" ]]; then
    echo "⚠ llama.cpp not found — run install.sh to build it"
fi
# Model directory — reads from config.json, defaults to ./models inside install dir
MODELS_DIR=$(python3 -c "import json,os; c=json.load(open('config.json')); d=c.get('llm',{}).get('models_dir','./models'); print(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath('config.json')),d)) if d.startswith('.') else os.path.expanduser(d))" 2>/dev/null || echo "$REPO/models")
MODEL_FILE=$(python3 -c "import json; c=json.load(open('config.json')); print(c.get('llm',{}).get('model','qwen2.5-3b-instruct-q4_k_m.gguf'))" 2>/dev/null || echo "qwen2.5-3b-instruct-q4_k_m.gguf")
LLAMA_MODEL="$MODELS_DIR/$MODEL_FILE"

mkdir -p data/runtime/pids data/logs

echo "Starting Cascadia OS full stack..."

# Rotate startup.log if over 5MB
STARTUP_LOG="data/logs/startup.log"
if [[ -f "$STARTUP_LOG" ]] && [[ $(stat -f%z "$STARTUP_LOG" 2>/dev/null || echo 0) -gt 5242880 ]]; then
    mv "$STARTUP_LOG" "data/logs/startup.log.1"
    echo "$(date) | startup log rotated" > "$STARTUP_LOG"
fi
echo ""

# ── NATS (must start before everything else) ─────────────────────────────
echo "▸ Starting NATS..."
if lsof -i :4222 >/dev/null 2>&1; then
    echo "✓ NATS already running (port 4222)"
else
    if command -v nats-server >/dev/null 2>&1; then
        _NATS="nats-server"
    elif [ -x "$HOME/Zyrcon/runtime/bin/nats-server" ]; then
        _NATS="$HOME/Zyrcon/runtime/bin/nats-server"
    elif [ -f "$HOME/.local/bin/nats-server" ]; then
        _NATS="$HOME/.local/bin/nats-server"
    else
        echo "  nats-server not found — downloading..."
        _NVER="v2.10.18"
        _NARCH="darwin-arm64"
        [ "$(uname -m)" != "arm64" ] && _NARCH="darwin-amd64"
        mkdir -p "$HOME/.local/bin"
        curl -fsSL \
            "https://github.com/nats-io/nats-server/releases/download/$_NVER/nats-server-$_NVER-$_NARCH.zip" \
            -o /tmp/_nats.zip
        unzip -q /tmp/_nats.zip -d /tmp/_nats_tmp
        mv /tmp/_nats_tmp/*/nats-server "$HOME/.local/bin/nats-server"
        chmod +x "$HOME/.local/bin/nats-server"
        rm -rf /tmp/_nats.zip /tmp/_nats_tmp
        _NATS="$HOME/.local/bin/nats-server"
    fi
    $_NATS -p 4222 >> "$REPO/data/logs/nats.log" 2>&1 &
    _NW=0
    until lsof -i :4222 >/dev/null 2>&1 || [ $_NW -ge 10 ]; do
        sleep 1; _NW=$((_NW+1))
    done
    lsof -i :4222 >/dev/null 2>&1 \
        && echo "✓ NATS ready (port 4222)" \
        || echo "⚠ NATS not binding — check logs"
fi
# ─────────────────────────────────────────────────────────────────────────

# ── 1. llama.cpp ──────────────────────────────────────────────────────────
if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
    echo "✓ llama.cpp already running"
elif [[ ! -f "$LLAMA_BIN" ]]; then
    echo "⚠ llama.cpp not installed — run install.sh to set up"
elif [[ ! -f "$LLAMA_MODEL" ]]; then
    echo "⚠ AI model not downloaded yet — open PRISM → Settings to set up AI"
else
    echo "▸ Starting llama.cpp..."
    lsof -ti :8080 | xargs kill -9 2>/dev/null; sleep 1
    "$LLAMA_BIN" \
        --model "$LLAMA_MODEL" \
        --host 127.0.0.1 --port 8080 \
        --ctx-size 4096 --n-gpu-layers 99 \
        --alias qwen2.5-3b-instruct-q4_k_m.gguf \
        > data/logs/llamacpp.log 2>&1 &
    LLAMA_PID=$!
    echo $LLAMA_PID > data/runtime/pids/llama.pid
    sleep 6
    curl -sf http://127.0.0.1:8080/health > /dev/null && echo "✓ llama.cpp ready" || echo "✗ llama.cpp failed — check data/logs/llamacpp.log"
fi

# ── 2. License Gate ───────────────────────────────────────────────────────
# License Gate is a FLINT-managed service (config.json → services[license_gate]).
# Starting it directly here causes FLINT's _cleanup_orphan_components() to
# SIGKILL the orphan when FLINT boots, printing a spurious "Killed: 9" line.
# We skip the direct start and let FLINT own it; health check runs post-FLINT.

# NATS started before llama.cpp — see section below

# ── 3. Cascadia OS ────────────────────────────────────────────────────────
CASCADIA_RUNNING=false
if curl -sf http://127.0.0.1:4011/health > /dev/null 2>&1; then
    # Verify it's running from THIS directory, not a stale/backup instance
    RUNNING_PID=$(pgrep -f "cascadia.kernel.watchdog" | head -1)
    if ps -p "$RUNNING_PID" -o command= 2>/dev/null | grep -qF "$REPO"; then
        echo "✓ Cascadia OS already running"
        CASCADIA_RUNNING=true
    else
        echo "▸ Restarting Cascadia OS — stale instance detected..."
        pkill -f "cascadia.kernel" 2>/dev/null || true
        sleep 2
    fi
fi

if [[ "$CASCADIA_RUNNING" == "false" ]]; then
    echo "▸ Starting Cascadia OS..."
    PYTHON="${REPO}/.venv/bin/python3"
    [[ ! -f "$PYTHON" ]] && PYTHON="python3"
    "$PYTHON" -m cascadia.kernel.watchdog --config config.json >> data/logs/flint.log 2>&1 &
    WATCHDOG_PID=$!
    sleep 10
    FLINT_READY=false
    FLINT_STATE="unknown"
    for _i in $(seq 1 80); do
        FLINT_STATE=$(curl -s http://127.0.0.1:4011/health 2>/dev/null | python3 -c \
          "import json,sys; d=json.load(sys.stdin); print(d.get('state','unknown'))" 2>/dev/null)
        if [ "$FLINT_STATE" = "ready" ]; then
            FLINT_READY=true
            break
        fi
        sleep 1
    done
    if [ "$FLINT_READY" = true ]; then
        echo "✓ Cascadia OS ready (state=ready)"
    else
        echo "⚠ Cascadia OS did not reach ready state"
        echo "  Last state: $FLINT_STATE"
        echo "  Check data/logs/ for errors"
    fi
fi

# ── 3.5. License Gate ────────────────────────────────────────────────────
# Sources .env so ZYRCON_LICENSE_KEY is visible
# Always starts — returns lite if no key (no block)
echo "▸ Starting License Gate..."
set -a
[ -f "$REPO/.env" ] && source "$REPO/.env" 2>/dev/null || true
set +a
if ! curl -sf http://127.0.0.1:6100/api/health > /dev/null 2>&1; then
    python3 -m cascadia.licensing.license_gate \
        >> "$REPO/data/logs/license_gate.log" 2>&1 &
    _LG_WAIT=0
    until curl -sf http://127.0.0.1:6100/api/health \
        >/dev/null 2>&1 || [ $_LG_WAIT -ge 15 ]; do
        sleep 1; _LG_WAIT=$((_LG_WAIT+1))
    done
fi
if curl -sf http://127.0.0.1:6100/api/health >/dev/null 2>&1; then
    # /api/health is liveness-only now — read tier from /status (the licensing endpoint)
    _TIER=$(curl -s http://127.0.0.1:6100/api/license/status \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('tier','lite'))" \
        2>/dev/null || echo "lite")
    echo "✓ License Gate ready — tier: $_TIER"
else
    echo "⚠ License Gate slow — continuing as lite"
fi
# ─────────────────────────────────────────────────────

# ── 3.6. DEPOT Operator Catalog ──────────────────────────────────────────
# FLINT-managed (tier 3, port 6212). This block is a fallback: if FLINT did
# not start DEPOT within 30s, start it directly so operators are always reachable.
echo "▸ Checking DEPOT (port 6212)..."
_DEPOT_WAIT=0
until curl -sf http://127.0.0.1:6212/health > /dev/null 2>&1 || \
      [ $_DEPOT_WAIT -ge 30 ]; do
    sleep 1; _DEPOT_WAIT=$((_DEPOT_WAIT+1))
done
if ! curl -sf http://127.0.0.1:6212/health > /dev/null 2>&1; then
    echo "  DEPOT not up after 30s — starting directly..."
    python3 -m cascadia.depot.depot_api \
        >> "$REPO/data/logs/depot_api.log" 2>&1 &
    _DEPOT_WAIT2=0
    until curl -sf http://127.0.0.1:6212/health > /dev/null 2>&1 || \
          [ $_DEPOT_WAIT2 -ge 15 ]; do
        sleep 1; _DEPOT_WAIT2=$((_DEPOT_WAIT2+1))
    done
fi
if curl -sf http://127.0.0.1:6212/health > /dev/null 2>&1; then
    echo "✓ DEPOT ready on port 6212"
else
    echo "⚠ DEPOT not responding — operators may be unavailable"
fi
# ─────────────────────────────────────────────────────

# ── 4. PRISM Dashboard ────────────────────────────────────────────────────
echo "▸ Waiting for PRISM (tier 3)..."
PRISM_WAIT=0
until curl -sf http://127.0.0.1:6300/health > /dev/null 2>&1; do
    sleep 2
    PRISM_WAIT=$((PRISM_WAIT + 2))
    if [ $PRISM_WAIT -ge 60 ]; then
        echo "✗ PRISM did not come up after 60s — check data/logs/prism.log"
        break
    fi
done
if curl -sf http://127.0.0.1:6300/health > /dev/null 2>&1; then
    echo "✓ PRISM ready on port 6300"
fi

# ── 4.5. Purchase Webhook ─────────────────────────────────────────────────
# FLINT starts purchase_webhook (tier 3) alongside PRISM.
# Port 6214: 6210=operator_manager, 6211=chief, 6212=depot_api, 6213=sync_publisher.
PW_WAIT=0
until curl -sf http://127.0.0.1:6214/health > /dev/null 2>&1; do
    sleep 2
    PW_WAIT=$((PW_WAIT + 2))
    if [ $PW_WAIT -ge 30 ]; then
        echo "⚠ Purchase Webhook not up after 30s — Stripe auto-install unavailable (check data/logs/purchase_webhook.log)"
        break
    fi
done
if curl -sf http://127.0.0.1:6214/health > /dev/null 2>&1; then
    echo "✓ Purchase Webhook ready (port 6214)"
fi

# ── 5. Mission Manager ────────────────────────────────────────────────────
echo "Running missions migration..."
PYTHON="${REPO}/.venv/bin/python3"
[[ ! -f "$PYTHON" ]] && PYTHON="python3"
"$PYTHON" -m cascadia.missions.migrate >> data/logs/mission_manager.log 2>&1
_MM_CONFIGURED=false
if python3 -c "
import json,sys
try:
    c=json.load(open('config.json'))
    svcs=[s if isinstance(s,str) else s.get('name','') for s in c.get('services',[])]
    sys.exit(0 if 'mission_manager' in svcs else 1)
except: sys.exit(1)
" 2>/dev/null; then
    _MM_CONFIGURED=true
fi
if [ "$_MM_CONFIGURED" = true ]; then
    echo "▸ Waiting for Mission Manager..."
    MM_WAIT=0
    until curl -sf http://127.0.0.1:6207/healthz > /dev/null 2>&1; do
        sleep 2
        MM_WAIT=$((MM_WAIT + 2))
        if [ $MM_WAIT -ge 60 ]; then
            echo "✗ Mission Manager did not come up after 60s — check logs"
            break
        fi
    done
    curl -sf http://127.0.0.1:6207/healthz > /dev/null 2>&1 \
        && echo "✓ Mission Manager ready (port 6207)" || true
else
    echo "▸ Mission Manager not configured — skipping"
fi

# ── 6. Operators ──────────────────────────────────────────────────────────
# ── RECON operator ──────────────────────────────────
RECON_DIR="$CASCADIA_OPERATORS_DIR/recon"
echo "▸ Starting RECON..."
if curl -sf http://127.0.0.1:8002/api/health > /dev/null 2>&1; then
    echo "✓ RECON already running"
else
    cd "$RECON_DIR"
    python3 dashboard.py >> "$REPO/data/logs/recon.log" 2>&1 &
    RECON_PID=$!
    echo $RECON_PID > "$REPO/data/runtime/pids/recon.pid"
    cd "$REPO"
    sleep 3
    curl -sf http://127.0.0.1:8002/api/health > /dev/null \
        && echo "✓ RECON ready (PID $RECON_PID)" \
        || echo "⚠ RECON started but health check failed — check recon.log"
fi

# ── QUOTE_BRIEF operator ────────────────────────────
QUOTE_BRIEF_DIR="$CASCADIA_OPERATORS_DIR/quote_brief"
echo "▸ Starting QUOTE_BRIEF..."
if curl -sf http://127.0.0.1:8006/api/health > /dev/null 2>&1; then
    echo "✓ QUOTE_BRIEF already running"
else
    cd "$QUOTE_BRIEF_DIR"
    python3 server.py >> "$REPO/data/logs/quote_brief.log" 2>&1 &
    QUOTE_BRIEF_PID=$!
    echo $QUOTE_BRIEF_PID > "$REPO/data/runtime/pids/quote_brief.pid"
    cd "$REPO"
    sleep 3
    curl -sf http://127.0.0.1:8006/api/health > /dev/null \
        && echo "✓ QUOTE_BRIEF ready (PID $QUOTE_BRIEF_PID)" \
        || echo "⚠ QUOTE_BRIEF started but health check failed — check quote_brief.log"
fi
# QUOTE_BRIEF self-registers via its own persistent _crew_register_with_retry
# thread (same pattern as RECON). No curl needed here — server.py owns it.

# SOCIAL (activity_driven) — started by OM boot check if active sessions exist

# ── 7. Register operators with CREW ──────────────────────────────────────
# BELL self-registers with CREW automatically after startup.
# Commercial operators (cascadia-os-operators) self-register when started.
# Custom operators: POST http://127.0.0.1:5100/register with your operator_id.


# ── 8. Health Monitor (with auto-restart) ───────────────────────────────
# Kill any existing health_monitor loops before starting a new one.
# This prevents multiple loops accumulating across start.sh restarts,
# which would cause multiple processes competing on port 6209.
pkill -f "cascadia.monitoring.health_alert" 2>/dev/null || true; sleep 1
echo "▸ Starting Health Monitor..."
(
    while true; do
        python3 -m cascadia.monitoring.health_alert \
          >> data/logs/health_monitor.log 2>&1
        echo "[Health Monitor] Restarting after exit..." \
          >> data/logs/health_monitor.log
        sleep 5
    done
) &
HEALTH_PID=$!
echo $HEALTH_PID > data/runtime/pids/health_monitor.pid
sleep 1
if curl -sf http://localhost:6209/health > /dev/null 2>&1; then
    echo "✓ Health Monitor ready (port 6209)"
else
    echo "⚠ Health Monitor starting — check health_monitor.log"
fi

# ── 8.5. Memory pressure watchdog ────────────────────────────────────────
# Stops idle operators if swap activates. Checks every 5 minutes.
# Safe to re-run: kills any prior watchdog via PID file before starting.
_MW_PID_FILE="$REPO/data/runtime/pids/memwatch.pid"
if [ -f "$_MW_PID_FILE" ]; then
    _OLD_MW=$(cat "$_MW_PID_FILE" 2>/dev/null || echo "")
    [ -n "$_OLD_MW" ] && kill "$_OLD_MW" 2>/dev/null || true
    rm -f "$_MW_PID_FILE"
fi
echo "▸ Starting memory pressure watchdog..."
(
    _IDLE_OPS="aurelia debrief collect brief social code competition-researcher"
    while true; do
        sleep 300
        _SWAP=$(sysctl vm.swapusage 2>/dev/null \
            | awk -F'used = ' 'NR==1{print $2}' \
            | awk '{print $1}' | tr -d 'M')
        if [ -n "$_SWAP" ] && [ "$_SWAP" != "0.00" ]; then
            _TS=$(date '+%Y-%m-%d %H:%M:%S')
            echo "[$_TS] [memwatch] Swap active (${_SWAP}M) — stopping idle operators" \
                >> "$REPO/data/logs/memwatch.log"
            _UID=$(id -u)
            for _OP in $_IDLE_OPS; do
                _LPID=$(launchctl list 2>/dev/null \
                    | awk -v s="ai.zyrcon.$_OP" '$3==s && $1~/^[0-9]/{print $1}')
                if [ -n "$_LPID" ]; then
                    launchctl bootout "gui/$_UID/ai.zyrcon.$_OP" 2>/dev/null \
                        && echo "[$_TS] [memwatch] bootout $_OP" \
                        >> "$REPO/data/logs/memwatch.log"
                fi
            done
            # Alert via Telegram when swap exceeds 800 MB
            _SWAP_ALERT_THRESHOLD=800
            _SWAP_INT=${_SWAP%.*}
            if [ "${_SWAP_INT:-0}" -gt "$_SWAP_ALERT_THRESHOLD" ]; then
                echo "[$_TS] [memwatch] ⚠️ Swap ${_SWAP}MB — sending Telegram alert" \
                    >> "$REPO/data/logs/memwatch.log"
                curl -s -X POST http://127.0.0.1:6211/task \
                    -H "Content-Type: application/json" \
                    -d "{\"task\":\"SYSTEM ALERT\\n\\n🔴 SWAP MEMORY HIGH\\nSwap used: ${_SWAP} MB\\nThreshold: ${_SWAP_ALERT_THRESHOLD} MB\\n\\nRun /ram for full details.\\nConsider restarting idle services.\",\"metadata\":{\"chat_id\":\"${TELEGRAM_OWNER_CHAT_ID:-}\",\"source\":\"memwatch\",\"bypass_llm\":true}}" \
                    --connect-timeout 3 --silent 2>/dev/null || true
            fi
        fi
    done
) &
echo $! > "$_MW_PID_FILE"
echo "✓ Memory watchdog started (PID $(cat "$_MW_PID_FILE"))"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Cascadia OS stack is up."
echo "═══════════════════════════════════════════════════════════"
echo ""
# Component health summary
# /api/health is liveness-only — read tier from the /status licensing endpoint
_lg_tier=$(curl -sf http://127.0.0.1:6100/api/license/status 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tier','?'))" 2>/dev/null || echo "?")
echo "  License Gate     →  http://127.0.0.1:6100/api/health  (tier: $_lg_tier)"
echo "  PRISM            →  http://localhost:6300/health"
echo "  Mission Manager  →  http://localhost:6207/healthz"
echo ""
echo "  Run demo:  bash demo.sh"
echo ""

# ── Core services health check ───────────────────────────────────────────
echo "Verifying core services..."
_CHECKS=0
for _PORT in 6211 6300 8002; do
    for _I in 1 2 3 4 5; do
        if curl -s --connect-timeout 2 \
            "http://127.0.0.1:${_PORT}/health" \
            2>/dev/null | \
            grep -qE "ok|ready|healthy"; then
            echo "✅ Port ${_PORT} OK"
            _CHECKS=$((_CHECKS + 1))
            break
        fi
        sleep 3
    done
done
if [ "$_CHECKS" -lt 2 ]; then
    echo "❌ Core services failed — LaunchAgent will retry"
    exit 1
fi
echo "✅ Cascadia OS started successfully"
# Agent (launchd) sets CASCADIA_FOREGROUND=1: stay in the foreground blocking on the
# watchdog so launchd keeps the job alive and does NOT reap the stack's descendant tree
# on exit (setsid/nohup can't escape launchd descendant-tracking). update_node and manual
# runs leave it unset → fork-and-exit as before (start.sh returns, deploy proceeds).
if [ "${CASCADIA_FOREGROUND:-0}" = "1" ]; then
    if [ -n "${WATCHDOG_PID:-}" ]; then
        echo "Foreground mode: waiting on watchdog (PID $WATCHDOG_PID)..."
        wait "$WATCHDOG_PID"
    else
        echo "Foreground mode: supervising existing watchdog..."
        while pgrep -f 'cascadia.kernel.watchdog' >/dev/null 2>&1; do sleep 5; done
    fi
else
    exit 0
fi
