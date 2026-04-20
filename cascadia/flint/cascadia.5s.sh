#!/bin/bash
# Cascadia OS — Flint Menu Bar Controller
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
# <swiftbar.version>1.0.0</swiftbar.version>

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$REPO_DIR/data/logs"
VAULT_DIR="$REPO_DIR/data/vault"
PRISM_PORT=6300
FLINT_PORT=4011

# Find Python — check .venv first
if [[ -f "$REPO_DIR/.venv/bin/python3" ]]; then
  PYTHON="$REPO_DIR/.venv/bin/python3"
elif [[ -f "$REPO_DIR/venv/bin/python3" ]]; then
  PYTHON="$REPO_DIR/venv/bin/python3"
else
  PYTHON="$(command -v python3)"
fi

mkdir -p "$LOG_DIR"

# ── Handle actions ────────────────────────────────────────────────────────────
case "${1:-}" in
  start-all)
    cd "$REPO_DIR"
    # Kill any existing instance cleanly
    pkill -f "cascadia.kernel" 2>/dev/null || true
    lsof -ti :8080 | xargs kill -9 2>/dev/null || true
    sleep 1
    # Start llama.cpp if configured
    CONFIG="$REPO_DIR/config.json"
    if [[ -f "$CONFIG" ]]; then
      LLAMA_BIN=$($PYTHON -c "import json,os; c=json.load(open('$CONFIG')); print(c.get('llm',{}).get('llama_bin',''))" 2>/dev/null)
      MODELS_DIR=$($PYTHON -c "import json,os; c=json.load(open('$CONFIG')); d=c.get('llm',{}).get('models_dir','./models'); print(os.path.abspath(os.path.join('$REPO_DIR',d)) if d.startswith('.') else os.path.expanduser(d))" 2>/dev/null)
      MODEL_FILE=$($PYTHON -c "import json; c=json.load(open('$CONFIG')); print(c.get('llm',{}).get('model',''))" 2>/dev/null)
      if [[ -n "$LLAMA_BIN" && -f "$LLAMA_BIN" && -n "$MODEL_FILE" ]]; then
        MODEL_PATH="$MODELS_DIR/$MODEL_FILE"
        if [[ -f "$MODEL_PATH" ]]; then
          nohup "$LLAMA_BIN" \
            --model "$MODEL_PATH" \
            --host 127.0.0.1 --port 8080 \
            --ctx-size 4096 --n-gpu-layers 99 \
            --alias "$MODEL_FILE" \
            > "$LOG_DIR/llamacpp.log" 2>&1 &
        fi
      fi
    fi
    # Start Cascadia OS
    nohup "$PYTHON" -m cascadia.kernel.watchdog \
      --config "$REPO_DIR/config.json" \
      > "$LOG_DIR/flint.log" 2>&1 &
    sleep 2
    exit 0
    ;;
  stop-all)
    pkill -f "cascadia.kernel" 2>/dev/null || true
    lsof -ti :8080 | xargs kill -9 2>/dev/null || true
    exit 0
    ;;
  open-prism)
    open "http://localhost:$PRISM_PORT/" 2>/dev/null
    exit 0
    ;;
  open-settings)
    open "http://localhost:$PRISM_PORT/#settings" 2>/dev/null
    exit 0
    ;;
  open-health)
    open "http://localhost:$PRISM_PORT/#health" 2>/dev/null
    exit 0
    ;;
  open-vault)
    open "$VAULT_DIR" 2>/dev/null
    exit 0
    ;;
esac

# ── Health check ──────────────────────────────────────────────────────────────
check() {
  curl -sf --max-time 1 "http://127.0.0.1:$1$2" > /dev/null 2>&1 && echo "1" || echo "0"
}

COMPONENTS=(4011 5100 5101 5102 5103 6200 6201 6202 6203 6204 6205 6300)
online=0
total=${#COMPONENTS[@]}
for port in "${COMPONENTS[@]}"; do
  [[ "$(check $port /health)" == "1" ]] && online=$((online+1))
done

flint_up=$(check $FLINT_PORT /health)
llama_up=$(check 8080 /health)

# ── Menu bar status line ──────────────────────────────────────────────────────
if [[ "$flint_up" == "1" && $online -eq $total ]]; then
  echo "⬡ Z·AI | color=#00C853 font=Menlo-Bold size=12"
elif [[ "$flint_up" == "1" ]]; then
  echo "◑ Z·AI | color=#FF9500 font=Menlo-Bold size=12"
else
  echo "○ Z·AI offline | color=#FF3B30 font=Menlo-Bold size=12"
fi

echo "---"

# ── Header ────────────────────────────────────────────────────────────────────
echo "Zyrcon AI | font=Menlo-Bold size=14 color=#1d1d1f"
echo "Cascadia OS | font=Menlo size=11 color=#888888"
echo "---"

# ── Kernel status ─────────────────────────────────────────────────────────────
echo "KERNEL | color=#888888 font=Menlo-Bold size=11"
if [[ "$flint_up" == "1" ]]; then
  echo "⬤ Running | color=#00C853 font=Menlo size=12"
else
  echo "○ Offline | color=#FF3B30 font=Menlo size=12"
fi

# ── AI model status ───────────────────────────────────────────────────────────
echo "---"
echo "AI MODEL | color=#888888 font=Menlo-Bold size=11"
if [[ "$llama_up" == "1" ]]; then
  echo "⬤ llama.cpp running :8080 | color=#00C853 font=Menlo size=12"
else
  echo "○ Not running | color=#888888 font=Menlo size=12"
fi

# ── Actions ───────────────────────────────────────────────────────────────────
echo "---"
if [[ "$flint_up" == "1" ]]; then
  echo "■ Stop All | bash='$0' param1=stop-all terminal=false refresh=true color=#FF3B30 font=Menlo size=12"
else
  echo "▶ Start All | bash='$0' param1=start-all terminal=false refresh=true color=#00C853 font=Menlo size=12"
fi

# ── Links ─────────────────────────────────────────────────────────────────────
echo "---"
echo "⬡ PRISM Dashboard | bash='$0' param1=open-prism terminal=false color=#60A5FA font=Menlo size=12"
echo "⚙ Settings | bash='$0' param1=open-settings terminal=false color=#60A5FA font=Menlo size=12"
echo "♥ System Health | bash='$0' param1=open-health terminal=false color=#60A5FA font=Menlo size=12"
echo "---"
echo "📂 Vault | bash='$0' param1=open-vault terminal=false color=#888888 font=Menlo size=12"
