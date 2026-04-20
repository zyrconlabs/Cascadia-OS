#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Cascadia OS — AI Model Setup
# Detects hardware, recommends best option, installs llama.cpp + downloads model.
# Usage: bash setup-llm.sh [3b|7b|14b|vl]   (default: auto-recommend)
# Re-run anytime to switch models.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[cascadia]${NC} $*"; }
success() { echo -e "${GREEN}[cascadia]${NC} $*"; }
warn()    { echo -e "${YELLOW}[cascadia]${NC} $*"; }
die()     { echo -e "${RED}[cascadia] ERROR:${NC} $*" >&2; exit 1; }
bold()    { echo -e "${BOLD}$*${NC}"; }

MODEL_ARG="${1:-}"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
# Non-interactive mode: if model arg provided, skip all prompts
NON_INTERACTIVE="${MODEL_ARG:+yes}"
# Models live inside the cascadia-os install folder
MODELS_DIR="${CASCADIA_MODELS_DIR:-$INSTALL_DIR/models}"
CONFIG_PATH="$INSTALL_DIR/config.json"

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║     Cascadia OS — AI Setup               ║"
echo "  ╚══════════════════════════════════════════╝"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Hardware detection
# ══════════════════════════════════════════════════════════════════════════════
echo ""
info "Detecting your hardware..."

OS="$(uname)"
ARCH="$(uname -m)"
RAM_GB=0
GPU_TYPE="none"       # apple_silicon | nvidia | amd | intel_mac | cpu_only
GPU_CAPABLE=false
RECOMMENDED_MODE="api"
RECOMMENDED_MODEL="3b"

# ── RAM detection ─────────────────────────────────────────────────────────────
if [[ "$OS" == "Darwin" ]]; then
    RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
    RAM_GB=$(( RAM_BYTES / 1073741824 ))
elif [[ "$OS" == "Linux" ]]; then
    RAM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
    RAM_GB=$(( RAM_KB / 1048576 ))
fi

# ── GPU detection ─────────────────────────────────────────────────────────────
if [[ "$OS" == "Darwin" ]]; then
    if [[ "$ARCH" == "arm64" ]]; then
        # Apple Silicon — M1/M2/M3/M4 — unified memory IS the GPU
        # Get chip name for better display
        CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || \
               system_profiler SPHardwareDataType 2>/dev/null | grep "Chip:" | awk '{print $2, $3}' || \
               echo "Apple Silicon")
        GPU_TYPE="apple_silicon"
        GPU_CAPABLE=true
    else
        # Intel Mac — no Metal acceleration for llama.cpp
        GPU_TYPE="intel_mac"
        GPU_CAPABLE=false
        CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Intel")
    fi
elif [[ "$OS" == "Linux" ]]; then
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
        VRAM_GB=$(( VRAM / 1024 ))
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")
        GPU_TYPE="nvidia"
        GPU_CAPABLE=true
    elif command -v rocminfo &>/dev/null || [[ -f /dev/kfd ]]; then
        GPU_TYPE="amd"
        GPU_CAPABLE=true
        GPU_NAME="AMD GPU (ROCm)"
    else
        GPU_TYPE="cpu_only"
        GPU_CAPABLE=false
    fi
fi

# ── Determine recommendation ──────────────────────────────────────────────────
if [[ "$GPU_CAPABLE" == "true" ]]; then
    if [[ $RAM_GB -ge 16 ]]; then
        RECOMMENDED_MODEL="7b"
        RECOMMENDED_MODE="local"
    elif [[ $RAM_GB -ge 8 ]]; then
        RECOMMENDED_MODEL="7b"
        RECOMMENDED_MODE="local"
    elif [[ $RAM_GB -ge 4 ]]; then
        RECOMMENDED_MODEL="3b"
        RECOMMENDED_MODE="local"
    else
        RECOMMENDED_MODEL="3b"
        RECOMMENDED_MODE="api"
    fi
else
    RECOMMENDED_MODE="api"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Display hardware report + recommendation
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  Hardware Report                        │"
echo "  ├─────────────────────────────────────────┤"
printf "  │  %-40s│\n" "RAM:  ${RAM_GB} GB"

