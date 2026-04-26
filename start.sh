#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Cascadia OS — full stack startup
# Starts: llama.cpp + Cascadia OS (11 components)
# ═══════════════════════════════════════════════════════════════════════════
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

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
    sleep 6
    curl -sf http://127.0.0.1:8080/health > /dev/null && echo "✓ llama.cpp ready" || echo "✗ llama.cpp failed — check data/logs/llamacpp.log"
fi

# ── 2. Cascadia OS ────────────────────────────────────────────────────────
CASCADIA_RUNNING=false
if curl -sf http://127.0.0.1:4011/health > /dev/null 2>&1; then
    # Verify it's running from THIS directory, not a stale/backup instance
    RUNNING_PID=$(pgrep -f "cascadia.kernel.watchdog" | head -1)
    RUNNING_DIR=$(lsof -p "$RUNNING_PID" 2>/dev/null | grep cwd | awk '{print $NF}' || echo "")
    if [[ "$RUNNING_DIR" == "$REPO" ]]; then
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
    curl -sf http://127.0.0.1:4011/health > /dev/null && echo "✓ Cascadia OS ready" || echo "✗ Cascadia OS failed — check logs"
fi

# ── 3. Operators ──────────────────────────────────────────────────────────
# First-party operators are maintained in cascadia-os-operators (private).
# Start operators from that repo before running this script.
# See: https://github.com/zyrconlabs/cascadia-os-operators

# ── 4. Register operators with CREW ──────────────────────────────────────
# BELL self-registers with CREW automatically after startup.
# Commercial operators (cascadia-os-operators) self-register when started.
# Custom operators: POST http://127.0.0.1:5100/register with your operator_id.


echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Cascadia OS stack is up."
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  PRISM dashboard  →  http://localhost:6300/"
echo ""
echo "  Run demo:  bash demo.sh"
echo ""
