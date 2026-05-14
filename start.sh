#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Cascadia OS — full stack startup
# Starts: llama.cpp + Cascadia OS (13 components)
# ═══════════════════════════════════════════════════════════════════════════
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# Load environment variables (VAULT_ENCRYPTION_KEY, etc.) so all child processes inherit them.
[[ -f "$REPO/.env" ]] && set -a && source "$REPO/.env" && set +a

# Clear intentional-stop flag so LaunchAgent resumes normal KeepAlive
rm -f "$REPO/data/runtime/cascadia.stopped"

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

mkdir -p data/runtime/pids

echo "Starting Cascadia OS full stack..."

# Rotate startup.log if over 5MB
STARTUP_LOG="data/logs/startup.log"
if [[ -f "$STARTUP_LOG" ]] && [[ $(stat -f%z "$STARTUP_LOG" 2>/dev/null || echo 0) -gt 5242880 ]]; then
    mv "$STARTUP_LOG" "data/logs/startup.log.1"
    echo "$(date) | startup log rotated" > "$STARTUP_LOG"
fi
echo ""

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
if curl -sf http://127.0.0.1:6100/api/health > /dev/null 2>&1; then
    echo "✓ License Gate already running"
else
    echo "▸ Starting License Gate..."
    PYTHON="${REPO}/.venv/bin/python3"
    [[ ! -f "$PYTHON" ]] && PYTHON="python3"
    "$PYTHON" -m cascadia.licensing.license_gate >> data/logs/license_gate.log 2>&1 &
    sleep 2
    curl -sf http://127.0.0.1:6100/api/health > /dev/null && echo "✓ License Gate ready" || echo "✗ License Gate failed — check data/logs/license_gate.log"
fi

# ── 2.5 NATS message bus ──────────────────────────────────────────────────
if command -v nats-server &> /dev/null; then
    if ! curl -sf http://localhost:8222/healthz > /dev/null 2>&1; then
        echo "▸ Starting NATS..."
        if [ -f "config/nats.conf" ]; then
            nats-server -c config/nats.conf &
        else
            nats-server -p 4222 -m 8222 >> data/logs/nats.log 2>&1 &
        fi
        NATS_PID=$!
        echo $NATS_PID > data/runtime/pids/nats.pid
        sleep 1
        if curl -sf http://localhost:8222/healthz > /dev/null 2>&1; then
            echo "✓ NATS ready on port 4222"
        else
            echo "⚠ NATS failed to start — check data/logs/nats.log"
        fi
    else
        echo "✓ NATS already running on port 4222"
    fi
else
    echo "⚠ NATS not installed — real-time events disabled"
    echo "  Install with: brew install nats-server"
fi

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

# ── 5. Mission Manager ────────────────────────────────────────────────────
echo "Running missions migration..."
PYTHON="${REPO}/.venv/bin/python3"
[[ ! -f "$PYTHON" ]] && PYTHON="python3"
"$PYTHON" -m cascadia.missions.migrate >> data/logs/mission_manager.log 2>&1
echo "▸ Waiting for Mission Manager (tier 2)..."
MM_WAIT=0
until curl -sf http://127.0.0.1:6207/healthz > /dev/null 2>&1; do
    sleep 2
    MM_WAIT=$((MM_WAIT + 2))
    if [ $MM_WAIT -ge 60 ]; then
        echo "✗ Mission Manager did not come up after 60s — check logs"
        break
    fi
done
if curl -sf http://127.0.0.1:6207/healthz > /dev/null 2>&1; then
    echo "✓ Mission Manager ready on port 6207"
fi

# ── 6. Operators ──────────────────────────────────────────────────────────
# ── RECON operator ──────────────────────────────────
RECON_DIR="/Users/andy/Zyrcon/operators/cascadia-os-operators/recon"
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

# CHIEF (on_demand) — started by Mission Manager via OM API: POST /operators/chief/wake
# SOCIAL (activity_driven) — started by OM boot check if active sessions exist

# ── 7. Register operators with CREW ──────────────────────────────────────
# BELL self-registers with CREW automatically after startup.
# Commercial operators (cascadia-os-operators) self-register when started.
# Custom operators: POST http://127.0.0.1:5100/register with your operator_id.


# ── 8. Health Monitor (with auto-restart) ───────────────────────────────
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

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Cascadia OS stack is up."
echo "═══════════════════════════════════════════════════════════"
echo ""
# Component health summary
_lg_health=$(curl -sf http://127.0.0.1:6100/api/health 2>/dev/null)
_lg_tier=$(echo "$_lg_health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tier','?'))" 2>/dev/null || echo "?")
echo "  License Gate     →  http://127.0.0.1:6100/api/health  (tier: $_lg_tier)"
echo "  PRISM            →  http://localhost:6300/health"
echo "  Mission Manager  →  http://localhost:6207/healthz"
echo ""
echo "  Run demo:  bash demo.sh"
echo ""