if [[ "$GPU_TYPE" == "apple_silicon" ]]; then
    printf "  │  %-40s│\n" "GPU:  ✓ Apple Silicon (Metal — full GPU)"
    printf "  │  %-40s│\n" "Chip: ${CHIP:-Apple Silicon}"
elif [[ "$GPU_TYPE" == "nvidia" ]]; then
    printf "  │  %-40s│\n" "GPU:  ✓ ${GPU_NAME} (${VRAM_GB}GB VRAM)"
elif [[ "$GPU_TYPE" == "amd" ]]; then
    printf "  │  %-40s│\n" "GPU:  ✓ ${GPU_NAME}"
elif [[ "$GPU_TYPE" == "intel_mac" ]]; then
    printf "  │  %-40s│\n" "GPU:  ✗ Intel Mac — no Metal acceleration"
    printf "  │  %-40s│\n" "CPU:  ${CHIP:-Intel}"
else
    printf "  │  %-40s│\n" "GPU:  ✗ No GPU detected (CPU only)"
fi
echo "  └─────────────────────────────────────────┘"
echo ""

# ── Recommendation message ────────────────────────────────────────────────────
if [[ "$GPU_TYPE" == "apple_silicon" ]]; then
    success "Apple Silicon detected — llama.cpp runs with full Metal GPU acceleration"
    echo "  Local mode is the best choice. Fast, private, and free."

elif [[ "$GPU_TYPE" == "nvidia" ]]; then
    success "NVIDIA GPU detected — llama.cpp runs with CUDA acceleration"
    echo "  Local mode will be fast and private."

elif [[ "$GPU_TYPE" == "amd" ]]; then
    success "AMD GPU detected — llama.cpp can use ROCm acceleration"
    echo "  Local mode should work well."

elif [[ "$GPU_TYPE" == "intel_mac" ]]; then
    warn "Intel Mac detected — llama.cpp runs on CPU only (no Metal)"
    echo ""
    echo "  This means:"
    echo "  • 3B model: ~30–60 tokens/second (usable but slow)"
    echo "  • 7B model: ~10–20 tokens/second (very slow)"
    echo "  • 14B model: not recommended"
    echo ""
    echo "  Recommendation: use a Cloud API for faster, better responses."
    echo "  Or install Ollama — it has better CPU optimizations than llama.cpp."
    echo ""

else
    warn "No GPU detected — llama.cpp will run on CPU only"
    echo ""
    echo "  This means:"
    echo "  • 3B model: ~5–15 tokens/second (slow)"
    echo "  • 7B+ models: very slow, not recommended"
    echo ""
    echo "  Strong recommendation: use a Cloud API for usable performance."
    echo ""
fi

# RAM warning if below minimum for chosen/recommended model
if [[ "$GPU_CAPABLE" == "true" && $RAM_GB -lt 4 ]]; then
    echo ""
    warn "Only ${RAM_GB}GB RAM detected — all models require at least 4GB"
    warn "Switching recommendation to Cloud API"
    RECOMMENDED_MODE="api"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Choose mode
# ══════════════════════════════════════════════════════════════════════════════
echo ""
if [[ "$RECOMMENDED_MODE" == "local" ]]; then
    echo "  How should Cascadia run AI?"
    echo ""
    echo "  [1] Local  ← RECOMMENDED — private, free, fast on your hardware"
    echo "  [2] API    — OpenAI / Anthropic / Groq (requires API key)"
    echo "  [3] Ollama — use a model already in Ollama"
    echo "  [4] Skip   — configure later"
else
    echo "  How should Cascadia run AI?"
    echo ""
    echo "  [1] Local  — runs on your hardware (may be slow without GPU)"
    echo "  [2] API    ← RECOMMENDED — fast, works on any hardware"
    echo "  [3] Ollama — use a model already in Ollama"
    echo "  [4] Skip   — configure later"
