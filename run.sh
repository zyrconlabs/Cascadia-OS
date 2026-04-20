#!/usr/bin/env bash
# Cascadia OS startup wrapper — used by launchd
# Starts llama.cpp then hands off to the watchdog
cd "/Users/andy/cascadia-os"

INSTALL_DIR="/Users/andy/cascadia-os"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python3"
CONFIG="$INSTALL_DIR/config.json"
LOG_DIR="$INSTALL_DIR/data/logs"

mkdir -p "$LOG_DIR"

# Use venv python if available
if [[ ! -f "$VENV_PYTHON" ]]; then
    VENV_PYTHON="$(command -v python3)"
fi

# Start llama.cpp if configured
LLAMA_BIN=$("$VENV_PYTHON" -c "import json,os; c=json.load(open('$CONFIG')); print(c.get('llm',{}).get('llama_bin',''))" 2>/dev/null)
MODELS_DIR=$("$VENV_PYTHON" -c "import json,os; c=json.load(open('$CONFIG')); d=c.get('llm',{}).get('models_dir','./models'); print(os.path.abspath(os.path.join('$INSTALL_DIR',d)) if d.startswith('.') else os.path.expanduser(d))" 2>/dev/null)
MODEL_FILE=$("$VENV_PYTHON" -c "import json; c=json.load(open('$CONFIG')); print(c.get('llm',{}).get('model',''))" 2>/dev/null)

if [[ -n "$LLAMA_BIN" && -f "$LLAMA_BIN" && -n "$MODEL_FILE" ]]; then
    MODEL_PATH="$MODELS_DIR/$MODEL_FILE"
    if [[ -f "$MODEL_PATH" ]] && ! curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
        "$LLAMA_BIN" \
            --model "$MODEL_PATH" \
            --host 127.0.0.1 --port 8080 \
            --ctx-size 4096 --n-gpu-layers 99 \
            --alias "$MODEL_FILE" \
            >> "$LOG_DIR/llamacpp.log" 2>&1 &
        sleep 5
    fi
fi

# Start Cascadia OS via watchdog (this is the long-running process launchd manages)
exec "$VENV_PYTHON" -m cascadia.kernel.watchdog --config "$CONFIG"
