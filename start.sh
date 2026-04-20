#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Cascadia OS — full stack startup
# Starts: Cascadia OS (11 components) + llama.cpp + RECON + QUOTE + CHIEF
# ═══════════════════════════════════════════════════════════════════════════
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"
# Model directory — reads from config.json, defaults to ./models inside install dir
MODELS_DIR=$(python3 -c "import json,os; c=json.load(open('config.json')); d=c.get('llm',{}).get('models_dir','./models'); print(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath('config.json')),d)) if d.startswith('.') else os.path.expanduser(d))" 2>/dev/null || echo "$REPO/models")
MODEL_FILE=$(python3 -c "import json; c=json.load(open('config.json')); print(c.get('llm',{}).get('model','qwen2.5-3b-instruct-q4_k_m.gguf'))" 2>/dev/null || echo "qwen2.5-3b-instruct-q4_k_m.gguf")
LLAMA_MODEL="$MODELS_DIR/$MODEL_FILE"

echo "Starting Cascadia OS full stack..."
echo ""

# ── 1. llama.cpp ──────────────────────────────────────────────────────────
if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
    echo "✓ llama.cpp already running"
elif [[ ! -f "$LLAMA_BIN" ]]; then
    echo "⚠ llama.cpp not installed — skipping (install via: brew install llama.cpp)"
elif [[ ! -f "$LLAMA_MODEL" ]]; then
    echo "⚠ AI model not downloaded yet — open PRISM → Settings to set up AI"
else
    echo "▸ Starting llama.cpp (Qwen 3B)..."
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
        echo "▸ Restarting Cascadia OS (stale instance from different path)..."
        pkill -f "cascadia.kernel" 2>/dev/null || true
        sleep 2
    fi
fi

if [[ "$CASCADIA_RUNNING" == "false" ]]; then
    echo "▸ Starting Cascadia OS..."
    PYTHON="${REPO}/.venv/bin/python"
    [[ ! -f "$PYTHON" ]] && PYTHON="python3"
    "$PYTHON" -m cascadia.kernel.watchdog --config config.json >> data/logs/flint.log 2>&1 &
    sleep 10
    curl -sf http://127.0.0.1:4011/health > /dev/null && echo "✓ Cascadia OS ready (11/11)" || echo "✗ Cascadia OS failed"
fi

# ── 3. Operators ──────────────────────────────────────────────────────────
# RECON
if curl -sf http://127.0.0.1:8002/api/health > /dev/null 2>&1; then
    echo "✓ RECON already running"
else
    echo "▸ Starting RECON..."
    mkdir -p data/vault/operators/recon/tasks/current
    if [[ ! -f data/vault/operators/recon/tasks/current/task.md ]]; then
        cp cascadia/operators/recon/tasks/current/task.md data/vault/operators/recon/tasks/current/ 2>/dev/null || true
    fi
    "$PYTHON" cascadia/operators/recon/dashboard.py >> data/logs/recon.log 2>&1 &
    sleep 2
    curl -sf http://127.0.0.1:8002/api/health > /dev/null && echo "✓ RECON ready" || echo "✗ RECON failed"
fi

# SCOUT
if curl -sf http://127.0.0.1:7002/api/health > /dev/null 2>&1; then
    echo "✓ SCOUT already running"
else
    echo "▸ Starting SCOUT..."
    "$PYTHON" cascadia/operators/scout/scout_server.py >> data/logs/scout.log 2>&1 &
    sleep 2
    curl -sf http://127.0.0.1:7002/api/health > /dev/null && echo "✓ SCOUT ready" || echo "✗ SCOUT failed"
fi

# ── 3. Operators ──────────────────────────────────────────────────────────
PYTHON="${REPO}/.venv/bin/python3"
[[ ! -f "$PYTHON" ]] && PYTHON="python3"

start_operator() {
    local op="$1" port="$2" entry="$3"
    local op_path="$REPO/cascadia/operators/$op/$entry"
    if curl -sf "http://127.0.0.1:${port}/api/health" > /dev/null 2>&1; then
        echo "✓ $op already running"
    elif [[ -f "$op_path" ]]; then
        "$PYTHON" "$op_path" >> "data/logs/${op}.log" 2>&1 &
        sleep 1
        curl -sf "http://127.0.0.1:${port}/api/health" > /dev/null             && echo "✓ $op started"             || echo "✗ $op failed — check data/logs/${op}.log"
    fi
}

start_operator "recon"                  "8002" "dashboard.py"
start_operator "scout"                  "7002" "scout_server.py"
start_operator "quote"                  "8007" "dashboard.py"
start_operator "chief"                  "8006" "dashboard.py"
start_operator "aurelia"                "8009" "dashboard.py"
start_operator "debrief"               "8008" "dashboard.py"
start_operator "competition-researcher" "8005" "dashboard.py"
start_operator "jr-programmer"          "8004" "dashboard.py"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Cascadia OS stack is up."
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  PRISM dashboard  →  http://localhost:6300/"
echo "  RECON            →  http://localhost:8002/"
echo "  SCOUT            →  http://localhost:7002/"
echo "  CHIEF            →  http://localhost:8006/"
echo "  QUOTE            →  http://localhost:8007/"
echo ""
echo "  Run demo:  bash demo.sh"
echo ""