fi
echo ""
if [[ -n "$NON_INTERACTIVE" ]]; then MODE_CHOICE=1
else read -r -p "  Choice [1-4, default: $([ \"$RECOMMENDED_MODE\" == \"local\" ] && echo 1 || echo 2)]: " MODE_CHOICE
fi
MODE_CHOICE="${MODE_CHOICE:-$([ "$RECOMMENDED_MODE" == "local" ] && echo 1 || echo 2)}"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Route to chosen setup path
# ══════════════════════════════════════════════════════════════════════════════

if [[ "$MODE_CHOICE" == "1" ]]; then
    # ── LOCAL MODE ────────────────────────────────────────────────────────────
    echo ""

    # Show size options filtered by RAM
    echo "  Choose model size:"
    echo ""
    if [[ $RAM_GB -ge 16 ]]; then
        echo "  [1] 3B  — 2.0 GB · Fast           (your ${RAM_GB}GB RAM: ✓ excellent)"
        echo "  [2] 7B  — 4.7 GB · Balanced       (your ${RAM_GB}GB RAM: ✓ excellent) ← recommended"
        echo "  [3] 14B — 8.9 GB · Best quality   (your ${RAM_GB}GB RAM: ✓ good)"
        DEFAULT_SIZE="2"
    elif [[ $RAM_GB -ge 8 ]]; then
        echo "  [1] 3B  — 2.0 GB · Fast           (your ${RAM_GB}GB RAM: ✓ great)"
        echo "  [2] 7B  — 4.7 GB · Balanced       (your ${RAM_GB}GB RAM: ✓ good) ← recommended"
        echo "  [3] 14B — 8.9 GB · Best quality   (your ${RAM_GB}GB RAM: ⚠ tight)"
        DEFAULT_SIZE="2"
    elif [[ $RAM_GB -ge 4 ]]; then
        echo "  [1] 3B  — 2.0 GB · Fast           (your ${RAM_GB}GB RAM: ✓ fine) ← recommended"
        echo "  [2] 7B  — 4.7 GB · Balanced       (your ${RAM_GB}GB RAM: ⚠ tight)"
        echo "  [3] 14B — 8.9 GB · Best quality   (your ${RAM_GB}GB RAM: ✗ not recommended)"
        DEFAULT_SIZE="1"
    else
        echo "  [1] 3B  — 2.0 GB · Fast           (your ${RAM_GB}GB RAM: ⚠ minimum)"
        echo "  [2] 7B  — 4.7 GB · Balanced       (your ${RAM_GB}GB RAM: ✗ not recommended)"
        echo "  [3] 14B — 8.9 GB · Best quality   (your ${RAM_GB}GB RAM: ✗ not recommended)"
        DEFAULT_SIZE="1"
    fi
    echo ""

    if [[ -n "$MODEL_ARG" ]]; then
        MODEL_SIZE="$MODEL_ARG"
        info "Using specified model: $MODEL_SIZE"
    else
        read -r -p "  Size [1-3, default $DEFAULT_SIZE]: " SIZE_CHOICE
        SIZE_CHOICE="${SIZE_CHOICE:-$DEFAULT_SIZE}"
        case "$SIZE_CHOICE" in
            2) MODEL_SIZE="7b"  ;;
            3) MODEL_SIZE="14b" ;;
            *) MODEL_SIZE="3b"  ;;
        esac
    fi

    # RAM hard block — prevent OOM crashes
    if [[ "$MODEL_SIZE" == "7b" && $RAM_GB -lt 6 ]]; then
        warn "7B requires 8GB RAM — you have ${RAM_GB}GB"
        if [[ -n "$NON_INTERACTIVE" ]]; then MODEL_SIZE="3b"
    else read -r -p "  Switch to 3B instead? [Y/n]: " SWITCH
        [[ "${SWITCH:-Y}" =~ ^[Yy]$ ]] && MODEL_SIZE="3b"
    fi
    elif [[ "$MODEL_SIZE" == "14b" && $RAM_GB -lt 12 ]]; then
        warn "14B requires 16GB RAM — you have ${RAM_GB}GB"
        if [[ -n "$NON_INTERACTIVE" ]]; then MODEL_SIZE="7b"
    else read -r -p "  Switch to 7B instead? [Y/n]: " SWITCH
        [[ "${SWITCH:-Y}" =~ ^[Yy]$ ]] && MODEL_SIZE="7b"
    fi
    fi

    # Set model details
    case "$MODEL_SIZE" in
        3b)  MODEL_FILE="qwen2.5-3b-instruct-q4_k_m.gguf"
             MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf" ;;
        7b)  MODEL_FILE="qwen2.5-7b-instruct-q4_k_m.gguf"
             MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf" ;;
        14b) MODEL_FILE="Qwen2.5-14B-Instruct-Q4_K_M.gguf"
             MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-GGUF/resolve/main/Qwen2.5-14B-Instruct-Q4_K_M.gguf" ;;
        vl)  MODEL_FILE="qwen2.5-vl-7b-instruct-q4_k_m.gguf"
             MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/qwen2.5-vl-7b-instruct-q4_k_m.gguf" ;;
    esac
    MODEL_PATH="$MODELS_DIR/$MODEL_FILE"

    # ── Install llama.cpp ──────────────────────────────────────────────────────
    echo ""
    LLAMA_BIN=""
    for candidate in \
        "$(command -v llama-server 2>/dev/null || true)" \
        "/opt/homebrew/bin/llama-server" \
        "/usr/local/bin/llama-server" \
        "$HOME/.local/bin/llama-server" \
        "$HOME/llama.cpp/build/bin/llama-server"; do
        if [[ -n "$candidate" && -f "$candidate" ]]; then
            LLAMA_BIN="$candidate"; break
        fi
    done

    if [[ -n "$LLAMA_BIN" ]]; then
        success "llama.cpp found: $LLAMA_BIN"
    elif [[ "$OS" == "Darwin" ]]; then
        info "Installing llama.cpp via Homebrew..."
        brew install llama.cpp
        for candidate in "/opt/homebrew/bin/llama-server" "/usr/local/bin/llama-server"; do
            [[ -f "$candidate" ]] && LLAMA_BIN="$candidate" && break
        done
        [[ -n "$LLAMA_BIN" ]] || LLAMA_BIN="$(command -v llama-server 2>/dev/null || true)"
        [[ -n "$LLAMA_BIN" ]] || die "llama-server not found after install. Try: brew link llama.cpp"
        success "llama.cpp installed: $LLAMA_BIN"
    elif [[ "$OS" == "Linux" ]]; then
        info "Building llama.cpp from source..."
        command -v cmake &>/dev/null || die "cmake required: sudo apt install cmake build-essential"
        [[ ! -d "$HOME/llama.cpp" ]] && git clone --depth 1 https://github.com/ggml-org/llama.cpp "$HOME/llama.cpp"
        cmake -S "$HOME/llama.cpp" -B "$HOME/llama.cpp/build" -DCMAKE_BUILD_TYPE=Release
        cmake --build "$HOME/llama.cpp/build" --target llama-server -j"$(nproc)"
        LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"
        success "llama.cpp built: $LLAMA_BIN"
    fi

    # ── Download model ─────────────────────────────────────────────────────────
    mkdir -p "$MODELS_DIR"
    if [[ -f "$MODEL_PATH" ]]; then
        success "Model already exists: $MODEL_PATH"
    else
        # Check other locations first
        for search_dir in "$HOME/Ai Models" "$HOME/ai models" "$HOME/models" "$HOME/cascadia-os/models"; do
            if [[ -f "$search_dir/$MODEL_FILE" ]]; then
                info "Found model at: $search_dir/$MODEL_FILE"
                if [[ -n "$NON_INTERACTIVE" ]]; then USE_EXISTING="Y"
                else read -r -p "  Use this existing file? [Y/n]: " USE_EXISTING
                fi
                if [[ "${USE_EXISTING:-Y}" =~ ^[Yy]$ ]]; then
                    MODEL_PATH="$search_dir/$MODEL_FILE"
                    MODELS_DIR="$search_dir"
                    break
                fi
            fi
        done

        if [[ ! -f "$MODEL_PATH" ]]; then
            echo ""
            info "Downloading Qwen 2.5 $(echo $MODEL_SIZE | tr '[:lower:]' '[:upper:]')..."
            info "Destination: $MODEL_PATH"
            warn "Large file — do not close this window. Download resumes if interrupted."
            echo ""
            if command -v curl &>/dev/null; then
                curl -L --progress-bar --continue-at - \
                    -H "User-Agent: Cascadia-OS/0.43" \
                    -o "$MODEL_PATH" "$MODEL_URL" \
                    || { rm -f "$MODEL_PATH"; die "Download failed. Re-run to resume."; }
            else
                wget -c --show-progress -O "$MODEL_PATH" "$MODEL_URL" \
                    || { rm -f "$MODEL_PATH"; die "Download failed."; }
            fi
            success "Downloaded: $(du -sh "$MODEL_PATH" | cut -f1)"
        fi
    fi

    # ── GPU layers flag ────────────────────────────────────────────────────────
    if [[ "$GPU_TYPE" == "apple_silicon" || "$GPU_TYPE" == "nvidia" || "$GPU_TYPE" == "amd" ]]; then
        N_GPU_LAYERS=99
    else
        N_GPU_LAYERS=0
        warn "No GPU acceleration — running on CPU. Responses will be slower."
    fi

    # ── Smoke test ─────────────────────────────────────────────────────────────
    info "Testing model (takes up to 20 seconds)..."
    "$LLAMA_BIN" --model "$MODEL_PATH" --host 127.0.0.1 --port 18765 \
        --ctx-size 256 --n-gpu-layers "$N_GPU_LAYERS" --log-disable \
        > /tmp/cascadia-llm-test.log 2>&1 &
    TEST_PID=$!
    TEST_OK=false
    for i in $(seq 1 20); do
        curl -sf http://127.0.0.1:18765/health > /dev/null 2>&1 && TEST_OK=true && break
        sleep 1
    done
    kill "$TEST_PID" 2>/dev/null; wait "$TEST_PID" 2>/dev/null || true

    if [[ "$TEST_OK" == "true" ]]; then
        success "Model test passed"
    else
        warn "Model test timed out — check /tmp/cascadia-llm-test.log if issues arise"
    fi

    # ── Write config ───────────────────────────────────────────────────────────
    [[ -f "$CONFIG_PATH" ]] && python3 - <<PYEOF
import json
c = json.load(open("$CONFIG_PATH"))
c['llm'] = c.get('llm', {})
c['llm'].update({
    'provider': 'llamacpp', 'model': '$MODEL_FILE',
    'base_url': 'http://127.0.0.1:8080',
    'models_dir': '$MODELS_DIR', 'llama_bin': '$LLAMA_BIN',
    'n_gpu_layers': $N_GPU_LAYERS, 'ctx_size': 4096,
    'configured': True, 'active_model_id': 'qwen2.5-$MODEL_SIZE'
})
json.dump(c, open("$CONFIG_PATH", 'w'), indent=2)
print("  config.json updated")
PYEOF

    echo ""
    success "═══════════════════════════════════════════"
    success " Local AI setup complete"
    success "═══════════════════════════════════════════"
    echo ""
    echo "  Model:       $MODEL_FILE"
    echo "  Location:    $MODEL_PATH"
    echo "  Engine:      $LLAMA_BIN"
    if [[ "$GPU_TYPE" == "apple_silicon" ]]; then
        echo "  Acceleration: Metal GPU (full Apple Silicon)"
    elif [[ "$GPU_TYPE" == "nvidia" ]]; then
        echo "  Acceleration: CUDA GPU"
    else
        echo "  Acceleration: CPU only (no GPU detected)"
    fi
    echo ""

    # ── Auto-start llama.cpp ───────────────────────────────────────────────────
    info "Starting AI inference server..."
    pkill -f "llama-server" 2>/dev/null || true
    sleep 1
    "$LLAMA_BIN" \
        --model "$MODEL_PATH" \
        --host 127.0.0.1 --port 8080 \
        --ctx-size 4096 \
        --n-gpu-layers "$N_GPU_LAYERS" \
        >> "$INSTALL_DIR/data/logs/llamacpp.log" 2>&1 &

    # Wait up to 20s for it to be ready
    LLM_READY=false
    for i in $(seq 1 20); do
        if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
            LLM_READY=true
            break
        fi
        sleep 1
    done

    if [[ "$LLM_READY" == "true" ]]; then
        success "AI inference server running on http://127.0.0.1:8080"
    else
        warn "AI server starting in background — may take a few more seconds"
        warn "Check: curl http://127.0.0.1:8080/health"
    fi

elif [[ "$MODE_CHOICE" == "2" ]]; then
    # ── CLOUD API MODE ────────────────────────────────────────────────────────
    echo ""
    echo "  Cloud API providers:"
    echo "  [1] OpenAI     — gpt-4o-mini (fast, cheap, capable)"
    echo "  [2] Anthropic  — claude-haiku (fast, private-feeling)"
    echo "  [3] Groq       — llama-3.3-70b (very fast, free tier available)"
    echo ""
    read -r -p "  Provider [1-3, default 1]: " PROV_CHOICE
    case "${PROV_CHOICE:-1}" in
        2) PROVIDER="anthropic"; DEFAULT_MODEL="claude-haiku-4-5-20251001" ;;
        3) PROVIDER="groq";      DEFAULT_MODEL="llama-3.3-70b-versatile" ;;
        *) PROVIDER="openai";    DEFAULT_MODEL="gpt-4o-mini" ;;
    esac
    echo ""
    echo "  Get your API key:"
    [[ "$PROVIDER" == "openai" ]]    && echo "  → https://platform.openai.com/api-keys"
    [[ "$PROVIDER" == "anthropic" ]] && echo "  → https://console.anthropic.com/keys"
    [[ "$PROVIDER" == "groq" ]]      && echo "  → https://console.groq.com/keys"
    echo ""
    read -r -p "  API key: " API_KEY
    [[ -z "$API_KEY" ]] && die "API key required"

    [[ -f "$CONFIG_PATH" ]] && python3 - <<PYEOF
import json
c = json.load(open("$CONFIG_PATH"))
c['llm'] = {'provider': '$PROVIDER', 'api_key': '$API_KEY',
             'model': '$DEFAULT_MODEL', 'configured': True,
             'base_url': None}
json.dump(c, open("$CONFIG_PATH", 'w'), indent=2)
print("  config.json updated")
PYEOF

    success "Cloud API configured: $PROVIDER / $DEFAULT_MODEL"

elif [[ "$MODE_CHOICE" == "3" ]]; then
    # ── OLLAMA MODE ───────────────────────────────────────────────────────────
    OLLAMA_MODELS=$(python3 -c "
from urllib import request as ur; import json
try:
    with ur.urlopen('http://localhost:11434/api/tags', timeout=2) as r:
        print('\n'.join(m['name'] for m in json.loads(r.read()).get('models',[])))
except: pass
" 2>/dev/null)
    if [[ -z "$OLLAMA_MODELS" ]]; then
        warn "Ollama not running at localhost:11434"
        echo "  Install: https://ollama.com  then: ollama pull qwen2.5:3b"
        echo "  Then re-run: bash setup-llm.sh"
    else
        echo ""
        info "Available Ollama models:"
        echo "$OLLAMA_MODELS" | nl -ba -w2
        echo ""
        read -r -p "  Model name (copy from list above): " OLLAMA_MODEL
        [[ -z "$OLLAMA_MODEL" ]] && die "Model name required"
        [[ -f "$CONFIG_PATH" ]] && python3 - <<PYEOF
import json
c = json.load(open("$CONFIG_PATH"))
c['llm'] = {'provider': 'ollama', 'model': '$OLLAMA_MODEL',
             'base_url': 'http://localhost:11434', 'configured': True}
json.dump(c, open("$CONFIG_PATH", 'w'), indent=2)
print("  config.json updated")
PYEOF
        success "Ollama configured: $OLLAMA_MODEL"
    fi

else
    warn "Skipped — run later: bash setup-llm.sh"
    exit 0
fi

echo "  Start Cascadia: bash start.sh (if not already running)"
echo ""
